import atexit
import base64, io, ipaddress, json, os, re, socket, threading, time, urllib.parse, urllib.request, requests
from PIL import Image
from playwright.sync_api import sync_playwright
import config as S
_last = [0.0]
_original_getaddrinfo = socket.getaddrinfo
_dns_cache = {}
_dns_lock = threading.Lock()
_dns_resolution = threading.local()
_deepai_playwright = None
_deepai_browser = None
_deepai_page = None
_deepai_last_generation = 0.0
_deepai_disabled_reason = None
_DEEPAI_URL = "https://deepai.org/machine-learning-model/text2img"
_DEEPAI_IMAGE_PREFIX = "https://api.deepai.org/job-view-file/"
_DEEPAI_MIN_INTERVAL = 5.0


class DeepAIUnavailableError(RuntimeError):
    """DeepAI generation cannot continue without bypassing a restriction."""

def _getaddrinfo_with_doh(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _original_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        if not isinstance(host, str) or not host or family == socket.AF_INET6 or getattr(_dns_resolution, "active", False):
            raise
        with _dns_lock:
            cached = _dns_cache.get(host)
            address = cached[0] if cached and cached[1] > time.monotonic() else None
            if cached and not address:
                _dns_cache.pop(host, None)
            if not address:
                _dns_resolution.active = True
                try:
                    query = urllib.parse.urlencode({"name": host, "type": "A"})
                    request = urllib.request.Request(
                        f"https://dns.google/resolve?{query}",
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    with urllib.request.urlopen(request, timeout=10) as response:
                        answer = json.loads(response.read().decode("utf-8"))
                    for record in answer.get("Answer", []):
                        if record.get("type") == 1:
                            candidate = str(record.get("data", ""))
                            try:
                                if ipaddress.ip_address(candidate).version == 4:
                                    address = candidate
                                    ttl = max(1, min(int(record.get("TTL", 60)), 300))
                                    _dns_cache[host] = (address, time.monotonic() + ttl)
                                    break
                            except ValueError:
                                continue
                finally:
                    _dns_resolution.active = False
        if address:
            return _original_getaddrinfo(address, port, family, type, proto, flags)
        raise

socket.getaddrinfo = _getaddrinfo_with_doh

def _retryable_image_error(exc):
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    message = str(exc).lower()
    if status is None:
        match = re.search(r"\b([45]\d\d)\b", message)
        status = int(match.group(1)) if match else None
    if (status in (400, 401, 402, 403, 404) or "quota" in message or "4006" in message
            or "daily free allocation" in message or "out of credits" in message
            or "insufficient credit" in message or "payment required" in message):
        return False
    if any(marker in message for marker in ("captcha", "verify you are human", "rate limit", "too many requests")):
        return False
    return True

def _close_deepai():
    global _deepai_playwright, _deepai_browser, _deepai_page

    try:
        if _deepai_browser and _deepai_browser.is_connected():
            _deepai_browser.close()
    finally:
        if _deepai_playwright:
            _deepai_playwright.stop()
        _deepai_playwright = None
        _deepai_browser = None
        _deepai_page = None


atexit.register(_close_deepai)


def _deepai_page_for_generation():
    global _deepai_playwright, _deepai_browser, _deepai_page, _deepai_disabled_reason

    if _deepai_disabled_reason:
        raise DeepAIUnavailableError(_deepai_disabled_reason)
    if _deepai_browser and _deepai_browser.is_connected() and _deepai_page:
        return _deepai_page

    try:
        _deepai_playwright = sync_playwright().start()
        _deepai_browser = _deepai_playwright.chromium.launch(headless=True)
        _deepai_page = _deepai_browser.new_page()
        response = _deepai_page.goto(_DEEPAI_URL, wait_until="domcontentloaded", timeout=60_000)
        if response is None or response.status >= 400:
            status = response.status if response else "no response"
            _deepai_disabled_reason = f"DeepAI image page failed to load ({status})"
            raise DeepAIUnavailableError(_deepai_disabled_reason)
        _check_deepai_restriction(_deepai_page)
        _deepai_page.locator("#generate-textarea").wait_for(timeout=30_000)
    except DeepAIUnavailableError:
        raise
    except Exception as exc:
        _deepai_disabled_reason = (
            f"DeepAI browser or generator page is unavailable ({type(exc).__name__})"
        )
        raise DeepAIUnavailableError(_deepai_disabled_reason) from exc
    return _deepai_page


def _check_deepai_restriction(page):
    global _deepai_disabled_reason

    text = page.locator("body").inner_text(timeout=10_000).lower()
    markers = (
        "verify you are human",
        "are you a human",
        "captcha",
        "unusual traffic",
        "rate limit",
        "too many requests",
    )
    match = next((marker for marker in markers if marker in text), None)
    if match:
        _deepai_disabled_reason = (
            f"DeepAI displayed a {match} restriction; stopping DeepAI attempts without bypassing it"
        )
        raise DeepAIUnavailableError(_deepai_disabled_reason)


def _deepai(prompt, seed, attempt=0):
    global _deepai_last_generation, _deepai_disabled_reason

    del seed, attempt
    page = _deepai_page_for_generation()
    _check_deepai_restriction(page)
    wait = _DEEPAI_MIN_INTERVAL - (time.monotonic() - _deepai_last_generation)
    if wait > 0:
        time.sleep(wait)

    previous_urls = set(
        page.locator("main img").evaluate_all(
            """(images, prefix) =>
                images.map(image => image.currentSrc || image.src)
                    .filter(src => src.startsWith(prefix))""",
            _DEEPAI_IMAGE_PREFIX,
        )
    )
    try:
        page.locator("#generate-textarea").fill(prompt[:2000])
        submit = page.locator("#modelSubmitButton")
        submit.wait_for(state="visible", timeout=10_000)
        page.wait_for_function(
            "() => !document.querySelector('#modelSubmitButton')?.disabled",
            timeout=10_000,
        )
        submit.click()
        page.wait_for_function(
            """({prefix, previous}) =>
                [...document.querySelectorAll('main img')].some(image => {
                    const src = image.currentSrc || image.src;
                    return src.startsWith(prefix) && !previous.includes(src)
                        && image.complete && image.naturalWidth >= 400 && image.naturalHeight >= 300;
                })""",
            arg={"prefix": _DEEPAI_IMAGE_PREFIX, "previous": list(previous_urls)},
            timeout=180_000,
        )
    except Exception as exc:
        try:
            _check_deepai_restriction(page)
        except DeepAIUnavailableError:
            raise
        message = str(exc).lower()
        if "429" in message or "403" in message:
            _deepai_disabled_reason = "DeepAI rate-limited or blocked image generation; stopping DeepAI attempts"
        else:
            _deepai_disabled_reason = (
                f"DeepAI generation failed ({type(exc).__name__}); stopping further DeepAI attempts"
            )
        raise DeepAIUnavailableError(_deepai_disabled_reason) from exc

    image_urls = page.locator("main img").evaluate_all(
        """(images, args) => {
            const image = images.find(element => {
                const src = element.currentSrc || element.src;
                return src.startsWith(args.prefix) && !args.previous.includes(src)
                    && element.complete && element.naturalWidth >= 400 && element.naturalHeight >= 300;
            });
            return image ? [image.currentSrc || image.src] : [];
        }""",
        {"prefix": _DEEPAI_IMAGE_PREFIX, "previous": list(previous_urls)},
    )
    if not image_urls:
        raise RuntimeError("DeepAI completed generation but no output image was found")
    response = page.request.get(image_urls[0], timeout=60_000)
    if not response.ok:
        if response.status in (403, 429):
            _deepai_disabled_reason = (
                f"DeepAI image download was blocked with HTTP {response.status}; stopping DeepAI attempts"
            )
        raise RuntimeError(f"DeepAI image download failed with HTTP {response.status}")
    image = Image.open(io.BytesIO(response.body())).convert("RGB")
    _deepai_last_generation = time.monotonic()
    return image

def _cloudflare(prompt, seed, attempt=0):
    acc = (os.getenv("CLOUDFLARE_ACCOUNT_ID") or os.getenv("CF_ACCOUNT_ID") or "").strip()
    tok = (os.getenv("CLOUDFLARE_API_TOKEN") or os.getenv("CF_API_TOKEN") or "").strip()
    if not (acc and tok): return None
    # Request native 16:9 widescreen on first attempts; fallback to default if needed
    payload = {"prompt": prompt[:2000], "steps": 4}
    r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                      headers={"Authorization": f"Bearer {tok}"}, json=payload, timeout=120)
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith("image/"):
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    result = r.json()
    if not result.get("success", True) or "result" not in result:
        raise ValueError(f"Cloudflare returned an unexpected response: {str(result)[:300]}")
    return Image.open(io.BytesIO(base64.b64decode(result["result"]["image"]))).convert("RGB")

def _pollinations(prompt, seed, attempt=0):
    wait = 16 - (time.time() - _last[0])                       # anonymous free tier pacing
    if wait > 0: time.sleep(wait)
    # Rotate across free Pollinations models on retries so a busy model never blocks generation
    models = ["flux", "flux-realism", "turbo"]
    model = models[attempt % len(models)]
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:1500]) +
           f"?width=1280&height=720&seed={seed + attempt}&model={model}&nologo=true&enhance=true&safe=true")
    hdr = {"User-Agent": "SerialRecapBot/1.0"}
    if os.getenv("POLLINATIONS_TOKEN"):
        hdr["Authorization"] = "Bearer " + os.getenv("POLLINATIONS_TOKEN")
    r = requests.get(url, headers=hdr, timeout=180); _last[0] = time.time()
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image"):
        raise ValueError("not an image")
    return Image.open(io.BytesIO(r.content)).convert("RGB")

