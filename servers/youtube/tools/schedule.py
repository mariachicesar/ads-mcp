"""YouTube scheduled uploads: dry run, upload as private with publishAt, list and cancel.

Execute uploads the local file right away (resumable upload) as a private video
with `publishAt` = the slot, so YouTube itself makes it public then. It also
enqueues a verify entry (platform "youtube", due slot + 60 min) that
scripts/run-scheduled-social.py uses to confirm the video actually went public.
The posting log ref is the YouTube videoId.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from shared import media_queue, scheduling, social_ledger, video_probe, youtube_client
from shared.caption_rules import check_caption, song_name, starts_with_song
from shared.errors import AdsMcpError
from shared.models import ToolRequest
from shared.responses import build_change, build_rule_check, build_success_response
from shared.social_profile import load_profile

SERVICE_NAME = "youtube"
PLATFORM = "youtube"
MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 5000
SHORTS_MAX_SECONDS = 180
CATEGORY_MUSIC = "10"
VERIFY_DELAY = timedelta(minutes=60)
REJECTED_UPLOAD = {"rejected", "failed"}
UPLOAD_RETRIES = 3
_TS = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _youtube(business_key: str, tool: str):
    """YouTube Data API client for the business (tests replace this)."""
    return youtube_client.data_client(youtube_client.load_config(business_key, tool))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _title_check(title: str, file_path: str) -> dict[str, Any]:
    song = song_name(file_path)
    if not title:
        fail = "YouTube title is missing."
    elif len(title) > MAX_TITLE_CHARS:
        fail = f"YouTube title is {len(title)} characters; YouTube allows {MAX_TITLE_CHARS}."
    elif "<" in title or ">" in title:
        fail = "YouTube title must not contain < or > (YouTube rejects them)."
    elif not starts_with_song(title, file_path):
        fail = f"YouTube title must start with the song name '{song}'."
    else:
        fail = ""
    return build_rule_check(rule="youtube-title", passed=not fail, severity="blocking" if fail else "info",
                            message=fail or f"Title starts with '{song}' and fits YouTube's limits.")


def _description(payload: dict[str, Any], tool: str) -> str:
    text = str(payload.get("description") or "").strip()
    if len(text) > MAX_DESCRIPTION_CHARS:
        raise scheduling.invalid(f"description is {len(text)} characters; YouTube allows {MAX_DESCRIPTION_CHARS}.", tool)
    if "<" in text or ">" in text:
        raise scheduling.invalid("description must not contain < or > (YouTube rejects them).", tool)
    return text


def _is_short(info: dict[str, Any] | None) -> bool | None:
    if not info:
        return None
    return info["height"] > info["width"] and info["seconds"] <= SHORTS_MAX_SECONDS


def _format_check(info: dict[str, Any] | None, short: bool | None) -> dict[str, Any]:
    if short is None:
        message = "Could not read the video format (ffprobe missing or failed); YouTube decides Short vs regular."
    else:
        kind = "a YouTube Short" if short else "a regular YouTube video"
        message = f"{info['width']}x{info['height']}, {info['seconds']:.0f} s: posts as {kind}."
    return build_rule_check(rule="youtube-format", passed=True, severity="info", message=message)


class UploadOutcomeUnknown(Exception):
    """The upload request went out and failed in a way that may still have created the video."""


def _is_definite(exc: Exception) -> bool:
    """A 4xx HttpError means YouTube refused the request, so no video was created."""
    status = getattr(getattr(exc, "resp", None), "status", None) if isinstance(exc, HttpError) else None
    try:
        return status is not None and 400 <= int(status) < 500
    except (TypeError, ValueError):
        return False


def _upload(yt, file_path: str, body: dict[str, Any]) -> str:
    """Resumable upload; returns the videoId.

    Errors before the first request (building the media/request) and 4xx refusals propagate as-is
    (definite: nothing was created). Anything else once the upload started raises
    UploadOutcomeUnknown, because YouTube may have finalized the video before the error.
    """
    media = MediaFileUpload(file_path, chunksize=-1, resumable=True)
    insert = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    try:
        while response is None:
            _, response = insert.next_chunk(num_retries=UPLOAD_RETRIES)
        return response["id"]
    except Exception as exc:  # noqa: BLE001
        if _is_definite(exc):
            raise
        raise UploadOutcomeUnknown(str(exc)[:300] or type(exc).__name__) from exc


def schedule_video(request: ToolRequest, request_id: str | None) -> dict:
    tool = "youtube_schedule_video"
    payload = request.payload
    if payload.get("fileUrl"):
        raise scheduling.invalid("YouTube scheduling needs a local filePath (it is uploaded from this machine).", tool)
    when = scheduling.parse_schedule_time(scheduling.required(payload, "scheduledTime", tool), tool)
    source, sha = scheduling.video_source(payload, tool)
    file_path = source["filePath"]
    title = str(payload.get("title") or "").strip()
    description = _description(payload, tool)
    info = video_probe.probe(file_path)
    short = _is_short(info)

    rule_checks = [scheduling.schedule_window_check(when, _now()), _title_check(title, file_path),
                   _format_check(info, short)]
    profile = load_profile(request.businessKey)
    if profile is not None:
        rule_checks.extend(check_caption(profile, description))

    publish_at = when.strftime(_TS)
    body = {
        "snippet": {"title": title, "description": description, "categoryId": CATEGORY_MUSIC},
        "status": {"privacyStatus": "private", "publishAt": publish_at, "selfDeclaredMadeForKids": False},
    }
    file = scheduling.file_preview(source, sha)
    changes = [build_change(field="youtube_video", label="Scheduled YouTube video", before=None,
                            after={**body, "file": file, "short": short}, resource_type="youtube_video")]

    def execute() -> dict[str, Any]:
        def upload_and_verify() -> dict[str, Any]:
            try:
                yt = _youtube(request.businessKey, tool)
                video_id = _upload(yt, file_path, body)
            except AdsMcpError:
                raise
            except UploadOutcomeUnknown as exc:
                # Must not reach run_and_mark's failure path: a "failed" log re-offers the song and
                # could upload it twice. Logged as queued with no ref instead; no verify entry.
                return {"videoId": None, "url": None, "verifyQueueKey": None, "short": short,
                        "outcomeUnknown": True,
                        "warning": (f"YouTube upload outcome unknown ({exc}); check YouTube Studio for a "
                                    f"private video scheduled at {scheduling.describe_time(when)} before "
                                    "re-scheduling. This song will not be re-offered automatically.")}
            except Exception as exc:  # noqa: BLE001
                raise youtube_client.upstream_error(tool, exc) from exc
            try:
                items = yt.videos().list(part="status", id=video_id).execute().get("items") or []
                upload_status = ((items[0].get("status") or {}) if items else {}).get("uploadStatus")
            except Exception:  # noqa: BLE001 - the upload succeeded; the runner verifies later
                upload_status = None
            if upload_status in REJECTED_UPLOAD:
                try:
                    yt.videos().delete(id=video_id).execute()
                except Exception:  # noqa: BLE001 - best effort; the video is private anyway
                    pass
                raise AdsMcpError(status_code=502, error_code="UPSTREAM_ERROR", tool=tool, retryable=False,
                                  message=f"YouTube {upload_status} the upload (video {video_id}); it was not scheduled.",
                                  details={"videoId": video_id, "uploadStatus": upload_status})
            result: dict[str, Any] = {"videoId": video_id, "url": f"https://youtu.be/{video_id}",
                                      "verifyQueueKey": None, "short": short}
            try:
                result["verifyQueueKey"] = media_queue.enqueue({
                    "platform": PLATFORM, "businessKey": request.businessKey,
                    "scheduledAt": (when + VERIFY_DELAY).strftime(_TS), "videoSha256": sha,
                    "videoId": video_id, "publishAt": publish_at,
                })
            except Exception as exc:  # noqa: BLE001 - never fail (and invite a re-upload) once YouTube has it
                result["verifyWarning"] = (f"Video is scheduled but the go-public check was not queued: {exc}. "
                                           "Check YouTube Studio after the slot.")
            return result

        return social_ledger.run_and_mark(
            request.businessKey, enabled=profile is not None, file_path=file_path, sha256=sha,
            platform=PLATFORM, scheduled_at=publish_at, call=upload_and_verify, ref_of=lambda r: r["videoId"])

    described = scheduling.describe_time(when)
    kind = {True: "as a Short", False: "as a regular video", None: "(format unknown)"}[short]
    response = scheduling.write_flow(
        request, request_id, service=SERVICE_NAME, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"file": file, "scheduledFor": described, "short": short, "upload": body},
        dry_summary=(f"Would upload '{title}' to YouTube now as a private video {kind} that YouTube makes "
                     f"public at {described}."),
        done_summary=f"Uploaded '{title}' to YouTube; it goes public at {described}.",
        execute=execute, bound=scheduling.bound_args(request, sha),
    )
    result = (response.get("data") or {}).get("result") or {}
    if result.get("outcomeUnknown"):
        response["summary"] = result["warning"]
        response.setdefault("warnings", []).append(result["warning"])
    elif result.get("verifyWarning"):
        response.setdefault("warnings", []).append(result["verifyWarning"])
    return response


def _queued_records(business_key: str) -> list[dict[str, Any]]:
    rows = []
    for key, song in social_ledger.load(business_key)["songs"].items():
        record = (song.get("platforms") or {}).get(PLATFORM) or {}
        if record.get("state") == "queued":
            # ref None = upload outcome unknown (logged queued so the song is not re-offered).
            rows.append({"songKey": key, "videoId": record.get("ref"), "scheduledAt": record.get("scheduledAt"),
                         "sha256": record.get("sha256")})
    return sorted(rows, key=lambda r: r.get("scheduledAt") or "")


def _fetch_videos(yt, ids: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        for item in yt.videos().list(part="status,snippet", id=",".join(chunk)).execute().get("items") or []:
            found[item["id"]] = item
    return found


def list_scheduled(request: ToolRequest, request_id: str | None) -> dict:
    tool = "youtube_list_scheduled"
    records = _queued_records(request.businessKey)
    videos: dict[str, dict[str, Any]] = {}
    ids = [r["videoId"] for r in records if r["videoId"]]
    if ids:
        try:
            videos = _fetch_videos(_youtube(request.businessKey, tool), ids)
        except AdsMcpError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise youtube_client.upstream_error(tool, exc) from exc
    rows = []
    for record in records:
        vid = record["videoId"]
        video = videos.get(vid) if vid else None
        status, snippet = (video or {}).get("status") or {}, (video or {}).get("snippet") or {}
        publish = _parse_ts(status.get("publishAt") or record.get("scheduledAt"))
        rows.append({
            "songKey": record["songKey"], "videoId": vid, "url": f"https://youtu.be/{vid}" if vid else None,
            "found": video is not None, "outcomeUnknown": not vid, "title": snippet.get("title"),
            "privacyStatus": status.get("privacyStatus"), "uploadStatus": status.get("uploadStatus"),
            "publishAt": status.get("publishAt") or record.get("scheduledAt"),
            "publishPacific": publish.astimezone(scheduling.SCHEDULE_TZ).isoformat() if publish else None,
        })
    unknown = sum(1 for r in rows if r["outcomeUnknown"])
    missing = sum(1 for r in rows if not r["found"]) - unknown
    summary = f"{len(rows)} YouTube video(s) in the posting log as queued."
    if unknown:
        summary += f" {unknown} with an unknown upload outcome (check YouTube Studio)."
    if missing:
        summary += f" {missing} not found on YouTube (deleted in YouTube Studio?)."
    return build_success_response(
        service=SERVICE_NAME, tool=tool, mode="read", business_key=request.businessKey, request_id=request_id,
        summary=summary, data={"videos": rows},
    )


def _verify_entry(business_key: str, video_id: str) -> dict[str, Any] | None:
    for entry in media_queue.list_entries(states=("pending",), business_key=business_key, platform=PLATFORM):
        if entry.get("videoId") == video_id:
            return entry
    return None


def cancel_scheduled(request: ToolRequest, request_id: str | None) -> dict:
    tool = "youtube_cancel_scheduled"
    video_id = str(scheduling.required(request.payload, "videoId", tool)).strip()
    try:
        yt = _youtube(request.businessKey, tool)
        items = yt.videos().list(part="status,snippet", id=video_id).execute().get("items") or []
    except AdsMcpError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise youtube_client.upstream_error(tool, exc) from exc
    if not items:
        raise scheduling.invalid(f"No YouTube video with id {video_id} on this channel.", tool)
    status, snippet = items[0].get("status") or {}, items[0].get("snippet") or {}
    publish = _parse_ts(status.get("publishAt"))
    ok = status.get("privacyStatus") == "private" and publish is not None and publish > _now()
    rule_checks = [build_rule_check(
        rule="video-is-scheduled", passed=ok, severity="info" if ok else "blocking",
        message=(f"Video {video_id} is private and scheduled for {scheduling.describe_time(publish)}." if ok else
                 f"Video {video_id} is {status.get('privacyStatus')} with publishAt {status.get('publishAt')}; "
                 "only a private video whose publish time is still ahead can be cancelled here."),
    )]
    changes = [build_change(field="youtube_video", label="Delete scheduled YouTube video",
                            before={"title": snippet.get("title"), "privacyStatus": status.get("privacyStatus"),
                                    "publishAt": status.get("publishAt")},
                            after=None, resource_type="youtube_video", resource_id=video_id)]

    def execute() -> dict[str, Any]:
        try:
            yt.videos().delete(id=video_id).execute()
        except Exception as exc:  # noqa: BLE001
            raise youtube_client.upstream_error(tool, exc) from exc
        result: dict[str, Any] = {"videoId": video_id, "deleted": True, "verifyQueueKey": None}
        entry = _verify_entry(request.businessKey, video_id)
        if entry is not None:
            try:
                result["verifyQueueKey"] = media_queue.move(entry["_key"], "cancelled")
            except KeyError:
                pass  # the runner already picked it up; it will find the video gone
        if load_profile(request.businessKey) is not None:
            record = next((r for r in _queued_records(request.businessKey) if r["videoId"] == video_id), None)
            sha = (record or {}).get("sha256") or (entry or {}).get("videoSha256")
            warning = social_ledger.mark_cancelled(
                request.businessKey, sha256=sha, platform=PLATFORM,
                scheduled_at=(record or {}).get("scheduledAt") or status.get("publishAt"), ref=video_id)
            if warning:
                result["ledgerWarning"] = warning
        return result

    title = snippet.get("title") or video_id
    return scheduling.write_flow(
        request, request_id, service=SERVICE_NAME, tool=tool, rule_checks=rule_checks, changes=changes,
        preview={"videoId": video_id, "title": snippet.get("title"), "status": status},
        dry_summary=f"Would delete the private YouTube video '{title}' before it goes public.",
        done_summary=f"Deleted the scheduled YouTube video '{title}'.",
        execute=execute,
    )
