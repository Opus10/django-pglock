# Prioritizing Blocked Code

[pglock.prioritize][] ensures that any blocking locks of a piece of code will be terminated. A background thread carries out termination of blocking statements on a configurable periodic interval.

## Basic Usage

For example, below we've ensured that our function kills any blocking activity during its execution:

```python
import pglock

@pglock.prioritize()
def my_important_function():
    # If any statements block this function, they will be terminated.
```

By default, the background thread operations on a one-second interval, but this can be configured with the `interval` argument:

```python
@pglock.prioritize(interval=5)
def my_important_function():
    # Every five seconds, blocking activity will be terminated
```

!!! tip

    Use a `datetime.timedelta` object for the interval for more precision.

## Side Effects

The `side_effect` argument can be used to configure the function executed by the background worker. It defaults to `pglock.Terminate`, which terminates all blocking queries.

`pglock.Terminate` can be supplied filters that are applied to the underlying `pglock.models.BlockedPGLock` queryset. For example, the following will terminate blocking queries that have a duration greater than five seconds:

```python
@pglock.prioritize(
    side_effect=pglock.Terminate(blocking_activity__duration__gte="5 seconds")
)
def my_important_function():
    # Blocking activity with a duration greater than five seconds will
    # be terminated
```

Underneath the hood, side effects are supplied a `pglock.models.BlockedPGLock` queryset that has filters applied. Side effects inherit `pglock.PrioritizeSideEffect` and implement the `worker` method. The `pglock.Terminate` side effect looks like this:

```python
class Terminate(pglock.PrioritizeSideEffect):
    def worker(self, blocked_locks):
        return blocked_locks.terminate_blocking_activity()
```

If you don't want to terminate processes, you can use `pglock.prioritize(side_effect=pglock.Cancel)` to cancel blocking activity; however, keep in mind that some queries are not able to be canceled and the prioritized process may continue to be blocked.