def _flux(prompt, seed, attempt=0):
    from gradio_client import Client

    space = os.getenv("HF_FLUX_SPACE") or "https://black-forest-labs-flux-1-schnell.hf.space"
    result = Client(space, verbose=False).predict(
        prompt=prompt[:2000],
        seed=int(seed),
        randomize_seed=True,
        width=1280,
        height=720,
        num_inference_steps=4,
        api_name="/infer",
    )
    image_data = result[0] if isinstance(result, (list, tuple)) else result
    if isinstance(image_data, dict):
        image_data = image_data.get("path")
    if isinstance(image_data, Image.Image):
        return image_data.convert("RGB")
    if not isinstance(image_data, (str, os.PathLike)):
        raise ValueError("FLUX returned no image file")
    return Image.open(image_data).convert("RGB")

def _image_providers():
    return (("deepai", _deepai, 1), ("cloudflare", _cloudflare, 3),
            ("flux", _flux, 1), ("pollinations", _pollinations, 3))

def generate_thumbnail(prompt, seed, out):
    """Generate one cover, preferring DeepAI then the existing providers."""
    full = f"{prompt}. Style: {S.STYLE}"
    for name, fn, attempts in _image_providers():
        for attempt in range(attempts):
            try:
                im = fn(full, seed, attempt=attempt)
                if im is None:
                    break
                if im.width < 400 or im.height < 300:
                    raise ValueError("image is too small")
                im.save(out, quality=92)
                print(f"    thumbnail art ok via {name} (try {attempt + 1})")
                return True
            except Exception as exc:
                print(f"    thumbnail {name} try {attempt + 1}/{attempts}: {type(exc).__name__}: {str(exc)[:80]}")
                if not _retryable_image_error(exc):
                    break
                if attempt + 1 < attempts:
                    time.sleep(6 * (attempt + 1))
    return False

def reuse_nearest_generated_images(comps, generated_indices):
    """Fill scenes without generated art from the nearest scene that has it."""
    sources = sorted(set(generated_indices))
    if not sources:
        return []
    reused = []
    for i in sorted(comps):
        if comps[i]:
            continue
        source = min(sources, key=lambda candidate: (abs(candidate - i), candidate))
        comps[i] = list(comps[source])
        reused.append((i, source))
    return reused

def generate(prompt, seed, out):
    """Make one illustration, preferring DeepAI then the existing providers."""
    full = f"{prompt}. Style: {S.STYLE}"
    for name, fn, attempts in _image_providers():
        for attempt in range(attempts):
            try:
                im = fn(full, seed, attempt=attempt)
                if im is None:
                    break
                if im.width < 400 or im.height < 300:
                    raise ValueError("image is too small")
                im.save(out, quality=92)
                print(f"    image ok via {name} (try {attempt + 1})")
                return True
            except Exception as exc:
                print(f"    image {name} try {attempt + 1}/{attempts}: {type(exc).__name__}: {str(exc)[:80]}")
                if not _retryable_image_error(exc):
                    break
                if attempt + 1 < attempts:
                    time.sleep(6 * (attempt + 1))
    return False
