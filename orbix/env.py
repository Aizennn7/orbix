from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> Path | None:
    """Load the project-root `.env` before runtime configuration is created.

    Existing process variables always win, so `uvicorn --env-file`, Docker, and
    shell exports keep precedence over the file. The file is optional; nothing
    is created or written here, and no value is ever returned to the browser.
    """
    configured = os.environ.get("ORBIX_ENV_FILE", "").strip()
    path = Path(configured).expanduser() if configured else ROOT / ".env"
    if not path.is_file():
        return None
    load_dotenv(path, override=False)
    return path
