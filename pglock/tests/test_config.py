import pytest

from pglock import config


def test_get(settings):
    settings.PGLOCK_ATTRIBUTES = ["hello"]
    settings.PGLOCK_BLOCKING_ATTRIBUTES = ["world"]
    settings.PGLOCK_CONFIGS = {
        "test": {"expanded": True},
        "blocking": {"blocking": True, "limit": 20},
        "attributes": {"blocking": True, "attributes": ["custom"]},
    }

    with pytest.raises(ValueError):
        config.get("bad")

    cfg = config.get("test")
    assert cfg["expanded"]
    assert cfg["attributes"] == ["hello"]

    cfg = config.get("blocking")
    assert cfg["blocking"]
    assert cfg["attributes"] == ["world"]
    assert cfg["limit"] == 20

    cfg = config.get("attributes")
    assert cfg["blocking"]
    assert cfg["attributes"] == ["custom"]

    assert config.get(None) == {"limit": 25, "attributes": ["hello"]}
