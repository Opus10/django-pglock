# Settings

Below are all settings for `django-pglock`.

## PGLOCK_ATTRIBUTES

The default attributes of the `PGLock` model shown by the `pglock` management command.

**Default** `("activity_id", "activity__duration", "mode", "rel_kind", "rel_name", "activity__context", "activity__query")`

## PGLOCK_BLOCKING_ATTRIBUTES

The default attributes of the `BlockedPGLock` model shown by the `pglock` management command when the `--blocking` flag is used.

**Default** `("activity_id", "blocking_activity_id", "activity__context", "blocking_activity__context", "activity__query", "blocking_activity__query")`

## PGLOCK_CONFIGS

Re-usable configurations that can be supplied to the `pglock` command with the `-c` option. Configurations are referenced by their key in the dictionary.

For example:

```python
PGLOCK_CONFIGS = {
    "blocked": {
        "filters": ["granted=False"]
    }
}
```

Doing `python manage.py pglock -c long-running` will only show locks with a wait duration greater than a minute.

**Default** `{}`

## PGLOCK_LIMIT

Limit the results returned by the `pglock` command. Can be overridden with the `-l` option.

**Default** `25`
