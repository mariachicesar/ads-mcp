from __future__ import annotations

import configparser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = ROOT / "infrastructure" / "systemd"
BUCKET = "amz-s3-videos-ads-mcp-009959434008-us-east-1-an"


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string((SYSTEMD / name).read_text(encoding="utf-8"))
    return parser



def test_social_timer_runs_every_30_minutes_and_catches_up():
    timer = _unit("ads-mcp-social.timer")
    assert timer.get("Timer", "OnCalendar") == "*:00/30"
    assert timer.get("Timer", "Persistent") == "true"
    assert timer.get("Install", "WantedBy") == "timers.target"


def test_social_service_is_locked_oneshot_with_region_and_bucket():
    text = (SYSTEMD / "ads-mcp-social.service").read_text(encoding="utf-8")
    service = _unit("ads-mcp-social.service")
    assert service.get("Service", "Type") == "oneshot"
    assert service.get("Service", "ExecStart").startswith("/usr/bin/flock -n /tmp/ads-mcp-social.lock ")
    assert service.get("Service", "ExecStart").endswith("scripts/run-scheduled-social.py")
    assert "Environment=AWS_REGION=us-east-1" in text
    assert f"Environment=ADS_MCP_MEDIA_BUCKET={BUCKET}" in text


def test_meta_ads_service_knows_the_media_bucket():
    text = (SYSTEMD / "meta-ads-mcp.service").read_text(encoding="utf-8")
    assert f"Environment=ADS_MCP_MEDIA_BUCKET={BUCKET}" in text


def test_deploy_installs_and_enables_timers():
    deploy = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "infrastructure/systemd/*.timer" in deploy
    assert "enable --now ads-mcp-social.timer" in deploy


def test_local_stdio_passes_media_bucket_and_instagram_id():
    text = (ROOT / "servers" / "meta-ads" / "local_stdio.py").read_text(encoding="utf-8")
    assert "ADS_MCP_MEDIA_BUCKET" in text and "META_INSTAGRAM_USER_ID" in text
