from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from tests.fake_tiktok import fake_tt  # noqa: F401  (fixture)

_SPEC = importlib.util.spec_from_file_location(
    "tiktok_auth", Path(__file__).resolve().parents[2] / "scripts" / "tiktok-auth.py")
auth = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(auth)

REDIRECT = "https://mariachielcuis.com/"


def test_auth_url_has_scopes_redirect_and_state():
    url = auth.build_auth_url("ck123", REDIRECT, "st8")

    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://www.tiktok.com/v2/auth/authorize/"
    assert q == {"client_key": "ck123", "scope": "user.info.basic,video.upload", "response_type": "code",
                 "redirect_uri": REDIRECT, "state": "st8"}


def test_auth_rejects_state_mismatch():
    with pytest.raises(ValueError, match="state"):
        auth.parse_pasted("https://mariachielcuis.com/?code=abc&scopes=video.upload&state=other", "st8")


def test_auth_accepts_url_or_bare_code():
    assert auth.parse_pasted("https://mariachielcuis.com/?code=ab%2Acd&state=st8", "st8") == "ab*cd"
    assert auth.parse_pasted("  ab%2Acd  ", "st8") == "ab*cd"


def test_auth_surfaces_denied_authorization():
    with pytest.raises(ValueError, match="access_denied"):
        auth.parse_pasted("https://mariachielcuis.com/?error=access_denied&error_description=user+cancelled&state=st8", "st8")


def test_auth_exchange_writes_secret(fake_tt, fake_secrets):
    secret_id = auth.exchange_and_save(business_key="el-cuis", client_key="ck", client_secret="cs",
                                       code="ab*cd", redirect_uri=REDIRECT)

    saved = fake_secrets.store[secret_id]
    assert secret_id == "/ads-mcp/el-cuis/tiktok/config"
    assert saved["refresh_token"] == "rft.new" and saved["open_id"] == "oid" and saved["refresh_expires_at"]
    assert saved["client_key"] == "ck" and saved["client_secret"] == "cs"
    assert "access_token" not in saved
    sent = fake_tt.calls[0]["data"]
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "ab*cd" and sent["redirect_uri"] == REDIRECT


def test_auth_exchange_requires_video_upload_scope(fake_tt, fake_secrets):
    fake_tt.token_body["scope"] = "user.info.basic"

    with pytest.raises(ValueError, match="video.upload"):
        auth.exchange_and_save(business_key="el-cuis", client_key="ck", client_secret="cs",
                               code="c", redirect_uri=REDIRECT)
    assert fake_secrets.store == {}
