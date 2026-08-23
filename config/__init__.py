"""System configuration package.

Loads and exposes environment settings and platform variables.
"""

from config.execution_config import ExecutionConfig
from config.settings import Settings

__all__ = ["ExecutionConfig", "Settings"]
