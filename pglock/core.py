from __future__ import annotations

import contextlib
import datetime as dt
import functools
import hashlib
import inspect
import threading
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any, Final, Tuple, TypeVar, Union

import pgactivity
from django.apps import apps
from django.db import DEFAULT_DB_ALIAS, connections, models, transaction
from django.db.utils import OperationalError
from typing_extensions import ParamSpec

from pglock import utils

if TYPE_CHECKING:
    from types import TracebackType

    from pglock.models import BlockedPGLockQuerySet


if utils.psycopg_maj_version == 2:
    import psycopg2.extensions
elif utils.psycopg_maj_version == 3:
    import psycopg.pq
else:
    raise AssertionError


_R = TypeVar("_R")
_P = ParamSpec("_P")

# Lock levels
ACCESS_SHARE: Final = "ACCESS SHARE"
ROW_SHARE: Final = "ROW SHARE"
ROW_EXCLUSIVE: Final = "ROW EXCLUSIVE"
SHARE_UPDATE_EXCLUSIVE: Final = "SHARE UPDATE EXCLUSIVE"
SHARE: Final = "SHARE"
SHARE_ROW_EXCLUSIVE: Final = "SHARE ROW EXCLUSIVE"
EXCLUSIVE: Final = "EXCLUSIVE"
ACCESS_EXCLUSIVE: Final = "ACCESS EXCLUSIVE"


_timeout = threading.local()


class _Unset: ...


_unset = _Unset()


class SideEffect:
    """The base class for side effects"""


class Return(SideEffect):
    """
    The side effect for returning the lock status when
    using `pglock.advisory` or `pglock.model`.
    """


class Raise(SideEffect):
    """
    The side effect for raising an error on lock acquisition
    failure when using `pglock.advisory` or `pglock.model`.
    """


class Skip(SideEffect):
    """
    The side effect for skipping wrapped code on lock acquisition error
    when using `pglock.advisory` as a decorator.
    """


def _cast_timeout(timeout):
    if timeout is not None and timeout is not _unset:
        if isinstance(timeout, (int, float)):
            timeout = dt.timedelta(seconds=timeout)

        if not isinstance(timeout, dt.timedelta):
            raise TypeError("Must supply int, float, or timedelta to pglock.timeout")

        if timeout < dt.timedelta(milliseconds=1):
            timeout = dt.timedelta()

    return timeout


def _is_transaction_errored(cursor):
    """
    True if the current transaction is in an errored state
    """
    if utils.psycopg_maj_version == 2:
        return (
            cursor.connection.get_transaction_status()
            == psycopg2.extensions.TRANSACTION_STATUS_INERROR
        )
    elif utils.psycopg_maj_version == 3:
        return cursor.connection.info.transaction_status == psycopg.pq.TransactionStatus.INERROR
    else:
        raise AssertionError


@contextlib.contextmanager
def lock_timeout(
    timeout: dt.timedelta | int | float | _Unset | None = _unset,
    *,
    using: str = DEFAULT_DB_ALIAS,
    **timedelta_kwargs: int,
) -> Generator[None]:
    """Set the lock timeout as a decorator or context manager.

    A value of `None` will set an infinite lock timeout.
    A value of less than a millisecond is not permitted.

    Nested invocations will successfully apply and rollback the timeout to
    the previous value.

    Args:
        timeout: The number of seconds as an integer or float. Use a timedelta object to
            precisely specify the timeout interval. Use `None` for an infinite timeout.
        using: The database to use.
        **timedelta_kwargs: Keyword arguments to directly supply to
            datetime.timedelta to create an interval. E.g.
            `pglock.timeout(seconds=1, milliseconds=100)`
            will create a timeout of 1100 milliseconds.

    Raises:
        django.db.utils.OperationalError: When a timeout occurs
        TypeError: When the timeout interval is an incorrect type
    """
    if timedelta_kwargs:
        timeout = dt.timedelta(**timedelta_kwargs)
    elif timeout is _unset:
        raise ValueError("Must supply a value to pglock.timeout")

    if timeout is not None:
        timeout = _cast_timeout(timeout)

        if not timeout:
            raise ValueError(
                "Must supply value greater than a millisecond to pglock.timeout or use `None` to"
                " reset the timeout."
            )
    else:
        timeout = dt.timedelta()

    if not hasattr(_timeout, "value"):
        _timeout.value = None

    old_timeout = _timeout.value
    _timeout.value = int(timeout.total_seconds() * 1000)

    try:
        with connections[using].cursor() as cursor:
            cursor.execute(f"SELECT set_config('lock_timeout', '{_timeout.value}', false)")
            yield
    finally:
        _timeout.value = old_timeout

        with connections[using].cursor() as cursor:
            if not _is_transaction_errored(cursor):
                if _timeout.value is None:
                    cursor.execute("SELECT set_config('lock_timeout', NULL, false)")
                else:
                    cursor.execute(f"SELECT set_config('lock_timeout', '{_timeout.value}', false)")


