"""The live profiles in social/profiles must stay loadable and well-formed.

Other tests use a frozen copy (tests/fixtures/social_profiles), so this is the only
check on the real files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared import social_ledger

LIVE_DIR = Path(__file__).resolve().parents[2] / "social" / "profiles"


@pytest.mark.parametrize("path", sorted(LIVE_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_live_profile_is_well_formed(path):
    profile = json.loads(path.read_text(encoding="utf-8"))

    assert profile["businessKey"] == path.stem
    assert profile["platforms"] and set(profile["platforms"]) <= set(social_ledger.PLATFORMS)
    assert profile["phone"] and profile["serviceCities"] and profile["slots"]
    for platform in profile.get("platformStarts", {}):
        assert platform in profile["platforms"]
