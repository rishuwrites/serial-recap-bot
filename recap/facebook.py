"""Facebook Page video publishing through Meta's resumable upload API."""
import os
import re
import time

import requests

import config as S

GRAPH = "https://graph.facebook.com"
VIDEO_GRAPH = "https://graph-video.facebook.com"


class FacebookPublishError(RuntimeError):
    def __init__(self, message, *, uncertain=False, status_code=0):
        super().__init__(message)
        self.uncertain = uncertain
        self.status_code = status_code


def _message(response, secrets):
    try:
        body = response.json()
        error = body.get("error", body)
        message = error.get("message") or str(error)
        code = error.get("code")
        if code:
            message = f"Meta API error {code}: {message}"
    except (ValueError, AttributeError):
        message = response.text[:500] or f"HTTP {response.status_code}"
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:500]


def _json(response, secrets, *, uncertain_on_server_error=False):
    if response.status_code < 200 or response.status_code >= 300:
        raise FacebookPublishError(
            _message(response, secrets),
            uncertain=uncertain_on_server_error and response.status_code >= 500,
            status_code=response.status_code,
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise FacebookPublishError("Meta returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise FacebookPublishError("Meta returned an unexpected JSON response")
    if body.get("error"):
        raise FacebookPublishError(_message(response, secrets))
    return body


def _offset(session_id, user_token, version):
    response = requests.get(
        f"{GRAPH}/{version}/{session_id}",
        headers={"Authorization": f"OAuth {user_token}"},
        timeout=(15, 60),
    )
    body = _json(response, (user_token,))
    try:
        return int(body["file_offset"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FacebookPublishError("Meta upload status did not include a valid file_offset") from exc


def _upload_file(session_id, path, user_token, version):
    size = os.path.getsize(path)
    offset = 0
    for attempt in range(4):
        try:
            with open(path, "rb") as video:
                video.seek(offset)
                response = requests.post(
                    f"{GRAPH}/{version}/{session_id}",
                    headers={
                        "Authorization": f"OAuth {user_token}",
                        "file_offset": str(offset),
                        "Content-Type": "application/octet-stream",
                    },
                    data=video,
                    timeout=(30, 1800),
                )
            body = _json(response, (user_token,))
            if not body.get("h"):
                raise FacebookPublishError("Meta upload completed without a video file handle")
            return body["h"]
        except FacebookPublishError as exc:
            if exc.status_code not in (429,) and exc.status_code < 500:
                raise
            try:
                offset = _offset(session_id, user_token, version)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 3:
                    raise FacebookPublishError("Could not verify Facebook upload progress after a transient error",
                                               uncertain=True) from exc
                time.sleep(2 ** attempt)
                continue
            if offset >= size:
                raise FacebookPublishError("Facebook upload reached the end without returning its file handle",
                                           uncertain=True) from exc
            if attempt == 3:
                raise FacebookPublishError(f"Facebook upload did not complete; verified offset {offset} of {size}") from exc
            time.sleep(2 ** attempt)
        except (requests.Timeout, requests.ConnectionError):
            try:
                offset = _offset(session_id, user_token, version)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 3:
                    raise FacebookPublishError("Could not verify Facebook upload progress after a timeout", uncertain=True)
                time.sleep(2 ** attempt)
                continue
            if offset >= size:
                raise FacebookPublishError("Facebook upload reached the end but did not return a file handle", uncertain=True)
            if attempt == 3:
                raise FacebookPublishError(f"Facebook upload did not complete; verified offset {offset} of {size}")
            time.sleep(2 ** attempt)
    raise FacebookPublishError("Facebook resumable upload exhausted retries")


def publish_video(path, *, title="", description="", page_id=None, page_token=None,
                  app_id=None, user_token=None, version=None):
    page_id = page_id or os.getenv("FACEBOOK_PAGE_ID", "")
    page_token = page_token or os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
    app_id = app_id or os.getenv("META_APP_ID", "")
    user_token = user_token or os.getenv("META_USER_ACCESS_TOKEN", "")
    version = version or S.META_GRAPH_API_VERSION
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise FacebookPublishError("META_GRAPH_API_VERSION must look like v26.0")
    missing = [name for name, value in (
        ("FACEBOOK_PAGE_ID", page_id),
        ("FACEBOOK_PAGE_ACCESS_TOKEN", page_token),
        ("META_APP_ID", app_id),
        ("META_USER_ACCESS_TOKEN", user_token),
    ) if not value]
    if missing:
        raise FacebookPublishError("Missing required Facebook configuration: " + ", ".join(missing))
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise FacebookPublishError(f"Facebook video file is missing or empty: {path}")

    secrets = (page_token, user_token)
    size = os.path.getsize(path)
    try:
        start = requests.post(
            f"{GRAPH}/{version}/{app_id}/uploads",
            params={
                "file_name": os.path.basename(path),
                "file_length": str(size),
                "file_type": "video/mp4",
                "access_token": user_token,
            },
            timeout=(15, 60),
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise FacebookPublishError("Facebook upload-session creation timed out; check for an existing upload before retrying",
                                   uncertain=True) from exc
    start_body = _json(start, secrets)
    session_id = start_body.get("id")
    if not isinstance(session_id, str) or not session_id.startswith("upload:"):
        raise FacebookPublishError("Meta did not return a valid resumable upload session ID")

    handle = _upload_file(session_id, path, user_token, version)
    try:
        result = requests.post(
            f"{VIDEO_GRAPH}/{version}/{page_id}/videos",
            data={
                "access_token": page_token,
                "title": title,
                "description": description,
                "fbuploader_video_file_chunk": handle,
            },
            timeout=(15, 180),
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise FacebookPublishError("Facebook publish request timed out; the video may already be published",
                                   uncertain=True) from exc
    body = _json(result, secrets, uncertain_on_server_error=True)
    video_id = body.get("id")
    if not video_id:
        raise FacebookPublishError("Facebook publish response did not include a video ID")
    return video_id
