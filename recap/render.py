import functools, glob, math, os, random, subprocess
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from latin import deva
import config as S
W, H, FPS = S.W, S.H, S.FPS

def find_font():
    windows_fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for pat in ["/usr/share/fonts/**/NotoSansDevanagari-Bold.ttf", "/usr/share/fonts/**/NotoSansDevanagari*Bold*.ttf",
                "/usr/share/fonts/**/Mukta*Bold*.ttf", "/usr/share/fonts/**/*Devanagari*.ttf",
                os.path.join(windows_fonts, "Nirmala*.ttf"), os.path.join(windows_fonts, "*Devanagari*.ttf")]:
        f = glob.glob(pat, recursive=True)
        if f: return f[0]
    raise SystemExit("No Devanagari font found (apt install fonts-noto-core)")

def wrap(draw, text, f, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= maxw: cur = t
        else: lines.append(cur); cur = w
    return [x for x in (lines + [cur]) if x] or [""]

@functools.lru_cache(None)
def _fp(): return find_font()
@functools.lru_cache(None)
def font(size): return ImageFont.truetype(_fp(), size, layout_engine=ImageFont.Layout.RAQM)
_d = ImageDraw.Draw(Image.new("RGB", (8, 8)))

def cover(img, out):
    """Fill the 16:9 frame; slightly oversize + centre-crop so any corner watermark is pushed out of view."""
    im = Image.open(img).convert("RGB"); r = max(W / im.width, H / im.height) * 1.10
    im = im.resize((int(im.width * r) + 1, int(im.height * r) + 1)); l, t = (im.width - W) // 2, (im.height - H) // 2
    im = im.crop((l, t, l + W, t + H))
    im = ImageEnhance.Color(im).enhance(1.10)
    im = ImageEnhance.Contrast(im).enhance(1.06)
    im.save(out, quality=92)

def card(title, emotion, out):
    """Used only when an illustration could not be generated: designed title card, never a blank screen."""
    title = deva(title)
    c0 = S.EMOTION_COLORS.get(emotion, (60, 30, 90)); im = Image.new("RGB", (W, H)); d = ImageDraw.Draw(im)
    for y in range(H): k = y / H; d.line([(0, y), (W, y)], fill=tuple(int(c0[i] * (1 - 0.7 * k)) for i in range(3)))
    f = font(110); ls = wrap(d, title, f, 1500)[:3]; y0 = 430 - len(ls) * 70
    for i, l in enumerate(ls): d.text(((W - d.textlength(l, font=f)) / 2, y0 + i * 140), l, font=f, fill=(255, 255, 255), stroke_width=5, stroke_fill=(0, 0, 0))
    im.save(out, quality=90)

def panel(chip_l, chip_r, emotion, out):
    chip_l, chip_r = deva(chip_l), deva(chip_r)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    for y in range(640, H):                                  # bottom shade for caption readability
        d.line([(0, y), (W, y)], fill=(0, 0, 0, int(225 * (y - 640) / (H - 640))))
    for y in range(0, 170): d.line([(0, y), (W, y)], fill=(0, 0, 0, int(145 * (1 - y / 170))))
    f = font(40); wl = int(d.textlength(chip_l, font=f)) + 50
    d.rounded_rectangle((30, 28, 30 + wl, 92), 14, fill=(200, 0, 20, 245)); d.text((55, 32), chip_l, font=f, fill=(255, 255, 255))
    col = S.EMOTION_COLORS.get(emotion, (255, 190, 0)) if chip_r != "अनुमान" else (255, 170, 0)
    wr = int(d.textlength(chip_r, font=f)) + 50
    d.rounded_rectangle((W - wr - 30, 28, W - 30, 92), 14, fill=col + (245,)); d.text((W - wr - 5, 32), chip_r, font=f, fill=(255, 255, 255))
    im.save(out)

def chunks(text, a, b, exact_words=None):
    """Return short caption groups with word index and timings: (group_words, active_idx, start_s, end_s).
    Uses exact WordBoundary events from edge-tts when present, falling back to character-weighted timings."""
    words = deva(text).split()
    if not words: return []
    # If edge-tts provided exact word timings matching our words, align them seamlessly
    if exact_words and len(exact_words) == len(words):
        result = []
        for g_start in range(0, len(words), 8):
            group = words[g_start:g_start + 8]
            g_exact = exact_words[g_start:g_start + 8]
            for idx in range(len(group)):
                ws = g_exact[idx][1]
                # Extend word highlight until next word starts so there is no flicker between words
                we = g_exact[idx + 1][1] if idx + 1 < len(g_exact) else max(g_exact[idx][2], b)
                if idx == 0 and g_start == 0:
                    ws = a
                if we > ws:
                    result.append((group, idx, ws, we))
        if result:
            return result

    # Fallback: character-weighted estimation across 8-word groups
    groups = [words[i:i + 8] for i in range(0, len(words), 8)]
    weights = [sum(max(1, len(w.strip("।,!?:;"))) for w in g) for g in groups]
    total = sum(weights) or 1; cursor = a; result = []
    for group, weight in zip(groups, weights):
        end = cursor + (b - a) * weight / total
        gw = [max(1, len(w.strip("।,!?:;"))) for w in group]; gtotal = sum(gw) or 1
        wc = cursor
        for idx, (word, ww) in enumerate(zip(group, gw)):
            we = wc + (end - cursor) * ww / gtotal
            result.append((group, idx, wc, we))
            wc = we
        cursor = end
    return result

def caption(words, active_idx, out):
    """Draw the short phrase and highlight ONLY the word at `active_idx` in yellow (fixes duplicate word bug)."""
    im = Image.new("RGBA", (W, 230), (0, 0, 0, 0)); d = ImageDraw.Draw(im); f = font(56)
    widths = [d.textlength(w + (" " if i < len(words) - 1 else ""), font=f) for i, w in enumerate(words)]
    x = (W - sum(widths)) / 2; y = 76
    for i, word in enumerate(words):
        is_active = (i == active_idx)
        color = (255, 222, 45, 255) if is_active else (255, 255, 255, 255)
        d.text((x + 3, y + 4), word, font=f, fill=(0, 0, 0, 210), stroke_width=4, stroke_fill=(0, 0, 0, 210))
        d.text((x, y), word, font=f, fill=color, stroke_width=4, stroke_fill=(0, 0, 0, 255))
        x += widths[i]
    im.save(out)

def clip(comp, frames, path, variant):
    N = frames; y = "ih/2-(ih/zoom/2)"
    zin, zout = f"1+0.13*on/{N}", f"1.13-0.13*on/{N}"
    z, x, y = [(zin, "iw/2-(iw/zoom/2)", y), (zout, "iw/2-(iw/zoom/2)", y),
               ("1.14", f"(iw-iw/zoom)*on/{N}", y), ("1.14", f"(iw-iw/zoom)*(1-on/{N})", y),
               (zin, "0", "0"), (zin, "iw-iw/zoom", "ih-ih/zoom")][variant % 6]
    vf = f"scale=2400:1350,zoompan=z='{z}':x='{x}':y='{y}':d={N}:s={W}x{H}:fps={FPS},format=yuv420p"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", comp, "-vf", vf, "-frames:v", str(N),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-an", path], check=True)

