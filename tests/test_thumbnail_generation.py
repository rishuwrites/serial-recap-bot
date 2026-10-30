import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "recap"))

import gemini
import images
from PIL import Image


class FakeResponse:
    def __init__(self, body=None, content=b"", status_code=200):
        self.body = body
        self.content = content
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise gemini.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.body


def jpeg_bytes(size):
    buffer = io.BytesIO()
    Image.new("RGB", size, (20, 50, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


class ThumbnailAnalysisTests(unittest.TestCase):
    def test_describes_episode_thumbnail_with_gemini(self):
        thumbnail = FakeResponse(content=jpeg_bytes((640, 360)))
        generated = FakeResponse(body={
            "candidates": [{"content": {"parts": [{"text": "Two people face each other in warm light."}]}}]
        })
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), \
                mock.patch.object(gemini, "list_models", return_value=["gemini-test"]), \
                mock.patch.object(gemini.requests, "get", return_value=thumbnail) as get, \
                mock.patch.object(gemini.requests, "post", return_value=generated) as post:
            description = gemini.describe_thumbnail("episode123")

        self.assertEqual(description, "Two people face each other in warm light.")
        self.assertIn("/vi/episode123/maxresdefault.jpg", get.call_args.args[0])
        payload = post.call_args.kwargs["json"]
        image_part = payload["contents"][0]["parts"][0]["inline_data"]
        self.assertEqual(image_part["mime_type"], "image/jpeg")
        self.assertTrue(image_part["data"])

    def test_skips_youtube_placeholder_and_uses_a_larger_thumbnail(self):
        responses = [
            FakeResponse(content=jpeg_bytes((120, 90))),
            FakeResponse(content=jpeg_bytes((640, 360))),
        ]
        generated = FakeResponse(body={
            "candidates": [{"content": {"parts": [{"text": "A family gathered indoors."}]}}]
        })
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}), \
                mock.patch.object(gemini, "list_models", return_value=["gemini-test"]), \
                mock.patch.object(gemini.requests, "get", side_effect=responses) as get, \
                mock.patch.object(gemini.requests, "post", return_value=generated):
            gemini.describe_thumbnail("episode123")

        self.assertEqual(get.call_count, 2)
        self.assertIn("sddefault.jpg", get.call_args_list[1].args[0])


class ThumbnailGenerationTests(unittest.TestCase):
    def test_flux_uses_configured_space_and_returns_its_image(self):
        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / "generated.png"
            Image.new("RGB", (1280, 720), (90, 30, 20)).save(image_path)
            client = mock.Mock()
            client.predict.return_value = [str(image_path)]
            with mock.patch.dict(os.environ, {"HF_FLUX_SPACE": "https://example.test/space"}), \
                    mock.patch("gradio_client.Client", return_value=client) as client_type:
                image = images._flux("cover prompt", 42)

        self.assertEqual(image.size, (1280, 720))
        client_type.assert_called_once_with("https://example.test/space", verbose=False)
        self.assertEqual(client.predict.call_args.kwargs["width"], 1280)
        self.assertEqual(client.predict.call_args.kwargs["height"], 720)

    def test_thumbnail_generation_falls_back_when_flux_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "thumbnail.jpg"
            with mock.patch.object(images, "_flux", side_effect=RuntimeError("space unavailable")), \
                    mock.patch.object(images, "_cloudflare", return_value=Image.new("RGB", (1280, 720))), \
                    mock.patch.object(images.time, "sleep"):
                succeeded = images.generate_thumbnail("cover prompt", 42, str(output))

            self.assertTrue(succeeded)
            with Image.open(output) as image:
                self.assertEqual(image.size, (1280, 720))

    def test_scene_image_generation_uses_flux_before_fallbacks(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scene.jpg"
            with mock.patch.object(images, "_flux", return_value=Image.new("RGB", (1280, 720))) as flux, \
                    mock.patch.object(images, "_cloudflare") as cloudflare, \
                    mock.patch.object(images, "_pollinations") as pollinations:
                succeeded = images.generate("scene prompt", 42, str(output))

        self.assertTrue(succeeded)
        flux.assert_called_once()
        cloudflare.assert_not_called()
        pollinations.assert_not_called()


if __name__ == "__main__":
    unittest.main()
