"""Regression tests for get_artifacts_dir().

Must resolve relative to the repo root by default, and honor
TRADEBOT_ARTIFACTS_DIR when set, so research modules never depend on a
hardcoded absolute path from a different machine.
"""

from pathlib import Path

import pytest

from utils.paths import PROJECT_ROOT, get_artifacts_dir


def test_default_returns_repo_relative_artifacts_dir() -> None:
    """Verifies the default artifacts directory is repo-relative, not absolute."""
    assert get_artifacts_dir() == PROJECT_ROOT / "artifacts"


def test_env_override_takes_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifies TRADEBOT_ARTIFACTS_DIR overrides the repo-relative default."""
    override_dir = tmp_path / "custom_artifacts"
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(override_dir))

    assert get_artifacts_dir() == override_dir


def test_no_env_override_ignores_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies an empty TRADEBOT_ARTIFACTS_DIR falls back to the repo-relative default."""
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", "")

    assert get_artifacts_dir() == PROJECT_ROOT / "artifacts"