def segment(tag, chip_l, chip_r, emotion, lines, times, audio, comps, tmp, out, offset, total, v0=0, word_times=None):
    D = times[-1][1]; n = max(1, round(D / S.CUT_SECONDS)); bounds = [D * k / n for k in range(n + 1)]; paths = []
    for k in range(n):
        p = f"{tmp}/{tag}_c{k}.mp4"; clip(comps[k % len(comps)], max(round(bounds[k + 1] * FPS) - round(bounds[k] * FPS), 2), p, v0 * 2 + k); paths.append(p)
    open(f"{tmp}/{tag}_c.txt", "w").write("".join(f"file '{os.path.abspath(p)}'\n" for p in paths))
    bg = f"{tmp}/{tag}_bg.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{tmp}/{tag}_c.txt", "-c", "copy", bg], check=True)
    panel(chip_l, chip_r, emotion, f"{tmp}/{tag}_panel.png")
    bar = f"{tmp}/bar.png"
    if not os.path.exists(bar): Image.new("RGBA", (W, 12), (255, 200, 0, 245)).save(bar)
    ins = ["-i", bg, "-i", audio, "-i", f"{tmp}/{tag}_panel.png", "-i", bar]; flt = "[0:v][2:v]overlay=0:0[v0];"; n_ov = 0
    wt_list = word_times if word_times and len(word_times) == len(lines) else [None] * len(lines)
    for li, (ln, (a, b), exact_w) in enumerate(zip(lines, times, wt_list)):
        for gi, (words, active_idx, ta, tb) in enumerate(chunks(ln, a, b, exact_words=exact_w)):
            cp = f"{tmp}/{tag}_cap{li}_{gi}.png"
            caption(words, active_idx, cp); ins += ["-i", cp]
            flt += f"[v{n_ov}][{4 + n_ov}:v]overlay=0:830:enable='between(t,{ta:.3f},{tb:.3f})'[v{n_ov + 1}];"; n_ov += 1
    flt += f"[v{n_ov}][3:v]overlay=x='-{W}+{W}*(t+{offset:.3f})/{total:.3f}':y={H - 12}[outv]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + ins + ["-filter_complex", flt, "-map", "[outv]", "-map", "1:a",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-maxrate", "5000k", "-bufsize", "10000k", "-r", str(FPS), "-g", str(FPS * 2),
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "160k", "-t", f"{D:.3f}", out], check=True)
    for p in paths + [bg]: os.remove(p)
    return D

