"""In-memory stand-in for the boto3 S3 client calls shared.media_queue makes."""

from __future__ import annotations

import io

import pytest


class _NoSuchKey(Exception):
    pass


class PreconditionFailed(Exception):
    response = {"Error": {"Code": "PreconditionFailed"}}


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.exceptions = type("Exceptions", (), {"NoSuchKey": _NoSuchKey})

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None, **kw):
        if IfNoneMatch == "*" and Key in self.objects:
            raise PreconditionFailed(Key)
        data = Body.read() if hasattr(Body, "read") else (Body.encode() if isinstance(Body, str) else Body)
        self.objects[Key] = data
        self.puts.append(Key)
        return {}

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        with open(Filename, "rb") as fh:
            self.put_object(Bucket=Bucket, Key=Key, Body=fh)

    def get_object(self, *, Bucket, Key, Range=None):
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        data = self.objects[Key]
        if Range:
            start, end = (int(x) for x in Range.removeprefix("bytes=").split("-"))
            data = data[start:end + 1]
        return {"Body": io.BytesIO(data), "ContentLength": len(data)}

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)
        return {}

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k, "Size": len(self.objects[k])} for k in keys], "IsTruncated": False}

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        return f"https://s3.example/{Params['Key']}?expires={ExpiresIn}"


@pytest.fixture
def fake_s3(monkeypatch):
    from shared import media_queue

    s3 = FakeS3()
    monkeypatch.setenv("ADS_MCP_MEDIA_BUCKET", "test-bucket")
    monkeypatch.setattr(media_queue, "_s3_client", lambda: s3)
    return s3
