from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "gbp_auth", Path(__file__).resolve().parents[2] / "scripts" / "gbp-auth.py")
auth = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(auth)


def test_build_config_has_the_five_keys():
    cfg = auth.build_config("cid", "csecret", "rtok", "accounts/123", "locations/456")

    # gbp_client.get_location_name builds accounts/<a>/locations/<id>, so the location is stored bare.
    assert cfg == {"client_id": "cid", "client_secret": "csecret", "refresh_token": "rtok",
                   "gbp_account_id": "accounts/123", "gbp_location_id": "456"}
    assert auth.build_config("c", "s", "r", "123", "456")["gbp_account_id"] == "accounts/123"


def test_phone_matches():
    assert auth.phone_matches("(626) 922-0091", "626-922-0091")
    assert auth.phone_matches("+1 626-922-0091", "626-922-0091")
    assert not auth.phone_matches("(818) 555-1234", "626-922-0091")
    assert not auth.phone_matches(None, "626-922-0091")
    assert not auth.phone_matches("", "626-922-0091")


def test_location_rows_flatten_titles_and_phones():
    accounts = [{"name": "accounts/1", "accountName": "Cesar"}]
    locations = {"accounts/1": [
        {"name": "locations/9", "title": "Mariachi El Cuis", "phoneNumbers": {"primaryPhone": "(626) 922-0091"}},
        {"name": "locations/8", "title": "No phone"},
    ]}

    rows = auth.location_rows(accounts, locations)

    assert rows == [
        {"account": "accounts/1", "accountName": "Cesar", "location": "locations/9",
         "title": "Mariachi El Cuis", "phone": "(626) 922-0091"},
        {"account": "accounts/1", "accountName": "Cesar", "location": "locations/8",
         "title": "No phone", "phone": None},
    ]


def test_scope_is_business_manage():
    assert auth.SCOPES == ["https://www.googleapis.com/auth/business.manage"]


def test_secret_id():
    assert auth.secret_id("el-cuis") == "/ads-mcp/el-cuis/gbp/config"