def _cast_lock_id(lock_id):
    """Cast a lock ID into the appropriate type"""
    if isinstance(lock_id, str):
        return int.from_bytes(
            hashlib.sha256(lock_id.encode("utf-8")).digest()[:8], "little", signed=True
        )
    elif isinstance(lock_id, int):
        return lock_id
    else:
        raise TypeError(f'Lock ID "{lock_id}" is not a string or int')


def advisory_id(lock_id: Union[str, int]) -> Tuple[int, int]:
    """
    Given a lock ID, return the (classid, objid) tuple that Postgres uses
    for the advisory lock in the pg_locks table,

    Args:
        lock_id: The lock ID

    Returns:
        The (classid, objid) tuple
    """
    lock_id = _cast_lock_id(lock_id)
    return lock_id >> 32, lock_id & 0xFFFFFFFF


class advisory(contextlib.ContextDecorator):
    """Obtain an advisory lock.

    When using the default side effect, returns `True` if the lock was acquired or `False` if not.

    Consult the
    `Postgres docs <https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS>`__
    for more information on shared and transactional locks.

    Args:
        lock_id (Union[str, int], default=None): The ID of the lock. When
            using the decorator, it defaults to the full module path and
            function name of the wrapped function. It must be supplied to
            the context manager or function calls.
        shared (bool, default=False): When `True`, creates a shared lock.
        xact (bool, default=False): When `True`, creates a transactional-level lock.
        using (str, default="default"): The database to use.
        timeout (Union[int, float, datetime.timedelta, None]): Set a timeout when waiting
            for the lock. This timeout only applies to the lock acquisition statement and not the
            wrapped code. If 0, `pg_try_advisory_lock` will be used to return immediately. If
            `None`, an infinite timeout will be used. When
            using a timeout, the acquisition status will be returned when running as a context
            manager. Use the `side_effect` argument to change the runtime behavior.
        side_effect (type[SideEffect] | SideEffect | None): Adjust the runtime behavior when using a timeout.
            `pglock.Return` will return the acquisition status when using the context manager.
            `pglock.Raise` will raise a `django.db.utils.OperationalError` if the lock cannot
            be acquired or a timeout happens. `pglock.Skip` will skip decoratored code if the
            lock cannot be acquired. Defaults to `pglock.Return` when used as a context manager
            or `pglock.Raise` when used as a decorator.

    Raises:
        django.db.utils.OperationalError: When a lock cannot be acquired or a timeout happens
            when using `side_effect=pglock.Raise`.
        ValueError: If an invalid `side_effect` is provided or no lock ID is supplied for
            the context manager.
        TypeError: If the lock ID is not a string or int.
    """  # noqa

    def __init__(
        self,
        lock_id: int | str | None = None,
        *,
        shared: bool = False,
        xact: bool = False,
        using: str = DEFAULT_DB_ALIAS,
        timeout: float | dt.timedelta | _Unset | None = _unset,
        side_effect: type[SideEffect] | SideEffect | None = None,
    ) -> None:
        """Acquire an advisory lock"""
        self.lock_id = lock_id
        self.using = using
        self.side_effect = side_effect
        self.shared = shared
        self.xact = xact
        self.timeout = _cast_timeout(timeout)

        # Use pg_try_advisory.. when a timeout of 0 has been applied.
        self.nowait = isinstance(self.timeout, dt.timedelta) and not self.timeout

        # "_func" will be set if a function is wrapped
        self._func = None

    @property
    def int_lock_id(self) -> int:
        return _cast_lock_id(self.lock_id)

    def in_transaction(self) -> bool:
        return connections[self.using].in_atomic_block

    def __call__(self, func: Callable[_P, _R]) -> Callable[_P, _R | None]:  # type: ignore[override]
        self._func = func

        @functools.wraps(func)
        def inner(*args: _P.args, **kwargs: _P.kwargs) -> _R | None:
            with self._recreate_cm():
                if self._acquired or self.side_effect != Skip:
                    return func(*args, **kwargs)

        return inner

    def _process_runtime_parameters(self) -> None:
        """
        Instantiate parameters such as the lock ID and side effect after
        we know if we are running as a context manager or decorator
        """
        if not self.lock_id and self._func:
            module = inspect.getmodule(self._func)
            self.lock_id = f"{module.__name__}.{self._func.__name__}"

        if self.side_effect is None:
            self.side_effect = Raise if self._func else Return
        else:
            self.side_effect = (
                self.side_effect.__class__
                if not inspect.isclass(self.side_effect)
                else self.side_effect
            )

        if not self.lock_id:
            raise ValueError("Must supply a lock ID as the first argument.")

        if self.side_effect not in (Return, Raise, Skip):
            raise ValueError(
                "side_effect must be one of pglock.Return, pglock.Raise, or pglock.Skip"
            )

        if not self._func and self.side_effect == Skip:
            raise ValueError(
                "Cannot use pglock.Skip in a context manager."
                " Use the @pglock.advisory decorator instead."
            )
        elif self._func and self.side_effect == Return:
            raise ValueError(
                "Cannot use pglock.Return in a @pglock.advisory() decorator."
                " Use it as a context manager instead."
            )

    def acquire(self) -> bool:
        self._process_runtime_parameters()

        if self.xact and not self.in_transaction():
            raise RuntimeError("Must be in a transaction to use xact=True.")

        acquire_sql = (
            f'pg{"_try" if self.nowait else ""}_advisory'
            f'{"_xact" if self.xact else ""}_lock'
            f'{"_shared" if self.shared else ""}'
        )
        sql = f"SELECT {acquire_sql}({self.int_lock_id})"

        with connections[self.using].cursor() as cursor:
            try:
                with contextlib.ExitStack() as stack:
                    if self.timeout is not _unset and not self.nowait:
                        stack.enter_context(lock_timeout(self.timeout, using=self.using))

                        if self.side_effect != Raise and self.in_transaction():
                            # If returning True/False, create a savepoint so that
                            # the transaction isn't in an errored state when returning.
                            stack.enter_context(transaction.atomic(using=self.using))

                    cursor.execute(sql)
                    acquired = cursor.fetchone()[0] if self.nowait else True
            except OperationalError:
                # This block only happens when the lock times out
                if self.side_effect != Raise:
                    acquired = False
                else:
                    raise

        if not acquired and self.side_effect == Raise:
            raise OperationalError(f'Could not acquire lock "{self.lock_id}"')

        return acquired

    def release(self) -> None:
        if self.xact:
            raise RuntimeError("Advisory locks with xact=True cannot be manually released.")

        with connections[self.using].cursor() as cursor:
            release_sql = f'pg_advisory_unlock{"_shared" if self.shared else ""}'
            cursor.execute(f"SELECT {release_sql}({self.int_lock_id})")

    def __enter__(self) -> bool:
        self._transaction_ctx = contextlib.ExitStack()
        if self.xact:
            if self.in_transaction():
                raise RuntimeError(
                    "Advisory locks with xact=True cannot run inside a transaction."
                    " Use the functional interface, i.e. pglock.advisory(...).acquire()"
                )

            # Transactional locks always create a durable transaction
            self._transaction_ctx.enter_context(transaction.atomic(using=self.using, durable=True))

        self._transaction_ctx.__enter__()

        self._acquired = self.acquire()

        self._savepoint_ctx = contextlib.ExitStack()
        if self._acquired and not self.xact and self.in_transaction():
            # Create a savepoint so that we can successfully release
            # the lock if the transaction errors
            self._savepoint_ctx.enter_context(transaction.atomic(using=self.using))

        self._savepoint_ctx.__enter__()

        return self._acquired

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._savepoint_ctx.__exit__(exc_type, exc_value, traceback)
        self._transaction_ctx.__exit__(exc_type, exc_value, traceback)

        if self._acquired and not self.xact:
            self.release()


