# django-pglock

`django-pglock` performs advisory locks, table locks, and helps manage blocking locks.
Here's some of the functionality at a glance:

* [pglock.advisory][] for application-level locking, for example, ensuring that tasks don't overlap.
* [pglock.model][] for locking an entire model.
* [pglock.timeout][] for dynamically setting the timeout to acquire a lock.
* [pglock.prioritize][] to kill blocking locks for critical code, such as migrations.
* The [pglock.models.PGLock][] and [pglock.models.BlockedPGLock][] models for querying active and blocked locks.
* The `pglock` management command that wraps the models and provides other utilities.

## Quickstart

### Advisory Locks

Use [pglock.advisory][] to acquire a [Postgres advisory lock](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS):

```python
import pglock

with pglock.advisory("my_lock_id"):
    # This code blocks until the "my_lock_id" lock is available]
```

Above our code will block until the lock is available, meaning no instances of the function will run simultaneously. Use the `timeout` argument to configure how long to wait for the lock. A timeout of zero will return immediately:

```python
with pglock.advisory("my_lock_id", timeout=0) as acquired:
    if acquired:
        # The lock is acquired
```

Use `side_effect=pglock.Raise` to raise a `django.db.utils.OperationalError` if the lock can't be acquired. When using the decorator, you can also use `side_effect=pglock.Skip` to skip the function if the lock can't be acquired:

```python
@pglock.advisory(timeout=0, side_effect=pglock.Skip)
def non_overlapping_func():
    # This function will not run if there's another one already running.
    # The decorator lock ID defaults to <module_name>.<function_name>
```

### Model Locks

[pglock.model][] can take a lock on an entire model during a transaction. For example:

```python
from django.db import transaction
import pglock

with transaction.atomic():
    pglock.model("auth.User")

    # Any operations on auth.User will be exclusive here. Even read access
    # for other transactions is blocked
```

[pglock.model][] uses [Postgres's LOCK statement](https://www.postgresql.org/docs/current/sql-lock.html), and it accepts the lock mode as a argument. See the [Postgres docs for more information](https://www.postgresql.org/docs/current/sql-lock.html).

!!! note

    [pglock.model][] is similar to [pglock.advisory][]. Use the `timeout` argument to avoid waiting for locks, and supply the appropriate `side_effect` to adjust runtime behavior.

### Prioritizing Blocked Code

[pglock.prioritize][] will terminate any locks blocking the wrapped code:

```python
import pglock

@pglock.prioritize()
def my_func():
    # Any other statements that have conflicting locks will be killed on a
    # periodic interval.
    MyModel.objects.update(val="value")
```

[pglock.prioritize][] is useful for prioritizing code, such as migrations, to avoid situations where locks are held for too long.

### Setting the Lock Timeout

Use [pglock.timeout][] to dynamically set [Postgres's lock_timeout runtime setting](https://www.postgresql.org/docs/current/runtime-config-client.html):

```python
import pglock

@pglock.timeout(1)
def do_stuff():
    # This function will throw an exception if any code takes longer than
    # one second to acquire a lock
```

### Querying Locks
Use [pglock.models.PGLock][] to query active locks. It wraps [Postgres's pg_locks view](https://www.postgresql.org/docs/current/view-pg-locks.html). Use [pglock.models.BlockedPGLock][] to query locks and join the activity that's blocking them.

Use `python manage.py pglock` to view and kill locks from the command line. It has several options for dynamic filters and re-usable configuration.

## Compatibility

`django-pglock` is compatible with Python 3.9 - 3.13, Django 4.2 - 5.1, Psycopg 2 - 3, and Postgres 13 - 17.

## Next Steps

We recommend everyone first read:

* [Installation](installation.md) for how to install the library.

After this, there are several usage guides:

* [Advisory Locks](advisory.md) for using advisory locks.
* [Model Locks](model.md) for locking models.
* [Setting the Lock Timeout](timeout.md) for setting dynamic lock timeouts.
* [Prioritizing Blocked Code](prioritize.md) for prioritizing code that may be blocked.
* [Proxy Models](proxy.md) for an overview of the proxy models and custom queryset methods.
* [Management Command](command.md) for using and configuring the management command.

Core API information exists in these sections:

* [Settings](settings.md) for all available Django settings.
* [Module](module.md) for documentation of the `pglock` module and models.
* [Release Notes](release_notes.md) for information about every release.
* [Contributing Guide](contributing.md) for details on contributing to the codebase.
