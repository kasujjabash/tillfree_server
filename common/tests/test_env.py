import pytest
from django.core.exceptions import ImproperlyConfigured

from common.env import env_bool, env_int, env_list, env_str


def test_required_string_is_enforced(monkeypatch):
    monkeypatch.delenv("TT_X", raising=False)
    with pytest.raises(ImproperlyConfigured, match="TT_X"):
        env_str("TT_X")
    assert env_str("TT_X", "fallback") == "fallback"


@pytest.mark.parametrize(
    ("raw", "expected"), [("true", True), ("ON", True), ("0", False), ("", False)]
)
def test_bool_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("TT_B", raw)
    assert env_bool("TT_B", default=not expected) is expected


def test_bool_rejects_garbage(monkeypatch):
    monkeypatch.setenv("TT_B", "maybe")
    with pytest.raises(ImproperlyConfigured):
        env_bool("TT_B", default=False)


def test_int_parsing(monkeypatch):
    monkeypatch.setenv("TT_I", "42")
    assert env_int("TT_I", 1) == 42
    monkeypatch.setenv("TT_I", "forty")
    with pytest.raises(ImproperlyConfigured):
        env_int("TT_I", 1)


def test_list_drops_blanks(monkeypatch):
    monkeypatch.setenv("TT_L", "a, b,,c ")
    assert env_list("TT_L") == ["a", "b", "c"]
