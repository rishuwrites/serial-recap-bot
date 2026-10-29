import base64, io, json, os, time, requests
from PIL import Image, UnidentifiedImageError
BASE = "https://generativelanguage.googleapis.com/v1beta"

class ThumbnailAnalysisError(RuntimeError):
    pass

def list_models(key):
    """Stable flash models first; preview/omni models often have 0 free quota (429), so they go last."""
    names = [os.getenv("GEMINI_MODEL") or "", "gemini-flash-latest"]
    try:
        r = requests.get(f"{BASE}/models", params={"key": key, "pageSize": 200}, timeout=30).json()
        ok = [m["name"].split("/")[-1] for m in r.get("models", [])
              if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]
              and not any(x in m["name"] for x in ("image", "tts", "live", "audio", "robotics", "computer", "omni", "exp"))]
        names += sorted([n for n in ok if "preview" not in n and "lite" not in n], reverse=True)
        names += sorted([n for n in ok if "preview" not in n and "lite" in n], reverse=True)
        names += sorted([n for n in ok if "preview" in n], reverse=True)
    except Exception:
        pass
    names.append("gemini-2.5-flash")
    seen = []
    for n in names:
        if n and n not in seen: seen.append(n)
    return seen

def describe_thumbnail(video_id):
    """Describe the episode's public YouTube thumbnail for high-level composition cues."""
    failures = []
    image_bytes = None
    for quality in ("maxresdefault", "sddefault", "hqdefault"):
        try:
            response = requests.get(
                f"https://img.youtube.com/vi/{video_id}/{quality}.jpg", timeout=30
            )
            response.raise_for_status()
            with Image.open(io.BytesIO(response.content)) as image:
                if image.width < 320 or image.height < 180:
                    failures.append(f"{quality}: thumbnail too small")
                    continue
                image.verify()
            image_bytes = response.content
            break
        except requests.RequestException as exc:
            failures.append(f"{quality}: {type(exc).__name__}")
        except (UnidentifiedImageError, OSError) as exc:
            failures.append(f"{quality}: invalid image ({type(exc).__name__})")
    if image_bytes is None:
        raise ThumbnailAnalysisError("Could not fetch a usable YouTube thumbnail: " + "; ".join(failures))

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ThumbnailAnalysisError("GEMINI_API_KEY is required to describe the episode thumbnail")
    models = list_models(key)[:8]
    if not models:
        raise ThumbnailAnalysisError("No Gemini models are available for thumbnail analysis")

    prompt = (
        "Describe this YouTube thumbnail for use as high-level composition guidance for a new, "
        "original recap thumbnail. Include the number and placement of people, expressions, "
        "background, lighting, colors, and text placement. Do not transcribe text, identify or "
        "imitate real people, or suggest copying logos or exact artwork. Keep the description "
        "concise and factual."
    )
    image_part = {
        "inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(image_bytes).decode("ascii"),
        }
    }
    last_error = "no Gemini response"
    for model in models:
        try:
            response = requests.post(
                f"{BASE}/models/{model}:generateContent",
                params={"key": key},
                json={"contents": [{"parts": [image_part, {"text": prompt}]}]},
                timeout=90,
            )
        except requests.RequestException as exc:
            last_error = f"{model}: {type(exc).__name__}"
            continue
        if response.status_code != 200:
            last_error = f"{model}: HTTP {response.status_code} {response.text[:160]}"
            continue
        try:
            description = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            last_error = f"{model}: invalid response ({type(exc).__name__})"
            continue
        if description:
            print("  source thumbnail described with", model)
            return description
        last_error = f"{model}: empty description"
    raise ThumbnailAnalysisError("Gemini could not describe the episode thumbnail: " + last_error)

