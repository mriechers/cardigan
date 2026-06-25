"""Single source of truth for the LLM config file location.

api and worker run as separate containers; both must read/write the SAME
config file so Settings changes made on the API take effect on the worker.
Point LLM_CONFIG_PATH at a path on a shared volume in production.
"""

import os
import shutil
from pathlib import Path

# Packaged default shipped in the image (repo-relative).
DEFAULT_CONFIG = Path("config/llm-config.json")


def resolve_config_path() -> Path:
    """Return the active config path, seeding it from the packaged default if absent."""
    target = Path(os.getenv("LLM_CONFIG_PATH", str(DEFAULT_CONFIG)))
    if not target.exists() and DEFAULT_CONFIG.exists() and target != DEFAULT_CONFIG:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_CONFIG, target)
    return target
