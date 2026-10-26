"""Portrait renderer built from the shared narration, word timings, and source illustrations."""
import json
import os
import subprocess

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import config as S
import render

W, H, FPS = S.VERTICAL_W, S.VERTICAL_H, S.FPS
CAPTION_TOP = 1470
CAPTION_HEIGHT = 230
CAPTION_MARGIN = 58


def caption_position():
    return CAPTION_TOP, CAPTION_HEIGHT


def compose_frame(image_path, out_path, header_left, header_right, channel_name):
    source = Image.open(image_path).convert("RGB")
    scale = max(W / source.width, H / source.height)
    background = source.resize((int(source.width * scale), int(source.height * scale)))
    left = (background.width - W) // 2
    top = (background.height - H) // 2
    background = background.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(34))
    background = ImageEnhance.Brightness(background).enhance(0.5).convert("RGBA")
    foreground = source.copy()
    foreground.thumbnail((W - 110, 1120), Image.Resampling.LANCZOS)
    frame = background
    x = (W - foreground.width) // 2
    y = 300 + (1120 - foreground.height) // 2
    frame.alpha_composite(foreground.convert("RGBA"), (x, y))

    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    for row in range(H):
        if row < 310:
            alpha = int(210 * (1 - row / 310))
            draw.line((0, row, W, row), fill=(0, 0, 0, alpha))
        elif row >= CAPTION_TOP - 70:
            alpha = min(210, int(210 * (row - CAPTION_TOP + 70) / 450))
            draw.line((0, row, W, row), fill=(0, 0, 0, alpha))
    frame.alpha_composite(shade)

    draw = ImageDraw.Draw(frame)
    font = render.font(44)
    left_text = render.deva(header_left)
    right_text = render.deva(header_right)
    draw.rounded_rectangle((42, 48, W - 42, 122), radius=22, fill=(175, 0, 20, 238))
    draw.text((W // 2, 60), left_text, anchor="mt", font=font, fill="white",
              stroke_width=2, stroke_fill=(0, 0, 0))
    badge_font = render.font(38)
    right_w = min(int(draw.textlength(right_text, font=badge_font)) + 46, W - 100)
    draw.rounded_rectangle((W - right_w - 42, 145, W - 42, 210), radius=18,
                           fill=(255, 188, 0, 238))
    draw.text((W - right_w / 2 - 42, 155), right_text, anchor="mt", font=badge_font,
              fill=(20, 20, 20), stroke_width=1, stroke_fill="white")

    channel_font = render.font(34)
    channel = render.deva(channel_name)
    draw.text((W // 2, 250), channel, anchor="mm", font=channel_font,
              fill=(255, 255, 255, 235), stroke_width=2, stroke_fill=(0, 0, 0, 230))
    frame.convert("RGB").save(out_path, quality=94)


def _caption_image(words, active, out_path):
    image = Image.new("RGBA", (W - 2 * CAPTION_MARGIN, CAPTION_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font_size = 50
    while True:
        font = render.font(font_size)
        rows = [[]]
        width = 0
        for index, word in enumerate(words):
            text = render.deva(word) + (" " if index < len(words) - 1 else "")
            word_width = draw.textlength(text, font=font)
            if rows[-1] and width + word_width > image.width - 30:
                rows.append([])
                width = 0
            rows[-1].append((text, index))
            width += word_width
        if len(rows) <= 2 or font_size <= 34:
            break
        font_size -= 2
    line_height = 78
    y = max(12, (CAPTION_HEIGHT - line_height * len(rows)) // 2)
    for row in rows[:2]:
        row_width = sum(draw.textlength(text, font=font) for text, _ in row)
        x = (image.width - row_width) / 2
        for text, index in row:
            color = (255, 222, 45, 255) if index == active else (255, 255, 255, 255)
            draw.text((x, y), text, font=font, fill=color, stroke_width=4,
                      stroke_fill=(0, 0, 0, 255))
            x += draw.textlength(text, font=font)
        y += line_height
    image.save(out_path)


def _segment(segment, image_path, tmp, out_path):
    duration = float(segment["times"][-1][1])
    frame_path = os.path.join(tmp, f"vertical_{segment['tag']}_frame.jpg")
    compose_frame(image_path, frame_path, segment.get("title", S.CHANNEL_NAME),
                  segment.get("chip", ""), S.CHANNEL_NAME)
    inputs = ["-loop", "1", "-framerate", str(FPS), "-i", frame_path, "-i", segment["audio"]]
    filters = [f"[0:v]zoompan=z='min(zoom+0.00035,1.035)':d=1:s={W}x{H}:fps={FPS},format=yuv420p[v0]"]
    last = "v0"
    count = 0
    word_times = segment.get("word_times") or [None] * len(segment["lines"])
    for line_index, (line, (start, end), exact_words) in enumerate(
            zip(segment["lines"], segment["times"], word_times)):
        for group_index, (words, active, cue_start, cue_end) in enumerate(
                render.chunks(line, start, end, exact_words=exact_words)):
            caption_path = os.path.join(tmp, f"vertical_{segment['tag']}_{line_index}_{group_index}.png")
            _caption_image(words, active, caption_path)
            inputs.extend(["-loop", "1", "-framerate", str(FPS), "-i", caption_path])
            next_label = f"v{count + 1}"
            filters.append(
                f"[{last}][{2 + count}:v]overlay={CAPTION_MARGIN}:{CAPTION_TOP}:"
                f"enable='between(t,{cue_start:.3f},{cue_end:.3f})':eof_action=pass[{next_label}]"
            )
            last = next_label
            count += 1
    filters.append(f"[{last}]format=yuv420p,trim=duration={duration:.3f},setpts=PTS-STARTPTS[outv]")
    frame_count = max(1, round(duration * FPS))
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error"] + inputs + [
            "-filter_complex", ";".join(filters),
            "-map", "[outv]", "-map", "1:a",
            "-frames:v", str(frame_count), "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-maxrate", "12000k", "-bufsize", "24000k", "-g", str(FPS * 2), "-flags", "+cgop",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "copy",
            "-movflags", "+faststart", out_path,
        ],
        check=True,
    )
    return duration


def render_episode(segments, images, tmp, out_path, music, total, emotion="tense"):
    os.makedirs(tmp, exist_ok=True)
    clips = []
    for index, segment in enumerate(segments):
        image_path = images[segment["img"]]
        clip_path = os.path.join(tmp, f"vertical_seg_{index:02d}.mp4")
        duration = _segment(segment, image_path, tmp, clip_path)
        clips.append(clip_path)
        print(f"   vertical {segment['tag']}: {duration:.0f}s")
    render.concat(clips, tmp, out_path, music, total, emotion, audio_bitrate="128k")
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", out_path,
    ], text=True))
    video = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"), {})
    if (video.get("width"), video.get("height")) != (W, H):
        raise RuntimeError(f"Vertical output has unexpected dimensions: {video.get('width')}x{video.get('height')}")
    actual_duration = float(probe.get("format", {}).get("duration", 0))
    if not actual_duration or abs(actual_duration - total) > max(0.5, 2 / FPS):
        raise RuntimeError(f"Vertical output duration mismatch: expected {total:.2f}s, got {actual_duration:.2f}s")
    if audio and abs(float(audio.get("duration", actual_duration)) - actual_duration) > 0.5:
        raise RuntimeError("Vertical audio/video duration mismatch")
    print(f"   vertical output: {W}x{H}, {actual_duration:.1f}s -> {os.path.basename(out_path)}")
    return out_path
