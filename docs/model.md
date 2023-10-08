# Model Locks

[pglock.model][] is a wrapper around [Postgres's LOCK statement](https://www.postgresql.org/docs/current/sql-lock.html) and can be used to lock an entire model.

## Basic Usage

[pglock.model][] must be used in a transaction. Once called, the lock will be held for the duration of the transaction. For example:

```python
from django.db import transaction
import pglock

with transaction.atomic():
    pglock.model("auth.User")
```

!!! tip

    [pglock.model][] can be called with model paths or model classes. Supply multiple models to lock more than one, for example, `pglock.model("auth.User", "auth.Group")`.

Above we've locked Django's auth user table. By default, it's locked in access exclusive mode. In other words, no
other processes can read or modify the table. Below we've locked the user table in access share mode:

```python
with transaction.atomic():
    pglock.model("auth.User", mode=pglock.ACCESS_SHARE)
```

See the [Postgres docs](https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-TABLES) for a breakdown of all lock levels and how they conflict with one another. `django-pglock` has corresponding constants for each mode.

!!! warning

    When using nested transactions, the lock will be held until either the inner one is rolled back or the outermost call completes. To ensure your transactions aren't nested, use `transaction.atomic(durable=True)`.

## Configuring Lock Wait Time

By default, [pglock.model][] will wait forever until the lock can be acquired.  Use the `timeout` argument to change this behavior.
For example, `timeout=0` will avoid waiting for the lock:

```python
with transaction.atomic():
    acquired = pglock.model("auth.User", timeout=0)
    if acquired:
        # The lock is acquired
```

As shown above, the acquisition status is returned to indicate if the lock was successfully acquired. Here we wait up to two seconds to acquire the lock:

```python
with transaction.atomic():
    acquired = pglock.model("auth.User", timeout=2)
```

!!! tip

    Use a `datetime.timedelta` argument for `timeout` for better precision or `None` to set an infinite timeout.

## Side Effects

The `side_effect` argument adjusts runtime characteristics when using a timeout. For example, below we're using `timeout=0` and `side_effect=pglock.Raise` to raise an exception if the lock cannot be acquired:

```python
with transaction.atomic():
    pglock.model("auth.User", timeout=2, side_effect=pglock.Raise)
    # A django.db.utils.OperationalError will be thrown if the lock cannot be acquired.
```
