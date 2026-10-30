import base64, io, os, time, urllib.parse, requests
from PIL import Image
import config as S
_last = [0.0]

def _cloudflare(prompt, seed, attempt=0):
    acc, tok = os.getenv("CF_ACCOUNT_ID"), os.getenv("CF_API_TOKEN")
    if not (acc and tok): return None
    # Request native 16:9 widescreen on first attempts; fallback to default if needed
    payload = {"prompt": prompt[:2000], "steps": 8, "seed": int(seed)}
    if attempt == 0:
        payload.update({"width": 1344, "height": 768})
    r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/black-forest-labs/flux-1-schnell",
                      headers={"Authorization": f"Bearer {tok}"}, json=payload, timeout=120)
    r.raise_for_status()
    return Image.open(io.BytesIO(base64.b64decode(r.json()["result"]["image"]))).convert("RGB")

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

def generate_thumbnail(prompt, seed, out):
    """Generate one 16:9 cover, preferring the FLUX Gradio Space then existing providers."""
    full = f"{prompt}. Style: {S.STYLE}"
    providers = (("flux", _flux, 1), ("cloudflare", _cloudflare, 3), ("pollinations", _pollinations, 3))
    for name, fn, attempts in providers:
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
                if attempt + 1 < attempts:
                    time.sleep(6 * (attempt + 1))
    return False

def generate(prompt, seed, out):
    """Make one illustration, preferring the FLUX Gradio Space then existing providers."""
    full = f"{prompt}. Style: {S.STYLE}"
    providers = (("flux", _flux, 1), ("cloudflare", _cloudflare, 3), ("pollinations", _pollinations, 3))
    for name, fn, attempts in providers:
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
                if attempt + 1 < attempts:
                    time.sleep(6 * (attempt + 1))
    return False
