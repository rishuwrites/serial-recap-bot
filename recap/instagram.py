"""Instagram Reels publishing through the Facebook Login resumable-upload flow."""
import json
import os
import re
import subprocess
import time
from fractions import Fraction
from urllib.parse import urlsplit

import requests

import config as S

GRAPH = "https://graph.facebook.com"
UPLOAD = "https://rupload.facebook.com"


class InstagramPublishError(RuntimeError):
    def __init__(self, message, *, uncertain=False, status_code=0):
        super().__init__(message)
        self.uncertain = uncertain
        self.status_code = status_code


def _message(response, token):
    try:
        body = response.json()
        error = body.get("error", body)
        message = error.get("message") or str(error)
        code = error.get("code")
        if code:
            message = f"Meta API error {code}: {message}"
    except (ValueError, AttributeError):
        message = response.text[:500] or f"HTTP {response.status_code}"
    if token:
        message = message.replace(token, "[REDACTED]")
    return message[:500]


def _json(response, token, *, uncertain_on_server_error=False):
    if response.status_code < 200 or response.status_code >= 300:
        raise InstagramPublishError(
            _message(response, token),
            uncertain=uncertain_on_server_error and response.status_code >= 500,
            status_code=response.status_code,
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise InstagramPublishError("Meta returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise InstagramPublishError("Meta returned an unexpected JSON response")
    if body.get("error"):
        raise InstagramPublishError(_message(response, token))
    return body


def _get_json(url, token):
    for attempt in range(4):
        try:
            response = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=(15, 45),
            )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
            return _json(response, token)
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise InstagramPublishError("Meta status polling exhausted retries")


def validate_video(path):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise InstagramPublishError(f"Instagram video file is missing or empty: {path}")
    if os.path.getsize(path) > 300 * 1024 * 1024:
        raise InstagramPublishError("Instagram Reel exceeds Meta's 300 MB file-size limit")
    try:
        output = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path,
        ], text=True, stderr=subprocess.STDOUT)
        probe = json.loads(output)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise InstagramPublishError(f"Could not inspect Instagram video with ffprobe: {exc}") from exc
    streams = probe.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if not video or not audio:
        raise InstagramPublishError("Instagram Reel must contain both video and audio")
    if (video.get("codec_name") != "h264" or video.get("width") != 1080
            or video.get("height") != 1920 or video.get("pix_fmt") not in ("yuv420p", "yuvj420p")):
        actual = (
            f"{video.get('codec_name')} {video.get('pix_fmt')} "
            f"{video.get('width')}x{video.get('height')}"
        )
        raise InstagramPublishError(f"Instagram video must be H.264 4:2:0 at 1080x1920; got {actual}")
    try:
        fps = float(Fraction(video.get("avg_frame_rate", "0/1")))
        duration = float(probe.get("format", {}).get("duration", 0))
        sample_rate = int(audio.get("sample_rate", 0))
        channels = int(audio.get("channels", 0))
        audio_bitrate = int(audio.get("bit_rate", 0))
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise InstagramPublishError("Instagram video has invalid stream metadata") from exc
    if not 23 <= fps <= 60:
        raise InstagramPublishError(f"Instagram Reel frame rate must be 23-60 FPS; got {fps:g}")
    if (audio.get("codec_name") != "aac" or sample_rate > 48000 or channels not in (1, 2)
            or audio_bitrate > 128_000):
        raise InstagramPublishError("Instagram Reel audio must be AAC, at most 48 kHz and 128 kbps, mono or stereo")
    if not 3 <= duration <= 900:
        raise InstagramPublishError(f"Instagram Reel duration must be 3 seconds to 15 minutes; got {duration:.2f}s")
    try:
        bitrate = int(video.get("bit_rate", 0))
    except (TypeError, ValueError):
        bitrate = 0
    if bitrate > 25_000_000:
        raise InstagramPublishError("Instagram Reel video bitrate exceeds Meta's 25 Mbps limit")
    return duration


def publish_reel(path, caption="", *, user_id=None, access_token=None, version=None,
                 poll_interval=5, max_polls=60):
    user_id = user_id or os.getenv("INSTAGRAM_USER_ID", "")
    access_token = access_token or os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    version = version or S.META_GRAPH_API_VERSION
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise InstagramPublishError("META_GRAPH_API_VERSION must look like v26.0")
    missing = [name for name, value in (
        ("INSTAGRAM_USER_ID", user_id),
        ("INSTAGRAM_ACCESS_TOKEN", access_token),
    ) if not value]
    if missing:
        raise InstagramPublishError("Missing required Instagram configuration: " + ", ".join(missing))
    if len(caption) > 2200:
        raise InstagramPublishError("Instagram caption exceeds the 2,200-character API limit")
    duration = validate_video(path)
    print(f"Instagram Reel validated: 1080x1920, {duration:.1f}s, {os.path.getsize(path)} bytes")

    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        created = requests.post(
            f"{GRAPH}/{version}/{user_id}/media",
            data={"media_type": "REELS", "upload_type": "resumable", "caption": caption},
            headers=headers,
            timeout=(15, 60),
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise InstagramPublishError("Instagram container creation timed out; no publish was attempted",
                                    uncertain=True) from exc
    container_body = _json(created, access_token)
    container = container_body.get("id")
    if not container:
        raise InstagramPublishError("Meta did not return an Instagram media-container ID")
    upload_url = container_body.get("uri") or f"{UPLOAD}/ig-api-upload/{version}/{container}"
    upload_parts = urlsplit(upload_url)
    if upload_parts.scheme != "https" or upload_parts.netloc != "rupload.facebook.com":
        raise InstagramPublishError("Meta returned an unexpected Instagram upload URL")

    try:
        with open(path, "rb") as video:
            uploaded = requests.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {access_token}",
                    "offset": "0",
                    "file_size": str(os.path.getsize(path)),
                    "Content-Type": "application/octet-stream",
                },
                data=video,
                timeout=(30, 1800),
            )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise InstagramPublishError("Instagram video upload timed out; inspect the container before retrying",
                                    uncertain=True) from exc
    upload_body = _json(uploaded, access_token)
    if upload_body.get("success") is not True:
        raise InstagramPublishError("Instagram resumable upload did not return success")

    status_url = f"{GRAPH}/{version}/{container}?fields=status_code,status"
    for _ in range(max_polls):
        state = _get_json(status_url, access_token)
        status = (state.get("status_code") or "").upper()
        if status == "FINISHED":
            break
        if status in ("ERROR", "EXPIRED"):
            detail = state.get("status") or status
            raise InstagramPublishError(f"Instagram media processing failed: {detail}")
        time.sleep(poll_interval)
    else:
        raise InstagramPublishError("Instagram media processing did not finish before the polling deadline",
                                    uncertain=True)

    try:
        published = requests.post(
            f"{GRAPH}/{version}/{user_id}/media_publish",
            data={"creation_id": container},
            headers=headers,
            timeout=(15, 120),
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise InstagramPublishError("Instagram publish request timed out; the Reel may already be published",
                                    uncertain=True) from exc
    media_id = _json(published, access_token, uncertain_on_server_error=True).get("id")
    if not media_id:
        raise InstagramPublishError("Instagram publish response did not include a media ID")
    return media_id
