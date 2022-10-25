import contextlib
import datetime as dt
import functools
import hashlib
import inspect
import threading

from django.apps import apps
from django.db import connections, DEFAULT_DB_ALIAS, transaction
from django.db.utils import OperationalError
import pgactivity
import psycopg2.extensions


# Lock levels
ACCESS_SHARE = "ACCESS SHARE"
ROW_SHARE = "ROW SHARE"
ROW_EXCLUSIVE = "ROW EXCLUSIVE"
SHARE_UPDATE_EXCLUSIVE = "SHARE UPDATE EXCLUSIVE"
SHARE = "SHARE"
SHARE_ROW_EXCLUSIVE = "SHARE ROW EXCLUSIVE"
EXCLUSIVE = "EXCLUSIVE"
ACCESS_EXCLUSIVE = "ACCESS EXCLUSIVE"


_timeout = threading.local()
_unset = object()


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


@contextlib.contextmanager
def lock_timeout(timeout=_unset, *, using=DEFAULT_DB_ALIAS, **timedelta_kwargs):
    """Set the lock timeout as a decorator or context manager.

    A value of ``None`` will set an infinite lock timeout.
    A value of less than a millisecond is not permitted.

    Nested invocations will successfully apply and rollback the timeout to
    the previous value.

    Args:
        timeout (Union[datetime.timedelta, int, float, None]): The number
            of seconds as an integer or float. Use a timedelta object to
            precisely specify the timeout interval. Use ``None`` for
            an infinite timeout.
        using (str, default="default"): The database to use.
        **timedelta_kwargs: Keyword arguments to directly supply to
            datetime.timedelta to create an interval. E.g.
            ``pglock.timeout(seconds=1, milliseconds=100)``
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
                "Must supply value greater than a millisecond to pglock.timeout or use ``None`` to"
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
            cursor.execute(f"SET lock_timeout={_timeout.value}")
            yield
    finally:
        _timeout.value = old_timeout

        with connections[using].cursor() as cursor:
            if (
                not cursor.connection.get_transaction_status()
                == psycopg2.extensions.TRANSACTION_STATUS_INERROR
            ):
                if _timeout.value is None:
                    cursor.execute("RESET lock_timeout")
                else:
                    cursor.execute(f"SET lock_timeout={_timeout.value}")


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


def advisory_id(lock_id):
    """
    Given a lock ID, return the (classid, objid) tuple that Postgres uses
    for the advisory lock in the pg_locks table
    """
    lock_id = _cast_lock_id(lock_id)
    return lock_id >> 32, lock_id & 0xFFFFFFFF


class advisory(contextlib.ContextDecorator):
    """Obtain an advisory lock.

    Args:
        lock_id (Union[str, int], default=None): The ID of the lock. When
            using the decorator, it defaults to the full module path and
            function name of the wrapped function. It must be supplied to
            the context manager.
        shared (bool, default=False): When ``True``, creates a shared
            advisory lock. Consult the
            `Postgres docs <https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS>`__
            for more information.
        using (str, default="default"): The database to use.
        timeout (Union[int, float, datetime.timedelta, None]): Set a timeout when waiting
            for the lock. This timeout only applies to the lock acquisition statement and not the
            wrapped code. If 0, ``pg_try_advisory_lock`` will be used to return immediately. If
            ``None``, an infinite timeout will be used. When
            using a timeout, the acquisition status will be returned when running as a context
            manager. Use the ``side_effect`` argument to change the runtime behavior.
        side_effect (str): Adjust the runtime behavior when using a timeout.
            `pglock.Return` will return the acquisition status when using the context manager.
            `pglock.Raise` will raise a ``django.db.utils.OperationalError`` if the lock cannot
            be acquired or a timeout happens. `pglock.Skip` will skip decoratored code if the
            lock cannot be acquired. Defaults to `pglock.Return` when used as a context manager
            or `pglock.Raise` when used as a decorator.

    Returns:
        bool: When using the default side effect, returns ``True`` if the lock was acquired or
        ``False`` if not.

    Raises:
        django.db.utils.OperationalError: When a lock cannot be acquired or a timeout happens
            when using ``side_effect=pglock.Raise``.
        ValueError: If an invalid ``side_effect`` is provided or no lock ID is supplied for
            the context manager.
        TypeError: If the lock ID is not a string or int.
    """  # noqa

    def __init__(
        self,
        lock_id=None,
        *,
        shared=False,
        using=DEFAULT_DB_ALIAS,
        timeout=_unset,
        side_effect=None,
    ):
        """Acquire an advisory lock"""
        self.lock_id = lock_id
        self.using = using
        self.side_effect = side_effect
        self.shared = shared
        self.timeout = _cast_timeout(timeout)

        # Use pg_try_advisory.. when a timeout of 0 has been applied.
        self.nowait = isinstance(self.timeout, dt.timedelta) and not self.timeout

        # "_func" will be set if a function is wrapped
        self._func = None

    @property
    def int_lock_id(self):
        return _cast_lock_id(self.lock_id)

    @property
    def acquire(self):
        return f'pg{"_try" if self.nowait else ""}_advisory_lock{"_shared" if self.shared else ""}'

    @property
    def release(self):
        return f'pg_advisory_unlock{"_shared" if self.shared else ""}'

    def __call__(self, func):
        self._func = func

        @functools.wraps(func)
        def inner(*args, **kwargs):
            with self._recreate_cm():
                if self.acquired or self.side_effect != Skip:
                    return func(*args, **kwargs)

        return inner

    def _process_runtime_parameters(self):
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

    def __enter__(self):
        self._process_runtime_parameters()

        sql = f"SELECT {self.acquire}({self.int_lock_id})"

        with connections[self.using].cursor() as cursor:
            try:
                with contextlib.ExitStack() as stack:
                    if self.timeout is not _unset and not self.nowait:
                        stack.enter_context(lock_timeout(self.timeout, using=self.using))

                        if self.side_effect != Raise and connections[self.using].in_atomic_block:
                            # If returning True/False, create a savepoint so that
                            # the transaction isn't in an errored state when returning.
                            stack.enter_context(transaction.atomic(using=self.using))

                    cursor.execute(sql)
                    self.acquired = cursor.fetchone()[0] if self.nowait else True
            except OperationalError:
                # This block only happens when the lock times out
                if self.side_effect != Raise:
                    self.acquired = False
                else:
                    raise

            if not self.acquired and self.side_effect == Raise:
                raise OperationalError(f'Could not acquire lock "{self.lock_id}"')

            self.stack = contextlib.ExitStack()
            if self.acquired and connections[self.using].in_atomic_block:
                # Create a savepoint so that we can successfully release
                # the lock if the transaction errors
                self.stack.enter_context(transaction.atomic(using=self.using))

            self.stack.__enter__()

            return self.acquired

    def __exit__(self, exc_type, exc_value, traceback):
        self.stack.__exit__(exc_type, exc_value, traceback)

        if self.acquired:
            with connections[self.using].cursor() as cursor:
                cursor.execute(f"SELECT {self.release}({self.int_lock_id})")


def model(
    *models,
    mode=ACCESS_EXCLUSIVE,
    using=DEFAULT_DB_ALIAS,
    timeout=_unset,
    side_effect=Return,
):
    """Lock model(s).

    Args:
        *models (Union[str, models.Model]): Model paths (e.g. "app_label.Model") or
            classes to lock.
        mode (str, default=pglock.ACCESS_EXCLUSIVE): The lock mode.
            See the
            `Postgres docs <https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-TABLES>`__
            for a list of all modes and what they mean.
            There is a constant for each one in the ``pglock`` module, e.g.
            ``pglock.ACCESS_SHARE``.
        using (str, default="default"): The database to use.
        timeout (Union[int, float, datetime.timedelta, None]): Set a timeout when waiting
            for the lock. If 0, ``NOWAIT` will be used to return immediately. If ``None``, the
            timeout is infinite. When using a timeout, the acquisition status will be returned.
            Use the ``side_effect`` argument to change the runtime behavior.
        side_effect (str, default=pglock.Return): Adjust the runtime behavior when using a timeout.
            `pglock.Return` will return the acquisition status. `pglock.Raise` will
            raise a ``django.db.utils.OperationalError`` if the lock cannot
            be acquired or a timeout happens.

    Returns:
        bool: When using the default side effect, returns ``True`` if the lock was acquired or
        ``False`` if not.

    Raises:
        django.db.utils.OperationalError: If ``side_effect=pglock.Raise`` and a lock cannot be
            acquired or a timeout occurs.
        RuntimeError: When running code outside of a transaction.
        ValueError: When ``side_effect`` is an invalid value or no models are supplied.
        TypeError: When ``timeout`` is an invalid type.

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

            # Set the lock timeout when either ``None`` or a non-zero
            # timeout has been supplied.
            if timeout is not _unset and not nowait:
                stack.enter_context(lock_timeout(timeout, using=using))

            with connections[using].cursor() as cursor:
                cursor.execute(sql)
                return True
    except OperationalError:
        if side_effect == Return:
            return False
        elif side_effect == Raise:
            raise
        else:
            raise AssertionError


def _prioritize_bg_task(backend_pid, side_effect):
    if not side_effect:  # pragma: no cover
        return

    BlockedPGLock = apps.get_model("pglock.BlockedPGLock")
    qset = BlockedPGLock.objects.pid(backend_pid)
    side_effect(qset)


class _PeriodicTimer(threading.Timer):
    def run(self):
        self.exc = None
        try:
            while not self.finished.wait(self.interval):
                self.function(*self.args, **self.kwargs)
        except BaseException as e:
            self.exc = e

    def cancel(self):
        super().cancel()

        if self.exc:
            raise RuntimeError("Exception raised in side effect") from self.exc


class PrioritizeSideEffect(SideEffect):
    """Base class for `pglock.prioritize` side effects.

    Must override the ``worker`` method, which takes
    a `pglock.models.BlockedPGLock` queryset of all locks
    that are blocking the prioritized process.

    Return the process IDs or blocked locks that were
    handled.

    Prioritize side effects take optional filters
    when initialize, which are passed to the underlying
    `pglock.models.BlockedPGLock` queryset.
    """

    def __init__(self, **filters):
        self.filters = filters

    def worker(self, blocked_locks):
        raise NotImplementedError

    def __call__(self, blocked_locks):
        return self.worker(blocked_locks.filter(**self.filters))


class Terminate(PrioritizeSideEffect):
    """
    The side effect for terminating blocking locks
    when using `pglock.prioritize`.

    Calls ``teminate_blocking_activity`` on the
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

    Calls ``cancel_blocking_activity`` on the
    blocked lock queryset.

    Supply a duration to only cancel queries lasting greater than the
    duration.
    """

    def worker(self, blocked_locks):
        return blocked_locks.cancel_blocking_activity()


@contextlib.contextmanager
def prioritize(
    *,
    interval=1,
    periodic=True,
    using=DEFAULT_DB_ALIAS,
    retries=0,
    timeout=_unset,
    side_effect=Terminate,
):
    """Kill any blocking locks.

    `pglock.prioritize` has a periodic background worker thread that checks for blocking activity
    and terminates it.

    Args:
        interval (Union[int, float, datetime.timedelta], default=1): The interval at
            which the background worker runs. Defaults to running every second.
        periodic (bool, default=True): If the worker should be ran periodically. If False,
            blocking locks are only killed once after the initial interval has happened.
        using (str, default="default"): The database to use.
        timeout (Union[datetime.timdelta, int, float, None]): The lock timeout to apply to the
            wrapped code. This is synonymous with using with
            ``with pglock.prioritize(), pglock.timeout()``. Although the background worker should
            properly terminate blocking locks, this serves as a backup option
            to ensure wrapped code doesn't block for too long. Never use a ``timeout`` that
            is less than ``interval``.
        side_effect (pglock.PrioritizeSideEffect, default=pglock.Terminate): The side effect
            called by the background worker. Supplied a `BlockedPGLock` queryset of locks
            blocking the prioritized code. Returns a list of all blocking PIDs that have been
            handled. The default side effect of ``pglock.Terminte`` will terminate blocking
            processes. `pglock.Cancel` is another side effect that can be used to cancel
            blocking processes.

    Raises:
        django.db.utils.OperationalError: If ``timeout`` is used and the timeout expires.
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
