# Advisory Locks

Sometimes applications need to perform custom locking that cannot be easily expressed as row or table locks. The [pglock.advisory][] decorator and context manager solves this by using [Postgres advisory locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS).

## Basic Examples

[pglock.advisory][] is a decorator and context manager that can be used to acquire an advisory lock. Once the lock is acquired, all other code requesting the lock will wait until the lock is released.

For example, below we've ensured there's only one instance of `my_exclusive_function` running at a time:

```python
import pglock

@pglock.advisory("my_module.my_exclusive_function")
def my_exclusive_function():
    # All other calls of my_exclusive_function will wait for this one to finish
```

!!! tip

    When used as a decorator, the lock ID isn't required and will default to `<module_name>.<function_name>`.

When creating an advisory lock, remember that the lock ID is a global name across the entire database. Be sure to choose meaningful names, ideally with namespaces, when serializing code with [pglock.advisory][].

!!! warning

    [Session-based locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS) are used by default. They're manually released when the context manager exits or the database connection is terminated. If connections are pooled (e.g., [pgbouncer](https://www.pgbouncer.org)) and code is killed without raising exceptions (e.g., out-of-memory errors), the lock will continue to be held until the connection is terminated. See [transaction-level locks](#transaction) for an alternative.

## Configuring Lock Wait Time

By default, [pglock.advisory][] will wait forever until the lock can be acquired. Use the `timeout` argument to change this behavior. For example, `timeout=0` will avoid waiting for the lock:

```python
with pglock.advisory("my_lock_id", timeout=0) as acquired:
    if not acquired:
        # Do stuff if the lock cannot be acquired
    else:
        # The lock was acquired
```

As shown above, the context manager returns a flag to indicate if the lock was successfully acquired. Here we wait up to two seconds to acquire the lock:

```python
with pglock.advisory("my_lock_id", timeout=2) as acquired:
    ...
```

!!! tip

    Use a `datetime.timedelta` argument for `timeout` for better precision or `None` to set an infinite timeout.

## Side Effects

The `side_effect` argument adjusts runtime characteristics when using a timeout. For example, below we're using `timeout=0` and `side_effect=pglock.Raise` to raise an exception if the lock cannot be acquired:

```python
with pglock.advisory(timeout=0, side_effect=pglock.Raise):
    # A django.db.utils.OperationalError will be thrown if the lock
    # cannot be acquired.
```

!!! note

    When using the decorator, the side effect defaults to `pglock.Raise` since the return value is not available to the decorated function.

Use `side_effect=pglock.Skip` to skip the function entirely if the lock cannot be acquired. This only applies to usage of the decorator:

```python
@pglock.advisory(timeout=0, side_effect=pglock.Skip)
def one_function_at_a_time():
    # This function runs once at a time. If this function runs anywhere
    # else, it will be skipped.
```

## Shared Locks

Advisory locks can be acquired in shared mode using `shared=True`. Shared locks do not conflict with other shared locks. They only conflict with other exclusive locks of the same lock ID. See the [Postgres docs](https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS-TABLE) for more information.

<a id="transaction"></a>

## Transaction-Level Locks

Use `pglock.advisory(xact=True)` to create a transaction-level advisory lock, which are released at the end of a transaction.

When using the decorator or context manager, a transaction will be opened. A `RuntimeError` will be raised if a transaction is already open.

Use the functional interface to acquire a lock if already in a transaction:

```python
import pglock
from django.db import transaction

with transaction.atomic():
    ...
    acquired = pglock.advisory("lock_id", xact=True).acquire()
    ...

# The lock is released at the end of the transaction.
```

Remember that once acquired, a transaction-level lock cannot be manually released. It will only be released when the transaction is over.

!!! danger

    The functional interface is only intended for transaction-level locks. Use the context manager or decorator for other use cases.
