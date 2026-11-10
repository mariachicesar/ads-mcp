from __future__ import annotations

import pytest

from shared import secrets, tiktok_client
from shared.errors import AdsMcpError
from tests.fake_tiktok import UPLOAD_URL, fake_tt  # noqa: F401  (fixture)

MIB = 1024 * 1024
SECRET_ID = "/ads-mcp/el-cuis/tiktok/config"
CONFIG = {"client_key": "ck", "client_secret": "cs", "refresh_token": "rft.old", "open_id": "oid",
          "refresh_expires_at": "2027-11-10T00:00:00Z"}


def test_chunk_plan_single_chunk_under_64mib():
    assert tiktok_client.chunk_plan(5) == [(0, 4)]
    assert tiktok_client.chunk_plan(64 * MIB) == [(0, 64 * MIB - 1)]


def test_chunk_plan_last_chunk_absorbs_remainder():
    size = 100 * MIB + 3
    plan = tiktok_client.chunk_plan(size)

    assert len(plan) == 10
    assert plan[0] == (0, 10 * MIB - 1)
    assert plan[-1] == (90 * MIB, size - 1)
    assert all(plan[i][1] + 1 == plan[i + 1][0] for i in range(len(plan) - 1))


def test_chunk_plan_rejects_empty():
    with pytest.raises(AdsMcpError):
        tiktok_client.chunk_plan(0)


def test_refresh_writes_back_rotated_token(fake_tt, fake_secrets):
    token = tiktok_client.refresh_access_token(dict(CONFIG), secret_id=SECRET_ID)

    assert token == "act.new"
    saved = fake_secrets.store[SECRET_ID]
    assert saved["refresh_token"] == "rft.new" and saved["client_key"] == "ck"
    assert "access_token" not in saved
    sent = fake_tt.calls[0]["data"]
    assert sent["grant_type"] == "refresh_token" and sent["refresh_token"] == "rft.old"


def test_refresh_unchanged_token_skips_write(fake_tt, fake_secrets):
    fake_tt.token_body["refresh_token"] = "rft.old"

    tiktok_client.refresh_access_token(dict(CONFIG), secret_id=SECRET_ID)

    assert SECRET_ID not in fake_secrets.store


def test_refresh_writeback_failure_aborts_publish(fake_tt, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("secrets manager down")
    monkeypatch.setattr(secrets, "update_secret", boom)

    with pytest.raises(AdsMcpError) as exc:
        tiktok_client.refresh_access_token(dict(CONFIG), secret_id=SECRET_ID)

    assert exc.value.retryable
    assert "act.new" not in str(exc.value.details) and "rft.new" not in str(exc.value.details)


def test_refresh_rejected_needs_reauth(fake_tt):
    fake_tt.token_body = {"error": "invalid_grant", "error_description": "Refresh token is invalid or expired."}

    with pytest.raises(AdsMcpError) as exc:
        tiktok_client.refresh_access_token(dict(CONFIG), secret_id=SECRET_ID)

    assert not exc.value.retryable and "tiktok-auth.py" in exc.value.message


def test_send_to_inbox_puts_chunks_with_content_range(fake_tt):
    size = 70 * MIB + 5
    data = bytes(size)
    reads = []

    def read_range(start, end):
        reads.append((start, end))
        return data[start:end + 1]

    result = tiktok_client.send_to_inbox("act.x", size=size, read_range=read_range, sleep=lambda s: None)

    assert result == {"publishId": "v_inbox_file~v2.123", "status": "SEND_TO_USER_INBOX"}
    init = fake_tt.calls[0]
    assert init["headers"]["Authorization"] == "Bearer act.x"
    assert init["json"]["source_info"] == {"source": "FILE_UPLOAD", "video_size": size,
                                           "chunk_size": 10 * MIB, "total_chunk_count": 7}
    puts = [c for c in fake_tt.calls if c["method"] == "PUT"]
    assert [p["headers"]["Content-Range"] for p in puts][0] == f"bytes 0-{10 * MIB - 1}/{size}"
    assert puts[-1]["headers"]["Content-Range"] == f"bytes {60 * MIB}-{size - 1}/{size}"
    assert all(p["url"] == UPLOAD_URL and p["headers"]["Content-Type"] == "video/mp4" for p in puts)
    assert sum(p["len"] for p in puts) == size


def test_send_to_inbox_polls_until_inbox(fake_tt):
    fake_tt.statuses = ["PROCESSING_UPLOAD", "PROCESSING_UPLOAD", "SEND_TO_USER_INBOX"]
    sleeps = []

    result = tiktok_client.send_to_inbox("act.x", size=3, read_range=lambda s, e: b"abc", sleep=sleeps.append)

    assert result["status"] == "SEND_TO_USER_INBOX" and sleeps == [10, 10]


def test_send_to_inbox_failed_status_is_not_retryable(fake_tt):
    fake_tt.statuses, fake_tt.fail_reason = ["FAILED"], "file_format_check_failed"

    with pytest.raises(AdsMcpError) as exc:
        tiktok_client.send_to_inbox("act.x", size=3, read_range=lambda s, e: b"abc", sleep=lambda s: None)

    assert not exc.value.retryable and "file_format_check_failed" in exc.value.message


def test_send_to_inbox_init_error_surfaces_tiktok_code(fake_tt):
    fake_tt.init_body = {"error": {"code": "spam_risk_too_many_pending_share", "message": "too many", "log_id": "l"}}

    with pytest.raises(AdsMcpError) as exc:
        tiktok_client.send_to_inbox("act.x", size=3, read_range=lambda s, e: b"abc", sleep=lambda s: None)

    assert "spam_risk_too_many_pending_share" in exc.value.message
    assert not any(c["method"] == "PUT" for c in fake_tt.calls)


def test_refresh_writeback_uses_update_not_create(fake_tt, monkeypatch):
    updates = []

    def no_create(*a, **kw):
        raise AssertionError("runner role has PutSecretValue only; must not call put_secret (create)")
    monkeypatch.setattr(secrets, "put_secret", no_create)
    monkeypatch.setattr(secrets, "update_secret", lambda sid, value, settings: updates.append((sid, value)))

    assert tiktok_client.refresh_access_token(dict(CONFIG), secret_id=SECRET_ID) == "act.new"
    assert updates and updates[0][0] == SECRET_ID and updates[0][1]["refresh_token"] == "rft.new"