PROMPT = """आप एक हिंदी टीवी-सीरियल रिकैप राइटर हैं। यह वीडियो एक पूरा सीरियल-एपिसोड है। इसे ध्यान से देखें-सुनें और इसकी कहानी पर तेज़ रफ़्तार, 2-3 मिनट के YouTube वीडियो की स्क्रिप्ट बनाएँ।
शैली (बहुत ज़रूरी):
- पहले 5 सेकंड में चौंकाने वाला hook: एपिसोड का सबसे बड़ा ट्विस्ट सबसे पहले (सस्पेंस के साथ, पूरा राज़ खोले बिना)। hook 12-16 शब्द का।
- फिर कहानी सस्पेंस के क्रम में समझाएँ (\"लेकिन यह शुरू कैसे हुआ?\" जैसे मोड़ से), छोटे-छोटे वाक्य, भावनात्मक लहजा, लगातार जिज्ञासा।
- बीच के दृश्यों की narration में 2-3 सस्पेंस सवाल रखें (जैसे \"आख़िर उसने ऐसा क्यों किया?\")।
- narration तेज़, रोचक और high-retention हो; कोई भूमिका या लंबा वर्णन नहीं।
- 10 से 12 दृश्य, हर दृश्य की narration 20-28 शब्द। कुल (hook, दृश्य, cliffhanger, prediction) लगभग 330-390 शब्द।
सच्चाई के नियम:
- सारा हिंदी टेक्स्ट सिर्फ़ देवनागरी में (अंग्रेज़ी शब्द/संक्षेप भी देवनागरी में); सिर्फ़ image_prompt और closeup_prompt अंग्रेज़ी में।
- सिर्फ़ वही लिखें जो एपिसोड में सचमुच हुआ/कहा गया; ट्विस्ट को बढ़ा-चढ़ाकर झूठ न बनाएँ। पात्रों के नाम वही रखें जो संवादों में बोले गए हों; पक्का न हो तो \"बहू\", \"दादी\" जैसे रिश्ते। कलाकारों के असली नाम कभी न लिखें। संवादों की नक़ल न करें।
- \"cliffhanger\": एपिसोड का अंत 1-2 छोटे वाक्य में। \"prediction\": 2 वाक्य, साफ़ अनुमान की भाषा में (\"शायद\", \"हो सकता है\"), तथ्य की तरह नहीं।
चित्र: \"characters\" में हर मुख्य पात्र का अंग्रेज़ी visual विवरण (उम्र, बाल, कपड़ों के रंग, पहचान की एक चीज़; सामान्य, किसी असली व्यक्ति जैसा नहीं)। हर दृश्य का \"image_prompt\" (wide shot, 40-70 शब्द: क्षण, जगह, भाव, मौजूद पात्रों के visual विवरण शब्दशः) और \"closeup_prompt\" (उसी क्षण में मुख्य पात्र के चेहरे का close-up, भावना साफ़, 25-45 शब्द)। ख़ून/हिंसा/नग्नता नहीं; बच्चों को ख़तरे में न दिखाएँ।
\"keywords\": इस एपिसोड की कहानी और शो से जुड़े 20 प्रासंगिक खोज-शब्द (हिंदी + अंग्रेज़ी); कोई असंबंधित शो/व्यक्ति नहीं।
केवल JSON:
{"characters":[{"key":"c1","name":"...","visual":"..."}],
 "hook":"...","headline":"<=8 शब्द","thumb_text":"<=5 शब्द का चौंकाने वाला सवाल/हुक","hook_scene":0,
 "scenes":[{"title":"<=5 शब्द","narration":"...","emotion":"tense|sad|happy|angry|romantic|shock","image_prompt":"...","closeup_prompt":"..."}],
 "cliffhanger":"...","prediction":["...","..."],"prediction_image_prompt":"...","keywords":["..."]}
"""

def analyze(video_url, show_name, episode):
    key = os.environ["GEMINI_API_KEY"]; models = list_models(key)[:8]; last = None
    base_cfg = {"responseMimeType": "application/json", "temperature": 0.5}
    for rnd, wait in enumerate([0, 30, 60, 120]):
        if wait: print(f"  all models busy, retrying in {wait}s"); time.sleep(wait)
        for m in models:
            for cfg in ({**base_cfg, "mediaResolution": "MEDIA_RESOLUTION_LOW"}, base_cfg):
                payload = {"contents": [{"parts": [{"file_data": {"file_uri": video_url}},
                                                   {"text": PROMPT + f"\nशो: {show_name} | एपिसोड: {episode}"}]}], "generationConfig": cfg}
                try: r = requests.post(f"{BASE}/models/{m}:generateContent", params={"key": key}, json=payload, timeout=900)
                except Exception as e: last = f"{m}: {type(e).__name__}"; break
                if r.status_code == 200:
                    try:
                        txt = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                        txt = txt.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                        print("  analysed with", m); return json.loads(txt)
                    except Exception as e: last = f"{m}: bad response ({e})"; print(" ", last); break
                last = f"{m}: {r.status_code} {r.text[:160]}"; print("  failed ->", m, r.status_code)
                if r.status_code != 400: break          # only 400 can mean 'mediaResolution unsupported' -> retry once without it
    raise SystemExit("Gemini could not analyse the video. Last error: " + str(last))
