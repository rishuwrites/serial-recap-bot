import json
import io
import os
import socket
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
    def test_dns_over_https_fallback_handles_system_lookup_failure(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({
            "Answer": [{"type": 1, "data": "203.0.113.10"}],
        }).encode("utf-8")

        def resolve(host, *args):
            if host == "unresolved.example":
                raise socket.gaierror("system DNS lookup failed")
            if host == "203.0.113.10":
                return ["resolved"]
            raise AssertionError(f"Unexpected host: {host}")

        images._dns_cache.clear()
        self.addCleanup(images._dns_cache.clear)
        with mock.patch.object(images, "_original_getaddrinfo", side_effect=resolve), \
                mock.patch.object(images.urllib.request, "urlopen", return_value=response):
            result = images._getaddrinfo_with_doh("unresolved.example", 443, socket.AF_INET)

        self.assertEqual(result, ["resolved"])

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

    def test_deepai_is_first_provider_for_scene_and_thumbnail_images(self):
        image = Image.new("RGB", (640, 640), color="purple")
        providers = ("_cloudflare", "_flux", "_pollinations")
        for generator in (images.generate, images.generate_thumbnail):
            with self.subTest(generator=generator.__name__), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "image.jpg"
                with mock.patch.object(images, "_deepai", return_value=image) as deepai, \
                        mock.patch.multiple(
                            images,
                            **{name: mock.DEFAULT for name in providers},
                        ) as fallbacks:
                    self.assertTrue(generator("test image", 1, str(output)))

                deepai.assert_called_once()
                for fallback in fallbacks.values():
                    fallback.assert_not_called()
                self.assertTrue(output.is_file())

    def test_deepai_block_falls_back_to_cloudflare(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "image.jpg"
            with mock.patch.object(
                images, "_deepai", side_effect=images.DeepAIUnavailableError("CAPTCHA detected"),
            ), mock.patch.object(images, "_cloudflare", return_value=Image.new("RGB", (640, 640))) as cloudflare:
                self.assertTrue(images.generate("test image", 1, str(output)))
            self.assertTrue(output.is_file())
        cloudflare.assert_called_once()

    def test_captcha_and_rate_limit_errors_are_not_retried(self):
        for message in ("CAPTCHA detected", "verify you are human", "rate limit"):
            with self.subTest(message=message):
                self.assertFalse(images._retryable_image_error(RuntimeError(message)))

    def test_deepai_generation_uses_browser_controls_without_api_calls(self):
        image_url = "https://api.deepai.org/job-view-file/example/output.jpg"
        page = mock.Mock()
        textarea = mock.Mock()
        submit = mock.Mock()
        body = mock.Mock()
        body.inner_text.return_value = "Image Generator"
        main_images = mock.Mock()
        main_images.evaluate_all.side_effect = [[], [image_url]]
        page.locator.side_effect = lambda selector: {
            "body": body,
            "main img": main_images,
            "#generate-textarea": textarea,
            "#modelSubmitButton": submit,
        }[selector]
        download = mock.Mock()
        download.ok = True
        download.body.return_value = jpeg_bytes((640, 640))
        page.request.get.return_value = download
        with mock.patch.object(images, "_deepai_page_for_generation", return_value=page), \
                mock.patch.object(images, "_deepai_last_generation", 0), \
                mock.patch.object(images, "_deepai_disabled_reason", None), \
                mock.patch.object(images.requests, "post") as api_post:
            image = images._deepai("example prompt", 42)

        self.assertEqual(image.size, (640, 640))
        textarea.fill.assert_called_once_with("example prompt")
        submit.click.assert_called_once()
        page.request.get.assert_called_once_with(image_url, timeout=60_000)
        api_post.assert_not_called()

    def test_deepai_captcha_detection_stops_without_retrying(self):
        page = mock.Mock()
        page.locator.return_value.inner_text.return_value = "Verify you are human"
        with mock.patch.object(images, "_deepai_disabled_reason", None):
            with self.assertRaises(images.DeepAIUnavailableError) as error:
                images._check_deepai_restriction(page)

        self.assertFalse(images._retryable_image_error(error.exception))

    def test_thumbnail_generation_falls_back_when_flux_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "thumbnail.jpg"
            with mock.patch.object(images, "_deepai", return_value=None), \
                    mock.patch.object(images, "_cloudflare", side_effect=RuntimeError("Cloudflare unavailable")), \
                    mock.patch.object(images, "_flux", return_value=Image.new("RGB", (1280, 720))), \
                    mock.patch.object(images.time, "sleep"):
                succeeded = images.generate_thumbnail("cover prompt", 42, str(output))

            self.assertTrue(succeeded)
            with Image.open(output) as image:
                self.assertEqual(image.size, (1280, 720))

    def test_cloudflare_uses_configured_secret_names_and_decodes_image(self):
        import base64

        response = mock.Mock()
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {"success": True, "result": {
            "image": base64.b64encode(jpeg_bytes((1280, 720))).decode("ascii"),
        }}
        with mock.patch.dict(os.environ, {
            "CLOUDFLARE_ACCOUNT_ID": "account",
            "CLOUDFLARE_API_TOKEN": "test-token",
        }), mock.patch.object(images.requests, "post", return_value=response) as post:
            image = images._cloudflare("cover prompt", 42)

        self.assertEqual(image.size, (1280, 720))
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(post.call_args.kwargs["json"]["steps"], 4)

    def test_thumbnail_generation_uses_deepai_before_fallbacks(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "thumbnail.jpg"
            with mock.patch.object(images, "_deepai", return_value=Image.new("RGB", (1280, 720))) as deepai, \
                    mock.patch.object(images, "_cloudflare") as cloudflare, \
                    mock.patch.object(images, "_flux") as flux, \
                    mock.patch.object(images, "_pollinations") as pollinations:
                succeeded = images.generate_thumbnail("cover prompt", 42, str(output))

        self.assertTrue(succeeded)
        deepai.assert_called_once()
        cloudflare.assert_not_called()
        flux.assert_not_called()
        pollinations.assert_not_called()

    def test_scene_image_generation_uses_deepai_before_fallbacks(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scene.jpg"
            with mock.patch.object(images, "_deepai", return_value=Image.new("RGB", (1280, 720))) as deepai, \
                    mock.patch.object(images, "_cloudflare") as cloudflare, \
                    mock.patch.object(images, "_flux") as flux, \
                    mock.patch.object(images, "_pollinations") as pollinations:
                succeeded = images.generate("scene prompt", 42, str(output))

        self.assertTrue(succeeded)
        deepai.assert_called_once()
        cloudflare.assert_not_called()
        flux.assert_not_called()
        pollinations.assert_not_called()

    def test_cloudflare_quota_failure_falls_back_to_flux(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scene.jpg"
            with mock.patch.object(
                images, "_deepai", return_value=None,
            ), mock.patch.object(
                images, "_cloudflare",
                side_effect=RuntimeError("Cloudflare daily quota exhausted"),
            ) as cloudflare, \
                    mock.patch.object(images, "_flux", return_value=Image.new("RGB", (1280, 720))) as flux, \
                    mock.patch.object(images, "_pollinations") as pollinations, \
                    mock.patch.object(images.time, "sleep") as sleep:
                succeeded = images.generate("scene prompt", 42, str(output))

        self.assertTrue(succeeded)
        cloudflare.assert_called_once()
        flux.assert_called_once()
        pollinations.assert_not_called()
        sleep.assert_not_called()

    def test_missing_scenes_reuse_nearest_generated_art(self):
        comps = {0: ["scene-0"], 1: [], 2: ["scene-2"], 3: []}

        reused = images.reuse_nearest_generated_images(comps, {0, 2})

        self.assertEqual(reused, [(1, 0), (3, 2)])
        self.assertEqual(comps[1], ["scene-0"])
        self.assertEqual(comps[3], ["scene-2"])

    def test_missing_scenes_remain_unfilled_when_no_art_was_generated(self):
        comps = {0: [], 1: []}

        reused = images.reuse_nearest_generated_images(comps, set())

        self.assertEqual(reused, [])
        self.assertEqual(comps, {0: [], 1: []})

    def test_payment_required_stops_retries_for_that_provider(self):
        response = images.requests.Response()
        response.status_code = 402
        error = images.requests.HTTPError("402 Payment Required", response=response)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scene.jpg"
            with mock.patch.object(images, "_deepai", return_value=None), \
                    mock.patch.object(images, "_flux", side_effect=RuntimeError("ZeroGPU quota")), \
                    mock.patch.object(images, "_cloudflare", return_value=None), \
                    mock.patch.object(images, "_pollinations", side_effect=error) as pollinations, \
                    mock.patch.object(images.time, "sleep"):
                succeeded = images.generate("scene prompt", 42, str(output))

        self.assertFalse(succeeded)
        pollinations.assert_called_once()

    def test_transient_server_error_retries_pollinations(self):
        response = images.requests.Response()
        response.status_code = 503
        error = images.requests.HTTPError("503 Service Unavailable", response=response)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "scene.jpg"
            with mock.patch.object(images, "_deepai", return_value=None), \
                    mock.patch.object(images, "_flux", side_effect=RuntimeError("space unavailable")), \
                    mock.patch.object(images, "_cloudflare", return_value=None), \
                    mock.patch.object(
                        images, "_pollinations",
                        side_effect=[error, Image.new("RGB", (1280, 720))],
                    ) as pollinations, \
                    mock.patch.object(images.time, "sleep"):
                succeeded = images.generate("scene prompt", 42, str(output))

        self.assertTrue(succeeded)
        self.assertEqual(pollinations.call_count, 2)


if __name__ == "__main__":
    unittest.main()
