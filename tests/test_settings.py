"""Unit tests for config/settings.py (Bug #26: fragile MT5_LOGIN parsing).

MT5_LOGIN's conversion runs at class-definition (import) time via a
dataclass field default, so a malformed value must never raise -- that
would crash every module importing Settings, not just MT5-related code.
"""

import importlib
import logging

import config.settings as settings_module
from config.settings import Settings, _parse_mt5_login


def test_parse_mt5_login_valid_integer_string() -> None:
    assert _parse_mt5_login("12345") == 12345


def test_parse_mt5_login_default_zero_string() -> None:
    assert _parse_mt5_login("0") == 0


def test_parse_mt5_login_invalid_text_defaults_to_zero_and_warns(
    caplog,
) -> None:
    with caplog.at_level(logging.WARNING):
        result = _parse_mt5_login("invalid_text")

    assert result == 0
    assert any("MT5_LOGIN" in r.message for r in caplog.records)


def test_settings_load_with_malformed_mt5_login_does_not_raise(monkeypatch) -> None:
    """Reproduces the reported crash: a malformed MT5_LOGIN in .env must not
    raise ValueError at import/class-definition time -- Settings.load()
    should come back with MT5_LOGIN=0 instead."""
    monkeypatch.setenv("MT5_LOGIN", "invalid_text")
    try:
        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings.load()
        assert settings.MT5_LOGIN == 0
    finally:
        # Restore the module to its normal (valid env) state so later tests
        # in the same process see the real Settings class definition.
        monkeypatch.delenv("MT5_LOGIN", raising=False)
        importlib.reload(settings_module)


def test_settings_load_with_valid_mt5_login(monkeypatch) -> None:
    monkeypatch.setenv("MT5_LOGIN", "5052764320")
    try:
        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings.load()
        assert settings.MT5_LOGIN == 5052764320
    finally:
        monkeypatch.delenv("MT5_LOGIN", raising=False)
        importlib.reload(settings_module)


def test_default_settings_load_is_unaffected() -> None:
    settings = Settings.load()
    assert isinstance(settings.MT5_LOGIN, int)
