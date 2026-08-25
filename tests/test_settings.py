"""Unit tests for config/settings.py.

Bug #26: MT5_LOGIN's conversion runs at class-definition (import) time via a
dataclass field default, so a malformed value must never raise -- that
would crash every module importing Settings, not just MT5-related code.

Bug #58: DUPLICATE_POLICY/MISSING_VALUE_POLICY were free-form strings never
validated against their documented allowed values -- a typo'd value fell
through every if/elif branch in data/csv_provider.py silently, letting
duplicates/NaNs flow through unhandled with no warning. Unlike MT5_LOGIN,
this validation runs in __post_init__ (per Settings() construction, not at
bare module-import time), so raising here is safe -- see Settings.__post_init__.
"""

import importlib
import logging

import pytest

import config.settings as settings_module
from config.settings import Settings, _parse_max_daily_loss_pct, _parse_mt5_login


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
    should come back with MT5_LOGIN=0 instead.
    """
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
    monkeypatch.setenv("MT5_LOGIN", "67660753")
    try:
        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings.load()
        assert settings.MT5_LOGIN == 67660753
    finally:
        monkeypatch.delenv("MT5_LOGIN", raising=False)
        importlib.reload(settings_module)


def test_default_settings_load_is_unaffected() -> None:
    settings = Settings.load()
    assert isinstance(settings.MT5_LOGIN, int)


def test_parse_max_daily_loss_pct_valid_float_string() -> None:
    assert _parse_max_daily_loss_pct("0.03") == 0.03


def test_parse_max_daily_loss_pct_invalid_text_defaults_and_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        result = _parse_max_daily_loss_pct("not_a_float")

    assert result == 0.05
    assert any("MAX_DAILY_LOSS_PCT" in r.message for r in caplog.records)


def test_parse_max_daily_loss_pct_zero_defaults_and_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        result = _parse_max_daily_loss_pct("0")

    assert result == 0.05
    assert any("MAX_DAILY_LOSS_PCT" in r.message for r in caplog.records)


def test_parse_max_daily_loss_pct_negative_defaults_and_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        result = _parse_max_daily_loss_pct("-0.1")

    assert result == 0.05
    assert any("MAX_DAILY_LOSS_PCT" in r.message for r in caplog.records)


def test_settings_load_with_malformed_max_daily_loss_pct_does_not_raise(monkeypatch) -> None:
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "garbage")
    try:
        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings.load()
        assert settings.MAX_DAILY_LOSS_PCT == 0.05
    finally:
        monkeypatch.delenv("MAX_DAILY_LOSS_PCT", raising=False)
        importlib.reload(settings_module)


def test_settings_load_with_valid_max_daily_loss_pct(monkeypatch) -> None:
    monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "0.02")
    try:
        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings.load()
        assert settings.MAX_DAILY_LOSS_PCT == 0.02
    finally:
        monkeypatch.delenv("MAX_DAILY_LOSS_PCT", raising=False)
        importlib.reload(settings_module)


def test_default_max_daily_loss_pct_is_positive_float() -> None:
    settings = Settings.load()
    assert isinstance(settings.MAX_DAILY_LOSS_PCT, float)
    assert settings.MAX_DAILY_LOSS_PCT > 0


@pytest.mark.parametrize("policy", ["drop", "keep"])
def test_settings_load_with_valid_duplicate_policy_values(monkeypatch, policy: str) -> None:
    monkeypatch.setenv("DUPLICATE_POLICY", policy)
    try:
        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings.load()
        assert settings.DUPLICATE_POLICY == policy
    finally:
        monkeypatch.delenv("DUPLICATE_POLICY", raising=False)
        importlib.reload(settings_module)


def test_settings_load_with_invalid_duplicate_policy_raises(monkeypatch) -> None:
    monkeypatch.setenv("DUPLICATE_POLICY", "Drop")  # wrong case -- a realistic typo
    try:
        reloaded = importlib.reload(settings_module)
        with pytest.raises(ValueError, match="DUPLICATE_POLICY"):
            reloaded.Settings.load()
    finally:
        monkeypatch.delenv("DUPLICATE_POLICY", raising=False)
        importlib.reload(settings_module)


@pytest.mark.parametrize("policy", ["raise", "drop", "fill"])
def test_settings_load_with_valid_missing_value_policy_values(monkeypatch, policy: str) -> None:
    monkeypatch.setenv("MISSING_VALUE_POLICY", policy)
    try:
        reloaded = importlib.reload(settings_module)
        settings = reloaded.Settings.load()
        assert settings.MISSING_VALUE_POLICY == policy
    finally:
        monkeypatch.delenv("MISSING_VALUE_POLICY", raising=False)
        importlib.reload(settings_module)


def test_settings_load_with_invalid_missing_value_policy_raises(monkeypatch) -> None:
    monkeypatch.setenv("MISSING_VALUE_POLICY", "ignore")  # not one of raise/drop/fill
    try:
        reloaded = importlib.reload(settings_module)
        with pytest.raises(ValueError, match="MISSING_VALUE_POLICY"):
            reloaded.Settings.load()
    finally:
        monkeypatch.delenv("MISSING_VALUE_POLICY", raising=False)
        importlib.reload(settings_module)
