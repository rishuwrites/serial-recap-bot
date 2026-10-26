import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "recap"))

import config
import facebook
import instagram
import publishing_state
import render
import vertical
import youtube
from PIL import Image


class FakeResponse:
    def __init__(self, body, status_code=200):
        self.body = body
        self.status_code = status_code
        self.text = json.dumps(body)

    def json(self):
        return self.body


class PublishingStateTests(unittest.TestCase):
    def test_destination_state_is_independent_and_persistent(self):
        with tempfile.TemporaryDirectory() as folder:
            publishing_state.update("kaisi", "source-1", "facebook", "in_progress", base=folder)
            publishing_state.update("kaisi", "source-1", "facebook", "success", media_id="fb-1", base=folder)
            publishing_state.update("kaisi", "source-1", "instagram", "failed", error="permission denied", base=folder)

            self.assertEqual(publishing_state.platform_status("kaisi", "source-1", "facebook", folder), "success")
            self.assertEqual(publishing_state.platform_status("kaisi", "source-1", "instagram", folder), "failed")
            self.assertEqual(publishing_state.failed_platforms("kaisi", ["facebook", "instagram"], folder), "source-1")
            entry = publishing_state.get_entry("kaisi", "source-1", folder)
            self.assertEqual(entry["platforms"]["facebook"]["media_id"], "fb-1")
            saved = json.loads((Path(folder) / "state" / "kaisi_publishing.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["source-1"]["platforms"]["instagram"]["error"], "permission denied")

    def test_uncertain_result_is_not_returned_for_automatic_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            publishing_state.update("kaisi", "source-1", "instagram", "uncertain", base=folder)
            self.assertEqual(publishing_state.failed_platforms("kaisi", ["instagram"], folder), "")

    def test_concurrent_workers_can_claim_a_destination_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            with ThreadPoolExecutor(max_workers=8) as pool:
                claimed = list(pool.map(
                    lambda _: publishing_state.claim("kaisi", "source-1", "facebook", base=folder),
                    range(8),
                ))
            self.assertEqual(sum(claimed), 1)
            self.assertEqual(publishing_state.get_entry("kaisi", "source-1", folder)
                             ["platforms"]["facebook"]["attempts"], 1)


class FacebookPublisherTests(unittest.TestCase):
    def test_resumable_upload_handle_is_published_to_configured_page(self):
        with tempfile.TemporaryDirectory() as folder:
            video_path = Path(folder) / "landscape.mp4"
            video_path.write_bytes(b"synthetic-video")
            responses = [
                FakeResponse({"id": "upload:session-1"}),
                FakeResponse({"h": "uploaded-handle"}),
                FakeResponse({"id": "page-video-1"}),
            ]
            with mock.patch.object(facebook.requests, "post", side_effect=responses) as post:
                video_id = facebook.publish_video(
                    str(video_path),
                    title="Title",
                    description="Description",
                    page_id="page-1",
                    page_token="page-token",
                    app_id="app-1",
                    user_token="user-token",
                    version="v26.0",
                )

        self.assertEqual(video_id, "page-video-1")
        self.assertTrue(post.call_args_list[0].args[0].endswith("/v26.0/app-1/uploads"))
        self.assertEqual(post.call_args_list[0].kwargs["params"]["file_length"], "15")
        self.assertEqual(post.call_args_list[1].kwargs["headers"]["file_offset"], "0")
        self.assertEqual(post.call_args_list[2].args[0], "https://graph-video.facebook.com/v26.0/page-1/videos")
        self.assertEqual(post.call_args_list[2].kwargs["data"]["fbuploader_video_file_chunk"], "uploaded-handle")
        self.assertEqual(post.call_args_list[2].kwargs["data"]["access_token"], "page-token")

    def test_api_error_redacts_access_tokens(self):
        response = FakeResponse({"error": {"message": "bad token secret-value"}}, status_code=400)
        with self.assertRaises(facebook.FacebookPublishError) as raised:
            facebook._json(response, ("secret-value",))
        self.assertNotIn("secret-value", str(raised.exception))

    def test_transient_chunk_failure_resumes_at_verified_offset(self):
        with tempfile.TemporaryDirectory() as folder:
            video_path = Path(folder) / "landscape.mp4"
            video_path.write_bytes(b"synthetic-video")
            responses = [
                FakeResponse({"id": "upload:session-2"}),
                FakeResponse({"error": {"message": "temporary failure"}}, status_code=500),
                FakeResponse({"h": "resumed-handle"}),
                FakeResponse({"id": "page-video-2"}),
            ]
            with mock.patch.object(facebook.requests, "post", side_effect=responses) as post, \
                    mock.patch.object(facebook.requests, "get", return_value=FakeResponse({"file_offset": 0})) as get, \
                    mock.patch.object(facebook.time, "sleep"):
                video_id = facebook.publish_video(
                    str(video_path), page_id="page-1", page_token="page-token",
                    app_id="app-1", user_token="user-token",
                )
        self.assertEqual(video_id, "page-video-2")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(post.call_args_list[2].kwargs["headers"]["file_offset"], "0")

    def test_missing_credentials_and_video_are_reported(self):
        with self.assertRaisesRegex(facebook.FacebookPublishError, "META_USER_ACCESS_TOKEN"):
            facebook.publish_video("missing.mp4", page_id="page", page_token="token", app_id="app", user_token="")


class InstagramPublisherTests(unittest.TestCase):
    def test_local_resumable_reel_flow_and_status_polling(self):
        with tempfile.TemporaryDirectory() as folder:
            video_path = Path(folder) / "vertical.mp4"
            video_path.write_bytes(b"synthetic-video")
            created = FakeResponse({"id": "container-1"})
            uploaded = FakeResponse({"success": True})
            published = FakeResponse({"id": "media-1"})
            with mock.patch.object(instagram.requests, "post", side_effect=[created, uploaded, published]) as post, \
                    mock.patch.object(instagram.requests, "get", return_value=FakeResponse({"status_code": "FINISHED"})) as get, \
                    mock.patch.object(instagram, "validate_video", return_value=12.0):
                media_id = instagram.publish_reel(
                    str(video_path), "caption", user_id="ig-user", access_token="secret", version="v26.0",
                    poll_interval=0, max_polls=1,
                )

        self.assertEqual(media_id, "media-1")
        self.assertEqual(post.call_args_list[0].args[0], "https://graph.facebook.com/v26.0/ig-user/media")
        self.assertEqual(post.call_args_list[0].kwargs["data"]["media_type"], "REELS")
        self.assertEqual(post.call_args_list[0].kwargs["data"]["upload_type"], "resumable")
        self.assertIn("/ig-api-upload/v26.0/container-1", post.call_args_list[1].args[0])
        self.assertEqual(post.call_args_list[1].kwargs["headers"]["file_size"], "15")
        self.assertIn("fields=status_code,status", get.call_args.args[0])
        self.assertEqual(post.call_args_list[2].kwargs["data"]["creation_id"], "container-1")

    def test_caption_limit_is_checked_before_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "vertical.mp4"
            path.write_bytes(b"video")
            with mock.patch.object(instagram.requests, "post") as post:
                with self.assertRaisesRegex(instagram.InstagramPublishError, "2,200"):
                    instagram.publish_reel(str(path), "x" * 2201, user_id="id", access_token="token")
            post.assert_not_called()

    def test_expired_token_error_is_reported_without_disclosing_token(self):
        response = FakeResponse({
            "error": {"code": 190, "message": "Error validating access token: expired-secret"},
        }, status_code=400)
        with self.assertRaises(instagram.InstagramPublishError) as raised:
            instagram._json(response, "expired-secret")
        self.assertIn("190", str(raised.exception))
        self.assertNotIn("expired-secret", str(raised.exception))

    def test_failed_processing_status_stops_before_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            video_path = Path(folder) / "vertical.mp4"
            video_path.write_bytes(b"synthetic-video")
            with mock.patch.object(instagram.requests, "post", side_effect=[
                    FakeResponse({"id": "container-2"}), FakeResponse({"success": True})]) as post, \
                    mock.patch.object(instagram, "validate_video", return_value=12.0), \
                    mock.patch.object(instagram.requests, "get", return_value=FakeResponse({
                        "status_code": "ERROR", "status": "Processing failed",
                    })):
                with self.assertRaisesRegex(instagram.InstagramPublishError, "processing failed"):
                    instagram.publish_reel(
                        str(video_path), user_id="ig-user", access_token="token", poll_interval=0, max_polls=1,
                    )
            self.assertEqual(post.call_count, 2)


class CompatibilityAndVerticalTests(unittest.TestCase):
    def test_existing_youtube_enablement_contract_is_unchanged(self):
        with mock.patch.dict(os.environ, {
            "YT_CLIENT_ID": "id", "YT_CLIENT_SECRET": "secret", "YT_REFRESH_TOKEN": "refresh",
        }):
            self.assertTrue(youtube.enabled())
        with mock.patch.dict(os.environ, {"YT_CLIENT_ID": "", "YT_CLIENT_SECRET": "", "YT_REFRESH_TOKEN": ""}):
            self.assertFalse(youtube.enabled())

    def test_vertical_safe_area_and_ffmpeg_synthetic_render(self):
        self.assertEqual(vertical.caption_position(), (1470, 230))
        self.assertLess(vertical.CAPTION_TOP + vertical.CAPTION_HEIGHT, vertical.H - 100)
        self.assertEqual((config.W, config.H), (1920, 1080))
        self.assertEqual((config.VERTICAL_W, config.VERTICAL_H), (1080, 1920))

        try:
            render.find_font()
        except SystemExit as exc:
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory() as folder:
            frame = Path(folder) / "scene.png"
            audio = Path(folder) / "voice.mp3"
            music = Path(folder) / "music.wav"
            output = Path(folder) / "vertical.mp4"
            Image.new("RGB", (640, 360), (50, 90, 140)).save(frame)
            import subprocess
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=3.2", "-c:a", "libmp3lame", "-b:a", "160k", str(audio),
            ], check=True)
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=3.2", str(music),
            ], check=True)

            segment = {
                "tag": "synthetic", "title": "आज की कहानी", "chip": "दृश्य १", "audio": str(audio),
                "lines": ["कहानी शुरू होती है।"], "times": [(0.0, 3.2)],
                "word_times": [[("कहानी", 0.0, 0.8), ("शुरू", 0.8, 1.6), ("होती", 1.6, 2.4), ("है।", 2.4, 3.2)]],
                "img": 0,
            }
            vertical.render_episode([segment], [str(frame)], folder, str(output), str(music), 3.2)

            probe = subprocess.check_output([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(output),
            ], text=True).strip()
            self.assertAlmostEqual(instagram.validate_video(str(output)), 3.2, delta=0.15)
        self.assertEqual(probe, "1080x1920")


if __name__ == "__main__":
    unittest.main()
