from typing import Any, List, Union

import pgactivity
import pgactivity.models
from django.apps import apps
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, models
from typing_extensions import Self

from pglock import config, utils


class PGTableQueryCompiler(pgactivity.models.PGTableQueryCompiler):
    def get_pid_clause(self) -> str:
        if isinstance(self.query.pids, str):
            return f"AND pid = ANY({self.query.pids})"
        else:
            return super().get_pid_clause()

    def get_ctes(self) -> List[str]:
        ctes = super().get_ctes()

        if self.query.relations:
            models = [
                apps.get_model(model) if isinstance(model, str) else model
                for model in self.query.relations
            ]
            rel_name_clause = (
                "AND pg_class.relname IN ("
                + ", ".join(f"'{model._meta.db_table}'" for model in models)
                + ")"
            )
        else:
            rel_name_clause = ""

        with self.connection.cursor() as cursor:  # pragma: no cover
            if utils.pg_maj_version(cursor) >= 14:
                # Waitstart is available in pg 14 and up
                wait_start_clause = "waitstart AS wait_start, NOW() - waitstart AS wait_duration"
            else:
                wait_start_clause = "NULL AS wait_start, NULL AS wait_duration"

        lock_cte = rf"""
            _pglock_lock_cte AS (
                SELECT
                    pid AS id,
                    pid AS activity_id,
                    TRIM(
                        BOTH '_' FROM UPPER(
                            REGEXP_REPLACE(
                                REPLACE(mode, 'Lock', ''),
                                '([A-Z])','_\1',
                                'g'
                            )
                        )
                    ) AS mode,
                    CASE pg_class.relkind
                        WHEN 'r' THEN 'TABLE'::text
                        WHEN 'i' THEN 'INDEX'::text
                        WHEN 'S' THEN 'SEQUENCE'::text
                        WHEN 't' THEN 'TOAST'::text
                        WHEN 'v' THEN 'VIEW'::text
                        WHEN 'm' THEN 'MATERIALIZED_VIEW'::text
                        WHEN 'c' THEN 'COMPOSITE_TYPE'::text
                        WHEN 'f' THEN 'FOREIGN_TABLE'::text
                        WHEN 'p' THEN 'PARTITIONED_TABLE'::text
                        WHEN 'I' THEN 'PARTITIONED_INDEX'::text
                        ELSE pg_class.relkind::text
                    END AS rel_kind,
                    UPPER(locktype) as type,
                    pg_class.relname as rel_name,
                    granted,
                    {wait_start_clause}
                FROM pg_locks
                JOIN pg_database ON pg_database.oid = pg_locks.database
                JOIN pg_class ON pg_class.oid = pg_locks.relation
                WHERE
                    pg_database.datname = '{settings.DATABASES[self.using]["NAME"]}'
                    {self.get_pid_clause()}
                    {rel_name_clause}
            )
        """

        blocked_lock_cte = """
            _pglock_blocked_lock_cte AS (
                SELECT
                    *,
                    UNNEST(pg_blocking_pids(activity_id)) AS blocking_activity_id
                FROM _pglock_lock_cte
            )
        """

        ctes.extend([lock_cte, blocked_lock_cte])
        return ctes


class PGTableQuery(pgactivity.models.PGTableQuery):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.relations = None

    def get_compiler(self, *args: Any, **kwargs: Any) -> PGTableQueryCompiler:
        compiler = super().get_compiler(*args, **kwargs)
        compiler.__class__ = PGTableQueryCompiler
        return compiler

    def __chain(self, *args: Any, **kwargs: Any) -> Self:
        clone = super().__chain(*args, **kwargs)
        clone.relations = self.relations
        return clone


class PGTableQuerySet(pgactivity.models.PGTableQuerySet):
    def __init__(self, model=None, query=None, using=None, hints=None):
        if query is None:
            query = PGTableQuery(model)

        super().__init__(model, query, using, hints)


class PGLockQuerySet(PGTableQuerySet):
    """The Queryset for the `PGLock` model."""

    def on(self, *relations: Union[models.Model, str]) -> Self:
        """Set the relations to filter against.

        Currently model names or classes are accepted.
        """
        qs = self._clone()
        qs.query.relations = relations
        return qs

    def cancel_activity(self) -> List[int]:
        """Cancel all PIDs in the `activity` field of the filtered queryset"""
        pids = list(self.values_list("activity_id", flat=True).distinct())
        return pgactivity.cancel(*pids, using=self.db)

    def terminate_activity(self) -> List[int]:
        """Terminate all PIDs in the `activity` field of the filtered queryset"""
        pids = list(self.values_list("activity_id", flat=True).distinct())
        return pgactivity.terminate(*pids, using=self.db)

    def config(self, name: str, **overrides: Any) -> Self:
        """
        Use a config name from `settings.PGLOCK_CONFIGS` to apply filters.
        Config overrides can be provided in the keyword arguments.

        Args:
            name: Name of the config. Must be a key from `settings.PGLOCK_CONFIGS`.
            **overrides: Any overrides to apply to the final config dictionary.

        Returns:
            The configuration
        """
        qset = self

        cfg = config.get(name, **overrides)

        qset = qset.using(cfg.get("database", DEFAULT_DB_ALIAS))
        qset = qset.pid(*cfg.get("pids", []))
        qset = qset.on(*cfg.get("on", []))

        for f in cfg.get("filters", []) or []:
            key, val = f.split("=", 1)
            qset = qset.filter(**{key: val})

        return qset


