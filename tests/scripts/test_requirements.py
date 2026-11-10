from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_meta_ads_boto3_supports_s3_conditional_writes():
    # put_object(IfNoneMatch="*") — used to claim queue entries — needs a recent botocore.
    reqs = (ROOT / "servers" / "meta-ads" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "boto3>=1.42.0" in [line.split("#")[0].strip() for line in reqs]
