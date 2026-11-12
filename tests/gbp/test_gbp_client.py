from __future__ import annotations

from servers.gbp import gbp_client


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
