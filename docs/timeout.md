# Setting the Lock Timeout

Use [pglock.timeout][] to dynamically set Postgres's `lock_timeout` variable. This setting is isolated to the thread executing code.

For example, below we've ensured that no statement will take longer than three seconds to acquire a lock:

```python
import pglock

with pglock.timeout(seconds=3):
    # If any SQL statement takes longer than 3 seconds to acquire a lock,
    # a django.db.utils.OperationalError will be raised.
```

!!! tip

    [pglock.timeout][] takes the same arguments as Python's `timedelta` object, meaning you can also pass `milliseconds`. You can also supply a `timedelta` object as the first argument.

[pglock.timeout][] can be nested like so:

```python
with pglock.timeout(seconds=2):
    # Every statment here will have a lock timeout of 2 seconds

    with pglock.timeout(seconds=5):
        # Every statement will now have a lock timeout of 5 seconds

    # Statements here will have a lock timeout of 2 seconds
```

Remember, [pglock.timeout][] can also be used as a decorator.