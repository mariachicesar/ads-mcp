from __future__ import annotations

import json

import pytest

from shared import media_queue
from shared.errors import AdsMcpError
from tests.fake_s3 import fake_s3  # noqa: F401  (fixture)


def _entry(**extra):
    return {"platform": "instagram", "businessKey": "el-cuis", "scheduledAt": "2026-11-12T20:00:00Z",
            "videoKey": "videos/el-cuis/abc.mp4", "videoSha256": "abc", "caption": "hola", "options": {}, **extra}


def test_enqueue_writes_pending_with_sortable_key(fake_s3):
    key = media_queue.enqueue(_entry())
    body = json.loads(fake_s3.objects[key])

    assert key.startswith("queue/pending/20261112T2000Z-")
    assert body["id"] and key.endswith(f"{body['id']}.json")
    assert body["attempts"] == 0 and body["lastError"] is None and body["result"] is None and body["createdAt"]


def test_put_video_dedupes_by_sha(fake_s3, tmp_path):
    f = tmp_path / "v.MP4"
    f.write_bytes(b"abc")

    k1 = media_queue.put_video(business_key="el-cuis", file_path=str(f), sha256="deadbeef")
    k2 = media_queue.put_video(business_key="el-cuis", file_path=str(f), sha256="deadbeef")

    assert k1 == k2 == "videos/el-cuis/deadbeef.mp4"
    assert fake_s3.puts == [k1]


def test_list_entries_filters_and_sorts(fake_s3):
    media_queue.enqueue(_entry(scheduledAt="2026-11-13T20:00:00Z"))
    media_queue.enqueue(_entry(scheduledAt="2026-11-12T20:00:00Z"))
    media_queue.enqueue(_entry(platform="tiktok"))
    media_queue.enqueue(_entry(businessKey="gq-painting"))

    rows = media_queue.list_entries(states=("pending",), business_key="el-cuis", platform="instagram")

    assert [r["scheduledAt"] for r in rows] == ["2026-11-12T20:00:00Z", "2026-11-13T20:00:00Z"]
    assert all(r["_state"] == "pending" and r["_key"].startswith("queue/pending/") for r in rows)


def test_move_applies_updates_and_deletes_source(fake_s3):
    key = media_queue.enqueue(_entry())
    new_key = media_queue.move(key, "done", updates={"result": {"mediaId": "m1"}})

    assert key not in fake_s3.objects
    assert new_key == key.replace("queue/pending/", "queue/done/")
    assert json.loads(fake_s3.objects[new_key])["result"] == {"mediaId": "m1"}


def test_move_missing_source_raises_keyerror(fake_s3):
    with pytest.raises(KeyError):
        media_queue.move("queue/pending/nope.json", "processing")


def test_move_rejects_unknown_state(fake_s3):
    key = media_queue.enqueue(_entry())
    with pytest.raises(ValueError):
        media_queue.move(key, "archived")


def test_presigned_get_uses_bucket_and_expiry(fake_s3):
    assert media_queue.presigned_get("videos/el-cuis/x.mp4", expires=600) == "https://s3.example/videos/el-cuis/x.mp4?expires=600"


def test_missing_bucket_env_raises(monkeypatch):
    monkeypatch.delenv("ADS_MCP_MEDIA_BUCKET", raising=False)
    with pytest.raises(AdsMcpError) as exc:
        media_queue.enqueue(_entry())
    assert exc.value.error_code == "INTERNAL_ERROR"
    assert "ADS_MCP_MEDIA_BUCKET" in exc.value.message


def test_claim_to_processing_is_exclusive_even_if_source_still_readable(fake_s3):
    key = media_queue.enqueue(_entry())
    processing_key = key.replace("queue/pending/", "queue/processing/")
    fake_s3.objects[processing_key] = fake_s3.objects[key]  # another runner already wrote its claim

    with pytest.raises(KeyError):
        media_queue.move(key, "processing")


def test_put_image_stores_jpeg_once(fake_s3, tmp_path):
    f = tmp_path / "frame.jpg"
    f.write_bytes(b"jpeg")
    calls = []
    real = fake_s3.upload_file

    def upload_file(Filename, Bucket, Key, ExtraArgs=None):
        calls.append(ExtraArgs)
        return real(Filename, Bucket, Key, ExtraArgs)

    fake_s3.upload_file = upload_file
    k1 = media_queue.put_image(business_key="el-cuis", file_path=str(f), sha256="cafe")
    k2 = media_queue.put_image(business_key="el-cuis", file_path=str(f), sha256="cafe")

    assert k1 == k2 == "images/el-cuis/cafe.jpg"
    assert fake_s3.objects[k1] == b"jpeg"
    assert calls == [{"ContentType": "image/jpeg"}]