class BasePGLock(pgactivity.models.PGTable):
    type = models.CharField(max_length=64)
    activity = models.ForeignKey("pgactivity.PGActivity", on_delete=models.DO_NOTHING)
    mode = models.CharField(max_length=64)
    granted = models.BooleanField()
    wait_start = models.DateTimeField(null=True)
    wait_duration = models.DurationField(null=True)
    rel_kind = models.CharField(max_length=32)
    rel_name = models.CharField(max_length=256)

    objects = PGLockQuerySet.as_manager()

    class Meta:
        abstract = True


class PGLock(BasePGLock):
    """
    Wraps Postgres's `pg_locks` view.

    Attributes:
        type (models.CharField): The type of lock. One of
            RELATION, EXTEND, FROZENID, PAGE, TUPLE, TRANSACTIONID, VIRTUALXID,
            SPECTOKEN, OBJECT, USERLOCK, or ADVISORY.
        activity (models.ForeignKey[pgactivity.PGActivity]): The activity
            from `pg_stats_activity` this lock references.
        mode (models.CharField): The mode of lock. One of
            ACCESS_SHARE, ROW_SHARE, ROW_EXCLUSIVE, SHARE_UPDATE_EXCLUSIVE,
            SHARE, SHARE_ROW_EXCLUSIVE, EXCLUSIVE, ACCESS_EXCLUSIVE.
        granted (models.BooleanField): `True` if the lock has been granted,
            `False` if the lock is blocked by another.
        wait_start (models.DateTimeField): When the lock started waiting. Only
            available in Postgres 14 and up.
        wait_duration (models.DurationField): How long the lock has been blocked.
            Only available in Postgres 14 and up
        rel_kind (models.CharField): The kind of relation being locked. One of
            TABLE, INDEX, SEQUENCE, TOAST, VIEW, MATERIALIZED_VIEW,
            COMPOSITE_TYPE, FOREIGN_TABLE, PARTITIONED_TABLE, or
            PARTITIONED_INDEX.
        rel_name (models.CharField): The name of the relation. E.g. the table name
            when `rel_kind=TABLE`.
    """

    class Meta:
        managed = False
        db_table = "_pglock_lock_cte"
        default_manager_name = "no_objects"


class BlockedPGLockQuerySet(PGLockQuerySet):
    """The Queryset for the `BlockedPGLock` model. Inherits `PGLockQuerySet`"""

    def cancel_blocking_activity(self) -> List[int]:
        """Cancel all PIDs in the `blocking_activity` field of the filtered queryset"""
        pids = list(self.values_list("blocking_activity_id", flat=True).distinct())
        return pgactivity.cancel(*pids, using=self.db)

    def terminate_blocking_activity(self) -> List[int]:
        """Terminate all PIDs in the `blocking_activity` field of the filtered queryset"""
        pids = list(self.values_list("blocking_activity_id", flat=True).distinct())
        return pgactivity.terminate(*pids, using=self.db)

    def pid(self, *pids: int) -> Self:
        qs = self._clone()

        if pids:
            qs.query.pids = " || ".join(f"{pid} || pg_blocking_pids({pid})" for pid in pids)
        else:  # pragma: no cover
            qs.query.pids = pids

        return qs


class BlockedPGLock(BasePGLock):
    """Models a blocked lock.

    Uses Postgres's `pg_blocking_pids` function to unnest and
    denormalize any blocking activity for a lock, returning both
    the activity and blocking activity as a row.

    Attributes:
        blocking_activity (models.ForeignKey[pgactivity.PGActivity]): The
            activity that's blocking the lock.
    """

    blocking_activity = models.ForeignKey(
        "pgactivity.PGActivity", on_delete=models.DO_NOTHING, related_name="+"
    )

    objects = BlockedPGLockQuerySet.as_manager()

    class Meta:
        managed = False
        db_table = "_pglock_blocked_lock_cte"
        default_manager_name = "no_objects"
