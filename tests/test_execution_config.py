"""Unit tests for config/execution_config.py."""

import pytest

from config.execution_config import ExecutionConfig


class TestExecutionConfig:
    """Tests for ExecutionConfig's default/valid/invalid execution_mode values."""

    def test_default_is_paper(self) -> None:
        assert ExecutionConfig().execution_mode == "paper"

    def test_explicit_paper_is_valid(self) -> None:
        assert ExecutionConfig(execution_mode="paper").execution_mode == "paper"

    def test_explicit_live_is_valid(self) -> None:
        assert ExecutionConfig(execution_mode="live").execution_mode == "live"

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError, match="execution_mode"):
            ExecutionConfig(execution_mode="not_a_real_mode")

    def test_load_classmethod_returns_an_instance(self) -> None:
        config = ExecutionConfig.load()
        assert isinstance(config, ExecutionConfig)