def model(
    *models: str | type[models.Model] | models.Model,
    mode: str = ACCESS_EXCLUSIVE,
    using: str = DEFAULT_DB_ALIAS,
    timeout: int | float | dt.timedelta | _Unset | None = _unset,
    side_effect: type[SideEffect] | SideEffect = Return,
) -> bool:
    """Lock model(s).

    Args:
        *models: Model paths (e.g. "app_label.Model") or classes to lock.
        mode: The lock mode. See the
            [Postgres docs](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-TABLES)
            for a list of all modes and what they mean. There is a constant for each one in the
            `pglock` module, e.g. `pglock.ACCESS_SHARE`.
        using: The database to use.
        timeout: Set a timeout when waiting for the lock. If 0, `NOWAIT` will be used to return
            immediately. If `None`, the timeout is infinite. When using a timeout, the acquisition
            status will be returned. Use the `side_effect` argument to change the runtime behavior.
        side_effect: Adjust the runtime behavior when using a timeout. `pglock.Return` will return
            the acquisition status. `pglock.Raise` will raise a `django.db.utils.OperationalError`
            if the lock cannot be acquired or a timeout happens.

    Returns:
        When using the default side effect, returns `True` if the lock was acquired or `False` if not.

    Raises:
        django.db.utils.OperationalError: If `side_effect=pglock.Raise` and a lock cannot be
            acquired or a timeout occurs.
        RuntimeError: When running code outside of a transaction.
        ValueError: When `side_effect` is an invalid value or no models are supplied.
        TypeError: When `timeout` is an invalid type.
    """  # noqa
    timeout = _cast_timeout(timeout)
    side_effect = side_effect.__class__ if not inspect.isclass(side_effect) else side_effect

    if side_effect not in (Return, Raise):
        raise ValueError("side_effect must be one of pglock.Return or pglock.Raise")

    if not models:
        raise ValueError("Must supply at least one model to pglock.model().")

    if not connections[using].in_atomic_block:
        raise RuntimeError(f'Database "{using}" must be in a transaction to lock models.')

    # Use NOWAIT when a timeout of 0 has been applied.
    nowait = isinstance(timeout, dt.timedelta) and not timeout
    models = [apps.get_model(model) if isinstance(model, str) else model for model in models]
    models = ", ".join(f'"{model._meta.db_table}"' for model in models)
    sql = f'LOCK TABLE {models} IN {mode} MODE {"NOWAIT" if nowait else ""}'

    try:
        with contextlib.ExitStack() as stack:
            if side_effect == Return:
                # If returning True/False, create a savepoint so that
                # the transaction isn't in an errored state when returning.
                stack.enter_context(transaction.atomic(using=using))

            # Set the lock timeout when either `None` or a non-zero
            # timeout has been supplied.
            if timeout is not _unset and not nowait:
                stack.enter_context(lock_timeout(timeout, using=using))

            with connections[using].cursor() as cursor:
                cursor.execute(sql)
                return True
    except OperationalError as exc:
        if side_effect == Return:
            return False
        elif side_effect == Raise:
            raise
        else:
            raise AssertionError from exc