def pick_music(emotion=None):
    """Find background music tracks in /music or the project root directory."""
    tr = (glob.glob(os.path.join(S.BASE, "music", "*.mp3")) +
          glob.glob(os.path.join(S.BASE, "music", "*.m4a")) +
          glob.glob(os.path.join(S.BASE, "music", "*.wav")))
    if not tr:
        tr = (glob.glob(os.path.join(S.BASE, "*.mp3")) +
              glob.glob(os.path.join(S.BASE, "*.m4a")) +
              glob.glob(os.path.join(S.BASE, "*.wav")))
    if tr and emotion:
        matched = [f for f in tr if emotion.lower() in os.path.basename(f).lower()]
        if matched: return random.choice(matched)
    return random.choice(tr) if tr else None

def generate_original_music(out, total, emotion="tense"):
    """Synthesize a mood-matched Indian TV serial dramatic score with rhythmic pulse & harmony pads via FFmpeg."""
    total = max(float(total), 8.0)
    # Emotion-tailored harmony pads + rhythmic heartbeat pulse frequency
    chords = {
        "tense":    [(110.0, 0.40), (130.81, 0.30), (155.56, 0.25), (220.0, 0.25)],
        "sad":      [(110.0, 0.45), (130.81, 0.35), (164.81, 0.30), (220.0, 0.20)],
        "happy":    [(130.81, 0.40), (164.81, 0.35), (196.00, 0.30), (261.63, 0.25)],
        "romantic": [(146.83, 0.40), (185.00, 0.35), (220.00, 0.30), (293.66, 0.20)],
        "shock":    [(110.0, 0.42), (116.54, 0.35), (155.56, 0.30), (207.65, 0.25)],
        "angry":    [(110.0, 0.45), (123.47, 0.35), (155.56, 0.30), (220.00, 0.25)],
    }
    emo_key = (emotion or "tense").lower()
    notes = chords.get(emo_key, chords["tense"])
    pulse_hz = 2.0 if emo_key in ("shock", "angry", "tense") else (1.1 if emo_key == "sad" else 1.5)

    inputs = []
    for freq, _ in notes:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={total:.3f}"]
    # Low rhythmic suspense heartbeat pulse (65 Hz sub-bass)
    inputs += ["-f", "lavfi", "-i", f"sine=frequency=65.41:sample_rate=44100:duration={total:.3f}"]
    # Subtle warm atmospheric string-like texture
    inputs += ["-f", "lavfi", "-i", f"anoisesrc=sample_rate=44100:duration={total:.3f}:amplitude=0.012:color=pink"]

    filters = []
    for i, (_, vol) in enumerate(notes):
        filters.append(f"[{i}:a]volume={vol:.2f},lowpass=f={450 + i * 150},tremolo=f={0.4 + i * 0.15}:d=0.25[a{i}]")
    p_idx = len(notes)
    n_idx = len(notes) + 1
    filters.append(f"[{p_idx}:a]volume=0.38,lowpass=f=140,tremolo=f={pulse_hz}:d=0.85[ap]")
    filters.append(f"[{n_idx}:a]bandpass=f=700:width_type=h:w=250,volume=0.12[an]")

    mix_ins = "".join(f"[a{i}]" for i in range(len(notes))) + "[ap][an]"
    filters.append(f"{mix_ins}amix=inputs={len(notes) + 2}:normalize=0,chorus=0.7:0.9:55:0.4:0.25:2,"
                   f"afade=t=in:st=0:d=1.5,afade=t=out:st={max(total - 2.5, 0):.3f}:d=2.5,alimiter=limit=0.9[out]")

    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + inputs + ["-filter_complex", ";".join(filters),
           "-map", "[out]", "-ac", "2", "-b:a", "160k", out]
    subprocess.run(cmd, check=True)
    return out

