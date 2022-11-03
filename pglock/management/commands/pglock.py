import datetime as dt
import os
import re
import sys
import textwrap

from django.core.management.base import BaseCommand
from django.db.models import F

from pglock import config, models


def get_terminal_width():  # pragma: no cover
    try:
        return os.get_terminal_size().columns
    except OSError:  # This only happens during testing
        return 80


def _format(val, expanded):
    if isinstance(val, dt.timedelta):
        if val:  # pragma: no branch
            val -= dt.timedelta(microseconds=val.microseconds)
    elif isinstance(val, str):
        if not expanded:
            val = " ".join(val.split())
        else:
            val = textwrap.dedent(val).strip()

    return str(val)


def _handle_user_input(*, cfg, num_locks, stdout):
    is_blocking = cfg.get("blocking")
    is_cancel = cfg.get("cancel")

    if not num_locks:
        stdout.write(
            f"No {'blocking ' if is_blocking else ''}queries"
            f" to {'cancel' if is_cancel else 'terminate'}."
        )
        return False

    if not cfg.get("yes"):
        pluralize = "y" if num_locks == 1 else "ies"
        resp = input(
            (
                f"{'Cancel' if is_cancel else 'Terminate'} "
                f"{num_locks} {'blocking ' if is_blocking else ''}quer{pluralize}? (y/[n]) "
            )
        )
        if not re.match("^(y)(es)?$", resp, re.IGNORECASE):
            stdout.write("Aborting!")
            return False

    return True


class Command(BaseCommand):
    help = "Show and manage locks."

    def add_arguments(self, parser):
        parser.add_argument("pids", nargs="*", type=str)
        parser.add_argument("-d", "--database", help="The database")
        parser.add_argument(
            "-f",
            "--filter",
            action="append",
            dest="filters",
            help="Filters for the underlying queryset",
        )
        parser.add_argument(
            "-o",
            "--on",
            action="append",
            dest="on",
            help="Show locks on models",
        )
        parser.add_argument(
            "-a",
            "--attribute",
            action="append",
            dest="attributes",
            help="Attributes to show",
        )
        parser.add_argument("--blocking", action="store_true", help="Show blocking locks")
        parser.add_argument("-l", "--limit", help="Limit results")
        parser.add_argument("-e", "--expanded", action="store_true", help="Show an expanded view")
        parser.add_argument("-c", "--config", help="Use a config from settings.PGLOCK_CONFIGS")
        parser.add_argument("-y", "--yes", action="store_true", help="Don't prompt for input")

        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--cancel",
            action="store_true",
            help="Cancel activity",
        )
        group.add_argument(
            "--terminate",
            action="store_true",
            help="Terminate activity",
        )

    def handle(self, *args, **options):
        cfg = config.get(options["config"], **options)
        is_blocking = cfg.get("blocking")
        is_cancel = cfg.get("cancel")
        is_terminate = cfg.get("terminate")

        if cfg.get("blocking"):
            qset = models.BlockedPGLock.objects.select_related("activity", "blocking_activity")
        else:
            qset = models.PGLock.objects.select_related("activity")

        locks = qset.config(options["config"], **options).values(*cfg["attributes"])

        term_w = get_terminal_width()
        expanded = cfg.get("expanded", False)

        if is_cancel or is_terminate:
            locks = (
                locks.distinct("activity_id")
                if not is_blocking
                else locks.distinct("blocking_activity_id")
            )
            num_locks = len(locks)

            if not _handle_user_input(cfg=cfg, num_locks=num_locks, stdout=self.stdout):
                sys.exit(1)

            method_name = (
                f"{'cancel' if is_cancel else 'terminate'}"
                f"{'_blocking' if is_blocking else ''}_activity"
            )
            num_success = len(getattr(locks, method_name)())
            pluralize = "y" if num_success == 1 else "ies"
            self.stdout.write(
                (
                    f"{'Canceled' if is_cancel else 'Terminated'} "
                    f"{num_success} {'blocking ' if is_blocking else ''}quer{pluralize}"
                )
            )
        else:
            locks = locks.order_by(
                "granted",
                # Only PG14 and up has a non-null wait_duration. Sort by this first, but
                # use activity__duration as a backup if wait_duration is null
                F("wait_duration").desc(nulls_last=True),
                F("activity__duration").desc(nulls_last=True),
            )

            if not cfg.get("pids") and cfg.get("limit"):
                locks = locks[: cfg["limit"]]

            for lock in locks:
                if cfg.get("expanded"):
                    self.stdout.write("\033[1m" + "─" * term_w + "\033[0m")
                    for a in cfg["attributes"]:
                        self.stdout.write(f"\033[1m{a}\033[0m: {_format(lock[a], expanded)}")
                else:
                    line = " | ".join(_format(lock[a], expanded) for a in cfg["attributes"])
                    line = line[:term_w]
                    self.stdout.write(line)
