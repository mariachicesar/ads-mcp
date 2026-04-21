from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send a signed ads-mcp request for local HMAC middleware smoke tests."
    )
    parser.add_argument("url", help="Full tool URL, e.g. http://127.0.0.1:8001/tools/list_accounts")
    parser.add_argument(
        "--body-file",
        help="Path to a JSON file for the request body. If omitted, a default read payload is used.",
    )
    parser.add_argument("--key-id", default="local-dev", help="Signing key ID header value.")
    parser.add_argument("--secret", required=True, help="Shared HMAC secret value.")
    parser.add_argument("--method", default="POST", help="HTTP method. Default: POST.")
    return parser


def load_body(body_file: str | None) -> bytes:
    if body_file:
        return Path(body_file).read_bytes()
    return json.dumps(
        {
            "tenantId": 1,
            "businessKey": "rnr-electrician",
            "requestedBy": 1,
            "payload": {},
        }
    ).encode("utf-8")


def canonical_request(*, method: str, path: str, timestamp: str, nonce: str, request_id: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    return "\n".join([method.upper(), path, timestamp, nonce, request_id, body_hash])


def main() -> int:
    args = build_parser().parse_args()
    body = load_body(args.body_file)

    timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    nonce = secrets.token_hex(16)
    request_id = f"smoke-{secrets.token_hex(8)}"
    split_url = urlsplit(args.url)
    path = split_url.path or "/"
    canonical = canonical_request(
        method=args.method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
        request_id=request_id,
        body=body,
    )
    signature = hmac.new(
        args.secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    request = Request(
        args.url,
        data=body,
        method=args.method,
        headers={
            "Content-Type": "application/json",
            "X-AdsMcp-Key-Id": args.key_id,
            "X-AdsMcp-Timestamp": timestamp,
            "X-AdsMcp-Nonce": nonce,
            "X-AdsMcp-Signature": signature,
            "X-Request-Id": request_id,
        },
    )

    try:
        with urlopen(request) as response:
            print(response.status)
            print(response.read().decode("utf-8"))
            return 0
    except HTTPError as exc:
        print(exc.code)
        print(exc.read().decode("utf-8"))
        return 1
    except URLError as exc:
        print(f"Connection failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())