def _prioritize_bg_task(backend_pid, side_effect):
    if not side_effect:  # pragma: no cover
        return

    BlockedPGLock = apps.get_model("pglock.BlockedPGLock")
    qset = BlockedPGLock.objects.pid(backend_pid)
    side_effect(qset)


class _PeriodicTimer(threading.Timer):
    def run(self) -> None:
        self.exc = None
        try:
            while not self.finished.wait(self.interval):
                self.function(*self.args, **self.kwargs)
        except BaseException as e:
            self.exc = e

    def cancel(self) -> None:
        super().cancel()

        if self.exc:
            raise RuntimeError("Exception raised in side effect") from self.exc


class PrioritizeSideEffect(SideEffect):
    """Base class for `pglock.prioritize` side effects.

    Must override the `worker` method, which takes
    a [pglock.models.BlockedPGLock][] queryset of all locks
    that are blocking the prioritized process.

    Return the process IDs or blocked locks that were
    handled.

    Prioritize side effects take optional filters
    when initialize, which are passed to the underlying
    [pglock.models.BlockedPGLock][] queryset.
    """

    def __init__(self, **filters: Any) -> None:
        self.filters = filters

    def worker(self, blocked_locks: BlockedPGLockQuerySet) -> list[int]:
        raise NotImplementedError

    def __call__(self, blocked_locks: BlockedPGLockQuerySet) -> list[int]:
        return self.worker(blocked_locks.filter(**self.filters))


