from __future__ import annotations

import json
from datetime import date

import pytest

from shared.errors import AdsMcpError
from shared.models import ToolRequest
from tests.service_import import load_service_module

CONFIG = {"client_id": "id", "client_secret": "secret", "refresh_token": "rt"}


class _Call:
    def __init__(self, result, log, name, kwargs):
        self.result, self.log, self.name, self.kwargs = result, log, name, kwargs

    def execute(self):
        self.log.append((self.name, self.kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _Resource:
    def __init__(self, api, name):
        self.api, self.name = api, name

    def list(self, **kwargs):
        return _Call(self.api.results[self.name], self.api.log, self.name, kwargs)

    def query(self, **kwargs):
        return _Call(self.api.results[self.name], self.api.log, self.name, kwargs)


class FakeApi:
    def __init__(self, **results):
        self.results, self.log = results, []

    def __getattr__(self, name):
        if name in ("results", "log"):
            raise AttributeError(name)
        return lambda: _Resource(self, name)


CHANNEL = {"items": [{"id": "UC1", "snippet": {"title": "Mariachi El Cuis"},
                      "statistics": {"subscriberCount": "42", "viewCount": "5000", "videoCount": "12"},
                      "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}


@pytest.fixture
def yt(monkeypatch):
    monkeypatch.setenv("ADS_MCP_YOUTUBE_CONFIGS_JSON", json.dumps({"el-cuis": CONFIG}))
    mod = load_service_module("youtube", "tools.read")
    data, analytics = FakeApi(channels=CHANNEL), FakeApi()
    monkeypatch.setattr(mod, "_build_clients", lambda config: (data, analytics))
    monkeypatch.setattr(mod, "_today", lambda: date(2026, 11, 11))
    return {"mod": mod, "data": data, "analytics": analytics}


def _req(**payload):
    return ToolRequest(businessKey="el-cuis", payload=payload)


def test_channel_overview_uses_own_channel(yt):
    r = yt["mod"].get_channel_overview(_req(), None)

    assert r["data"] == {"channelId": "UC1", "title": "Mariachi El Cuis", "subscribers": 42,
                         "totalViews": 5000, "videoCount": 12}
    assert yt["data"].log[0] == ("channels", {"part": "snippet,statistics,contentDetails", "mine": True})


def test_channel_id_from_config(yt, monkeypatch):
    monkeypatch.setenv("ADS_MCP_YOUTUBE_CONFIGS_JSON", json.dumps({"el-cuis": {**CONFIG, "channel_id": "UC9"}}))

    yt["mod"].get_channel_overview(_req(), None)

    assert yt["data"].log[0][1]["id"] == "UC9" and "mine" not in yt["data"].log[0][1]


def test_recent_videos_rows_and_engagement(yt):
    yt["data"].results["playlistItems"] = {"items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {"videoId": "b"}}]}
    yt["data"].results["videos"] = {"items": [
        {"id": "a", "snippet": {"title": "Serenata", "publishedAt": "2026-11-09T02:00:00Z"},
         "statistics": {"viewCount": "200", "likeCount": "9", "commentCount": "1"}, "contentDetails": {"duration": "PT15S"}},
        {"id": "b", "snippet": {"title": "Boda"}, "statistics": {}},
    ]}

    r = yt["mod"].get_recent_videos(_req(limit=5), None)

    rows = r["data"]["videos"]
    assert rows[0]["views"] == 200 and rows[0]["engagementRatePct"] == 5.0
    assert rows[0]["url"] == "https://www.youtube.com/watch?v=a"
    assert rows[1]["views"] == 0 and rows[1]["engagementRatePct"] == 0.0
    names = dict(yt["data"].log)
    assert names["playlistItems"]["playlistId"] == "UU1" and names["playlistItems"]["maxResults"] == 5
    assert names["videos"]["id"] == "a,b"


def test_recent_videos_empty_channel_skips_videos_call(yt):
    yt["data"].results["playlistItems"] = {"items": []}

    r = yt["mod"].get_recent_videos(_req(), None)

    assert r["data"]["videos"] == [] and "videos" not in dict(yt["data"].log)


def test_analytics_by_traffic_source(yt):
    yt["analytics"].results["reports"] = {
        "columnHeaders": [{"name": "insightTrafficSourceType"}, {"name": "views"}, {"name": "estimatedMinutesWatched"}],
        "rows": [["YT_SEARCH", 30, 12], ["SHORTS", 70, 20]],
    }

    r = yt["mod"].get_analytics(_req(dateRange="LAST_7_DAYS", dimension="traffic_source"), None)

    q = yt["analytics"].log[0][1]
    assert q["ids"] == "channel==MINE" and q["dimensions"] == "insightTrafficSourceType" and q["sort"] == "-views"
    assert q["metrics"] == "views,estimatedMinutesWatched,averageViewDuration"
    assert (q["startDate"], q["endDate"]) == ("2026-11-03", "2026-11-09")
    d = r["data"]
    assert d["rows"][0] == {"traffic_source": "YT_SEARCH", "views": 30, "estimatedMinutesWatched": 12}
    assert d["totals"]["views"] == 100 and d["totals"]["estimatedMinutesWatched"] == 32


def test_analytics_by_day_sorted_by_day(yt):
    yt["analytics"].results["reports"] = {"columnHeaders": [{"name": "day"}, {"name": "views"}], "rows": []}

    yt["mod"].get_analytics(_req(), None)

    q = yt["analytics"].log[0][1]
    assert q["sort"] == "day" and "maxResults" not in q
    assert "likes" in q["metrics"]


@pytest.mark.parametrize("payload", [{"dimension": "city"}, {"dateRange": "YESTERDAY"}])
def test_analytics_rejects_bad_input(yt, payload):
    with pytest.raises(AdsMcpError) as exc:
        yt["mod"].get_analytics(_req(**payload), None)
    assert exc.value.error_code == "REQUEST_INVALID"


def test_api_disabled_gives_hint(yt):
    yt["analytics"].results["reports"] = RuntimeError("YouTube Analytics API has not been used in project 123")

    with pytest.raises(AdsMcpError) as exc:
        yt["mod"].get_analytics(_req(), None)

    assert exc.value.error_code == "UPSTREAM_ERROR" and "Enable the YouTube" in exc.value.message


def test_missing_config(yt, monkeypatch):
    monkeypatch.setenv("ADS_MCP_YOUTUBE_CONFIGS_JSON", json.dumps({"el-cuis": {"client_id": "id"}}))

    with pytest.raises(AdsMcpError):
        yt["mod"].get_channel_overview(_req(), None)


def test_auth_config_builder():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("yt_auth", Path(__file__).parents[2] / "scripts" / "youtube-auth.py")
    auth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auth)

    assert auth.build_config("i", "s", "r", None) == {"client_id": "i", "client_secret": "s", "refresh_token": "r"}
    assert auth.build_config("i", "s", "r", "UC1")["channel_id"] == "UC1"
