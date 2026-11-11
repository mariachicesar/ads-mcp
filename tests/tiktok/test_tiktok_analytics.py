from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.fake_tiktok import FakeResponse, fake_tt  # noqa: F401  (fixture)
from tests.service_import import load_service_module

SECRET_ID = "/ads-mcp/el-cuis/tiktok/config"
CONFIG = {"client_key": "ck", "client_secret": "cs", "refresh_token": "rft.old", "open_id": "oid"}
NOW = datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc)


def _ts(day: int) -> int:
    return int(datetime(2026, 11, day, 20, 0, tzinfo=timezone.utc).timestamp())


def _video(i, day, views, likes=0, comments=0, shares=0):
    return {"id": f"v{i}", "title": f"Hook {i}\nmore text", "create_time": _ts(day), "duration": 15,
            "view_count": views, "like_count": likes, "comment_count": comments, "share_count": shares,
            "share_url": f"https://www.tiktok.com/@mariachielcuis/video/v{i}"}


class StatsTikTok:
    """fake_tt plus the Display API endpoints."""

    def __init__(self, base):
        self.base, self.pages, self.user, self.error = base, [], {}, None
        self.calls = base.calls

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        if "/v2/video/list/" in url:
            self.calls.append({"method": "POST", "url": url, "json": json, "headers": headers})
            if self.error:
                return FakeResponse(403, {"error": {"code": self.error, "message": "no"}})
            page = self.pages.pop(0)
            return FakeResponse(200, {"data": page, "error": {"code": "ok", "message": ""}})
        return self.base.post(url, data=data, json=json, headers=headers, timeout=timeout)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "params": params, "headers": headers})
        return FakeResponse(200, {"data": {"user": self.user}, "error": {"code": "ok", "message": ""}})


@pytest.fixture
def env(monkeypatch, fake_tt, fake_secrets):
    from shared import tiktok_client

    fake = StatsTikTok(fake_tt)
    monkeypatch.setattr(tiktok_client, "requests", fake)
    fake_secrets.store[SECRET_ID] = dict(CONFIG)
    mod = load_service_module("tiktok", "tools.analytics")
    monkeypatch.setattr(mod, "_now", lambda: NOW)
    return {"tt": fake, "secrets": fake_secrets, "mod": mod}


def _req(**payload):
    return ToolRequest(businessKey="el-cuis", payload=payload)


def test_video_stats_paginates_and_totals(env):
    env["tt"].pages = [
        {"videos": [_video(1, 10, 290, 8, 0, 2), _video(2, 9, 250, 5, 2)], "cursor": 111, "has_more": True},
        {"videos": [_video(3, 1, 100, 1)], "cursor": 0, "has_more": False},
    ]

    r = env["mod"].get_video_stats(_req(limit=20), None)

    assert r["ok"] is True
    d = r["data"]
    assert d["totals"] == {"views": 640, "likes": 14, "comments": 2, "shares": 2, "videos": 3, "avgViewsPerVideo": 213.3}
    first = d["videos"][0]
    assert first["hook"] == "Hook 1" and first["engagementRatePct"] == 3.45
    assert first["publishedPacific"].startswith("2026-11-10T12:00:00-08:00")
    lists = [c for c in env["tt"].calls if "/video/list/" in c["url"]]
    assert "view_count" in lists[0]["url"] and lists[1]["json"]["cursor"] == 111
    assert all(c["headers"]["Authorization"] == "Bearer act.new" for c in lists)


def test_video_stats_days_filter(env):
    env["tt"].pages = [{"videos": [_video(1, 10, 290), _video(2, 1, 100)], "has_more": False}]

    d = env["mod"].get_video_stats(_req(days=7, limit=20), None)["data"]

    assert [v["id"] for v in d["videos"]] == ["v1"]
    assert d["window"] == "last 7 days"


def test_limit_caps_page_size(env):
    env["tt"].pages = [{"videos": [_video(1, 10, 5), _video(2, 9, 5)], "has_more": True, "cursor": 5}]

    d = env["mod"].get_video_stats(_req(limit=2), None)["data"]

    assert d["totals"]["videos"] == 2
    assert [c["json"]["max_count"] for c in env["tt"].calls if "/video/list/" in c["url"]] == [2]


def test_rotated_refresh_token_saved_and_no_tokens_returned(env):
    env["tt"].pages = [{"videos": [], "has_more": False}]

    r = env["mod"].get_video_stats(_req(), None)

    assert env["secrets"].store[SECRET_ID]["refresh_token"] == "rft.new"
    assert "act.new" not in str(r) and "rft" not in str(r)


def test_missing_scope_gives_reconnect_hint(env):
    env["tt"].error = "scope_not_authorized"

    with pytest.raises(AdsMcpError) as exc:
        env["mod"].get_video_stats(_req(), None)

    assert "tiktok-auth.py" in exc.value.message


def test_not_connected(env):
    env["secrets"].store.pop(SECRET_ID)

    with pytest.raises(AdsMcpError) as exc:
        env["mod"].get_account_stats(_req(), None)

    assert exc.value.error_code == "PLATFORM_CONFIG_INCOMPLETE"


def test_account_stats(env):
    env["tt"].user = {"display_name": "Mariachi El Cuis", "follower_count": 120,
                      "following_count": 10, "likes_count": 900, "video_count": 33}

    r = env["mod"].get_account_stats(_req(), None)

    assert r["data"]["followers"] == 120 and r["data"]["videoCount"] == 33
    get = [c for c in env["tt"].calls if c["method"] == "GET"][0]
    assert "follower_count" in get["params"]["fields"] and "username" not in get["params"]["fields"]


def test_invalid_days(env):
    with pytest.raises(AdsMcpError):
        env["mod"].get_video_stats(_req(days=0), None)