def auto_music(tmp, total, emotion="tense"):
    """Automatically find or synthesize background music without requiring any user-provided files."""
    music = pick_music(emotion)
    if not music:
        out_bed = os.path.join(tmp, f"auto_ambient_{emotion or 'tense'}.m4a")
        music = generate_original_music(out_bed, total, emotion=emotion)
    return music

def concat(files, tmp, out, music=None, total=0, emotion="tense", audio_bitrate="160k"):
    with open(f"{tmp}/final.txt", "w", encoding="utf-8") as concat_file:
        concat_file.write("".join(f"file '{os.path.abspath(p)}'\n" for p in files))
    if not music:
        music = auto_music(tmp, total, emotion=emotion)
    raw = out + ".nomusic.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", f"{tmp}/final.txt", "-c", "copy", "-movflags", "+faststart", raw], check=True)
    vol = getattr(S, "MUSIC_VOLUME", 0.16)
    fade_out_st = max(total - 2.5, 0)
    # Try sidechain auto-ducking so music dips under speech and swells in pauses; fallback to direct amix if needed
    duck_flt = (f"[0:a]asplit=2[vo][sc];"
                f"[1:a]volume={vol * 1.35:.2f},afade=t=in:d=1.5,afade=t=out:st={fade_out_st:.2f}:d=2.5[m_raw];"
                f"[m_raw][sc]sidechaincompress=threshold=0.03:ratio=4:attack=50:release=400[m_duck];"
                f"[vo][m_duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
    res = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-stream_loop", "-1", "-i", music, "-filter_complex",
                          duck_flt, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart", out])
    if res.returncode != 0:
        basic_flt = (f"[1:a]volume={vol:.2f},afade=t=in:d=1.5,afade=t=out:st={fade_out_st:.2f}:d=2.5[m];"
                     f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-stream_loop", "-1", "-i", music, "-filter_complex",
                        basic_flt, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart", out], check=True)
    os.remove(raw)

def thumbnail(img, hook, tag_text, bottom_text, out):
    hook, tag_text, bottom_text = deva(hook), deva(tag_text), deva(bottom_text)
    im = Image.open(img).convert("RGB"); r = max(1280 / im.width, 720 / im.height)
    im = im.resize((int(im.width * r) + 1, int(im.height * r) + 1)); l, t = (im.width - 1280) // 2, (im.height - 720) // 2
    im = im.crop((l, t, l + 1280, t + 720))
    # Boost color vibrance, contrast, and sharpness for higher mobile click-through rate
    im = ImageEnhance.Color(im).enhance(1.25)
    im = ImageEnhance.Contrast(im).enhance(1.15)
    im = ImageEnhance.Sharpness(im).enhance(1.15)
    d = ImageDraw.Draw(im, "RGBA")
    for y in range(280, 720): d.line([(0, y), (1280, y)], fill=(0, 0, 0, int(235 * (y - 280) / 440)))
    for y in range(0, 120): d.line([(0, y), (1280, y)], fill=(0, 0, 0, int(130 * (1 - y / 120))))
    # High-impact hook text with drop shadow + thick black stroke
    f = font(118); ls = wrap(d, hook, f, 1200)[:2]; y0 = 555 - len(ls) * 128
    for i, line in enumerate(ls):
        yy = y0 + i * 128
        d.text((44, yy + 5), line, font=f, fill=(0, 0, 0, 230), stroke_width=8, stroke_fill=(0, 0, 0, 230))
        d.text((40, yy), line, font=f, fill=(255, 228, 0), stroke_width=7, stroke_fill=(0, 0, 0))
    # Top-left show badge
    f2 = font(52); w = int(d.textlength(tag_text, font=f2)) + 50
    d.rounded_rectangle((30, 26, 30 + w, 98), 16, fill=(210, 0, 20, 248)); d.text((55, 29), tag_text, font=f2, fill=(255, 255, 255))
    # Top-right shock badge ("महा-ट्विस्ट!")
    twist_txt = "महा-ट्विस्ट!"
    wt = int(d.textlength(twist_txt, font=f2)) + 46
    d.rounded_rectangle((1280 - wt - 30, 26, 1250, 98), 16, fill=(255, 195, 0, 248))
    d.text((1280 - wt - 7, 29), twist_txt, font=f2, fill=(15, 15, 15))
    # Bottom-right episode & date badge
    f3 = font(46); w3 = int(d.textlength(bottom_text, font=f3)) + 40
    d.rounded_rectangle((1280 - w3 - 30, 648, 1250, 704), 12, fill=(255, 255, 255, 240)); d.text((1280 - w3 - 10, 647), bottom_text, font=f3, fill=(20, 20, 20))
    im.convert("RGB").save(out, quality=90)