class Terminate(PrioritizeSideEffect):
    """
    The side effect for terminating blocking locks
    when using `pglock.prioritize`.

    Calls `teminate_blocking_activity` on the
    blocked lock queryset.

    Supply a duration to only terminate queries lasting greater than the
    duration.
    """

    def worker(self, blocked_locks):
        return blocked_locks.terminate_blocking_activity()


class Cancel(PrioritizeSideEffect):  # pragma: no cover
    """
    The side effect for canceling blocking locks
    when using `pglock.prioritize`.

    Calls `cancel_blocking_activity` on the
    blocked lock queryset.

    Supply a duration to only cancel queries lasting greater than the
    duration.
    """

    def worker(self, blocked_locks: BlockedPGLockQuerySet) -> list[int]:
        return blocked_locks.cancel_blocking_activity()


@contextlib.contextmanager
def prioritize(
    *,
    interval: int | float | dt.timedelta = 1,
    periodic: bool = True,
    using: str = DEFAULT_DB_ALIAS,
    timeout: dt.timedelta | int | float | _Unset | None = _unset,
    side_effect: type[PrioritizeSideEffect] | PrioritizeSideEffect = Terminate,
) -> Generator[None]:
    """Kill any blocking locks.

    `pglock.prioritize` has a periodic background worker thread that checks for blocking activity
    and terminates it.

    Args:
        interval: The interval (in seconds) at which the background worker runs.
        periodic: If the worker should be ran periodically. If False, blocking locks are
            only killed once after the initial interval has happened.
        using: The database to use.
        timeout: The lock timeout to apply to the wrapped code. This is synonymous with using with
            `with pglock.prioritize(), pglock.timeout()`. Although the background worker should
            properly terminate blocking locks, this serves as a backup option
            to ensure wrapped code doesn't block for too long. Never use a `timeout` that
            is less than `interval`.
        side_effect: The side effect called by the background worker. Supplied a `BlockedPGLock`
            queryset of locks blocking the prioritized code. Returns a list of all blocking PIDs
            that have been handled. The default side effect of `pglock.Terminte` will terminate
            blocking processes. `pglock.Cancel` is another side effect that can be used to cancel
            blocking processes.

    Raises:
        django.db.utils.OperationalError: If `timeout` is used and the timeout expires.
    """
    side_effect = side_effect() if inspect.isclass(side_effect) else side_effect

    if isinstance(interval, (int, float)):
        interval = dt.timedelta(seconds=interval)

    if not isinstance(interval, dt.timedelta):
        raise TypeError('"interval" argument must be an int, float, or timedelta instance')

    backend_pid = pgactivity.pid(using=using)
    timer_class = _PeriodicTimer if periodic else threading.Timer
    killer_thread = timer_class(
        interval.total_seconds(), _prioritize_bg_task, (backend_pid, side_effect)
    )
    killer_thread.start()

    try:
        with contextlib.ExitStack() as stack:
            if timeout is not _unset:
                stack.enter_context(lock_timeout(timeout, using=using))

            yield
    finally:
        killer_thread.cancel()
