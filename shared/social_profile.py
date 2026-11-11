from __future__ import annotations

import json
import os
import re
from pathlib import Path

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "social" / "profiles"


def _profiles_dir() -> Path:
    override = os.environ.get("ADS_MCP_SOCIAL_PROFILES_DIR")
    return Path(override) if override else _DEFAULT_DIR


def load_profile(business_key: str) -> dict | None:
    """Load a social profile by business key; None if missing."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", business_key or ""):
        return None
    path = _profiles_dir() / f"{business_key}.json"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)
