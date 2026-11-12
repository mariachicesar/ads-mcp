from __future__ import annotations

import pytest

from servers.gbp import gbp_client
from shared.errors import AdsMcpError


def _capture(monkeypatch):
    sent = {}

    def fake(method, path, config, *, body=None, params=None, tool=None):
        sent["body"] = body
        return {"name": "n"}

    monkeypatch.setattr(gbp_client, "_v4_request", fake)
    return sent


CONFIG = {"gbp_account_id": "1", "gbp_location_id": "2"}


def test_create_post_with_media_url_adds_one_photo(monkeypatch):
    sent = _capture(monkeypatch)
    gbp_client.create_post(CONFIG, summary="hola", call_to_action_type="CALL", media_url="https://x/y.jpg",
                           language_code="es")
    assert sent["body"]["media"] == [{"mediaFormat": "PHOTO", "sourceUrl": "https://x/y.jpg"}]
    assert sent["body"]["callToAction"] == {"actionType": "CALL"}


def test_create_post_without_media_url_body_unchanged(monkeypatch):
    sent = _capture(monkeypatch)
    gbp_client.create_post(CONFIG, summary="hola", language_code="es")
    assert "media" not in sent["body"]


def test_access_token_refresh_failure_is_retryable_auth_error(monkeypatch):
    class FailingCreds:
        def refresh(self, request):
            raise RuntimeError("token endpoint unreachable")

    monkeypatch.setattr(gbp_client, "_build_credentials", lambda config, tool=None: FailingCreds())

    with pytest.raises(AdsMcpError) as err:
        gbp_client._access_token({"client_id": "i", "client_secret": "s", "refresh_token": "r"}, tool="t")

    # Raised before any API request, so callers can distinguish it from an in-flight POST failure.
    assert err.value.error_code == "UPSTREAM_AUTH_ERROR" and err.value.retryable
    assert "token endpoint unreachable" in err.value.message
