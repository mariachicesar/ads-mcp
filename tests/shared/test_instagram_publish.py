from __future__ import annotations

import pytest

from shared import instagram_publish
from shared.errors import AdsMcpError

CONFIG = {"access_token": "tok", "app_secret": "sec"}


@pytest.fixture
def fake_graph(monkeypatch):
    graph = {"calls": [], "statuses": ["IN_PROGRESS", "FINISHED"]}

    def graph_request(config, method, path, *, params=None, data=None, tool=None, **kw):
        graph["calls"].append((method, path, dict(data or params or {})))
        if method == "POST" and path.endswith("/media"):
            return {"id": "cont1"}
        if method == "GET" and path == "cont1":
            return {"status_code": graph["statuses"].pop(0)}
        if method == "POST" and path.endswith("/media_publish"):
            return {"id": "media1"}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(instagram_publish, "_graph_request", graph_request)
    return graph


def _publish(**kw):
    return instagram_publish.publish_reel(CONFIG, ig_user_id="178", video_url="https://s3.example/v.mp4",
                                          caption="hola", share_to_feed=True, sleep=lambda s: None, **kw)


def test_publish_reel_polls_until_finished_then_publishes(fake_graph):
    assert _publish() == {"containerId": "cont1", "mediaId": "media1"}

    method, path, fields = fake_graph["calls"][0]
    assert (method, path) == ("POST", "178/media")
    assert fields == {"media_type": "REELS", "video_url": "https://s3.example/v.mp4", "caption": "hola", "share_to_feed": True}
    assert fake_graph["calls"][-1] == ("POST", "178/media_publish", {"creation_id": "cont1"})


def test_container_error_fails_without_publish(fake_graph):
    fake_graph["statuses"] = ["ERROR"]

    with pytest.raises(AdsMcpError) as exc:
        _publish()

    assert exc.value.details["containerStatus"] == "ERROR"
    assert not any(path.endswith("/media_publish") for _, path, _ in fake_graph["calls"])


def test_container_timeout_fails(fake_graph):
    fake_graph["statuses"] = ["IN_PROGRESS"] * 10

    with pytest.raises(AdsMcpError) as exc:
        _publish(poll_seconds=10, timeout_seconds=30)

    assert exc.value.details["containerStatus"] == "IN_PROGRESS"
