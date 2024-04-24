"""Core way to access configuration"""

from django.conf import settings


def attributes():
    return getattr(
        settings,
        "PGLOCK_ATTRIBUTES",
        [
            "activity_id",
            "activity__duration",
            "granted",
            "mode",
            "rel_kind",
            "rel_name",
            "activity__context",
            "activity__query",
        ],
    )


def blocking_attributes():
    return getattr(
        settings,
        "PGLOCK_BLOCKING_ATTRIBUTES",
        [
            "activity_id",
            "blocking_activity_id",
            "activity__context",
            "blocking_activity__context",
            "activity__query",
            "blocking_activity__query",
        ],
    )


def configs():
    """Return pre-configured LS arguments"""
    return getattr(settings, "PGLOCK_CONFIGS", {})


def limit():
    """The default limit when using the pglock command"""
    return getattr(settings, "PGLOCK_LIMIT", 25)


def get(name, **overrides):
    """Get a configuration with overrides"""
    if not name:
        cfg = {}
    elif name not in configs():
        raise ValueError(f'"{name}" is not a valid config name from settings.PGLOCK_CONFIGS')
    else:
        cfg = configs()[name]

    # Note: We might allow overriding with "None" or empty values later, but currently no
    # settings allow this. This code filters overrides so that management commands can
    # simply pass in options that might already contain Nones
    cfg.update(**{key: val for key, val in overrides.items() if val})

    if "limit" not in cfg:
        cfg["limit"] = limit()

    if "attributes" not in cfg:
        cfg["attributes"] = attributes() if not cfg.get("blocking") else blocking_attributes()

    return cfg
