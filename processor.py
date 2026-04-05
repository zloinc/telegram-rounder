import asyncio
import math
import os
import re
import shutil
import tempfile

FFMPEG = (
    os.getenv("FFMPEG_PATH")
    or shutil.which("ffmpeg")
    or "/opt/homebrew/bin/ffmpeg"
)

BASE_DIR = os.path.dirname(__file__)
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
FFPROBE = (
    os.getenv("FFPROBE_PATH")
    or shutil.which("ffprobe")
    or "/opt/homebrew/bin/ffprobe"
)

FONT_PATHS: dict[str, list[str]] = {
    "helvetica": [
        os.path.join(FONTS_DIR, "IskraCYR-Regular.otf"),
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "verdana": [
        os.path.join(FONTS_DIR, "gillsanslightc.otf"),
        "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Verdana_Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "georgia": [
        os.path.join(FONTS_DIR, "Rodchenko Bold.otf"),
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Georgia_Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    ],
    "impact": [
        os.path.join(FONTS_DIR, "FORTNITE SHA_0.otf"),
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
}

_FALLBACK_FONTS = [
    os.path.join(FONTS_DIR, "IskraCYR-Regular.otf"),
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

FONT_SIZES = {"S": 26, "M": 34, "L": 44}
_TRAILING_CURVE_PUNCT = set(",.!?:;)]}\"»…")
_SPACE_ADVANCE_EM = 0.35
async def probe_video(input_path: str) -> dict:
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        input_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode()}")
    import json

    return json.loads(stdout.decode() or "{}")


async def create_style_preview_image(
    output_path: str,
    *,
    caption: str,
    text_color: tuple = (255, 255, 255, 255),
    font_name: str = "helvetica",
    font_size_name: str = "M",
    position: str = "bottom",
    text_bg: bool = False,
    size: int = 640,
):
    from PIL import Image, ImageDraw

    with tempfile.TemporaryDirectory() as temp_dir:
        overlay_path = os.path.join(temp_dir, "preview-overlay.png")
        await _create_curved_text_overlay(
            text=caption,
            size=size,
            output_path=overlay_path,
            text_color=text_color,
            font_name=font_name,
            font_size_name=font_size_name,
            position=position,
            text_bg=text_bg,
        )

        base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(base)
        draw.ellipse((0, 0, size - 1, size - 1), fill=(248, 249, 251, 255))
        draw.ellipse((16, 16, size - 17, size - 17), fill=(255, 255, 255, 255))
        draw.ellipse((28, 28, size - 29, size - 29), fill=(255, 255, 255, 255))

        overlay = Image.open(overlay_path).convert("RGBA")
        base.alpha_composite(overlay)
        base.save(output_path, "PNG")


async def video_to_circle(
    input_path: str,
    output_path: str,
    caption: str | None = None,
    word_timings: list[dict] | None = None,
    text_color: tuple = (255, 255, 255, 255),
    font_name: str = "helvetica",
    font_size_name: str = "M",
    position: str = "bottom",
    text_bg: bool = False,
    size: int = 640,
) -> str:
    render_kwargs = dict(
        text_color=text_color,
        font_name=font_name,
        font_size_name=font_size_name,
        position=position,
        text_bg=text_bg,
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        if word_timings:
            return await _process_with_dynamic_captions(
                input_path, output_path, word_timings, size, temp_dir, **render_kwargs
            )
        if caption:
            return await _process_with_caption(
                input_path, output_path, caption, size, temp_dir, **render_kwargs
            )
        return await _process_without_caption(input_path, output_path, size, temp_dir)


async def _create_circle_mask(size: int, output_path: str):
    from PIL import Image, ImageDraw
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size, size), fill=255)
    img.save(output_path)


async def _process_without_caption(
    input_path: str, output_path: str, size: int, temp_dir: str
) -> str:
    mask_path = os.path.join(temp_dir, "mask.png")
    await _create_circle_mask(size, mask_path)

    filter_complex = (
        f"[0:v]scale={size}:{size}:force_original_aspect_ratio=increase,"
        f"crop={size}:{size}[cropped];"
        f"[1:v]format=gray[mask];"
        f"[cropped]format=rgba[rgb];"
        f"[rgb][mask]alphamerge[v]"
    )
    cmd = [
        FFMPEG, "-y", "-i", input_path, "-i", mask_path,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-c:a", "aac",
        "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuva420p", "-movflags", "+faststart",
        "-t", "60", output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {stderr.decode()}")
    return output_path


async def _process_with_caption(
    input_path: str, output_path: str, caption: str,
    size: int, temp_dir: str, **render_kwargs
) -> str:
    circle_video = os.path.join(temp_dir, "circle.mp4")
    await _process_without_caption(input_path, circle_video, size, temp_dir)

    text_overlay = os.path.join(temp_dir, "text_overlay.png")
    await _create_curved_text_overlay(caption, size, text_overlay, **render_kwargs)

    return await _overlay_text_on_video(circle_video, text_overlay, output_path)


def _group_words_into_chunks(
    words: list[dict], max_words: int = 2, max_gap: float = 0.7
) -> list[dict]:
    if not words:
        return []
    chunks, current = [], [words[0]]
    for w in words[1:]:
        if len(current) >= max_words or (w["start"] - current[-1]["end"]) > max_gap:
            chunks.append(current)
            current = [w]
        else:
            current.append(w)
    if current:
        chunks.append(current)

    result = []
    for i, chunk in enumerate(chunks):
        start = max(0.0, chunk[0]["start"])
        word_end = chunk[-1]["end"]
        if i + 1 < len(chunks):
            end = min(word_end, chunks[i + 1][0]["start"] - 0.02)
        else:
            end = word_end + 0.1
        result.append({
            "text": _join_caption_tokens([w["word"] for w in chunk]),
            "start": start,
            "end": max(start + 0.1, end),
        })
    return result


def _join_caption_tokens(tokens: list[str]) -> str:
    text = " ".join(token.strip() for token in tokens if token and token.strip())
    text = re.sub(r"\s+([,.!?:;)\]}\"»…])", r"\1", text)
    text = re.sub(r"([(\[{\"«])\s+", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def _process_with_dynamic_captions(
    input_path: str, output_path: str, word_timings: list[dict],
    size: int, temp_dir: str, **render_kwargs
) -> str:
    circle_video = os.path.join(temp_dir, "circle.mp4")
    await _process_without_caption(input_path, circle_video, size, temp_dir)

    chunks = _group_words_into_chunks(word_timings)
    if not chunks:
        return circle_video

    overlays = []
    for i, chunk in enumerate(chunks):
        png_path = os.path.join(temp_dir, f"chunk_{i:03d}.png")
        await _create_curved_text_overlay(chunk["text"], size, png_path, **render_kwargs)
        overlays.append((png_path, chunk["start"], chunk["end"]))

    cmd = [FFMPEG, "-y", "-i", circle_video]
    for png_path, _, _ in overlays:
        cmd += ["-i", png_path]

    filter_parts = []
    for i, (_, start, end) in enumerate(overlays):
        src = "[0:v]" if i == 0 else f"[v{i - 1}]"
        dst = "[v]" if i == len(overlays) - 1 else f"[v{i}]"
        filter_parts.append(
            f"{src}[{i + 1}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'{dst}"
        )

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-c:a", "aac",
        "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuva420p", "-movflags", "+faststart",
        "-t", "60", output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg dynamic captions failed: {stderr.decode()}")
    return output_path


async def _overlay_text_on_video(
    circle_video: str, text_overlay: str, output_path: str
) -> str:
    filter_complex = (
        "[0:v]format=yuva420p[v0];"
        "[1:v]format=yuva420p[v1];"
        "[v0][v1]overlay=0:0[v]"
    )
    cmd = [
        FFMPEG, "-y", "-i", circle_video, "-i", text_overlay,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-c:a", "aac",
        "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuva420p", "-movflags", "+faststart",
        "-t", "60", output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg overlay failed: {stderr.decode()}")
    return output_path


def _load_font(font_name: str = "helvetica", font_size: int = 34):
    from PIL import ImageFont
    paths = FONT_PATHS.get(font_name, []) + _FALLBACK_FONTS
    for fp in paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, font_size)
            except Exception:
                pass
    return ImageFont.load_default()


def _curve_units(text: str) -> list[dict]:
    units: list[dict] = []
    for char in text:
        if char.isspace():
            if units and units[-1]["kind"] == "space":
                continue
            units.append({"text": " ", "kind": "space"})
            continue
        if char in _TRAILING_CURVE_PUNCT:
            for prev in reversed(units):
                if prev["kind"] == "text":
                    prev["text"] += char
                    break
            else:
                units.append({"text": char, "kind": "text"})
            continue
        units.append({"text": char, "kind": "text"})
    return units


async def _create_curved_text_overlay(
    text: str,
    size: int,
    output_path: str,
    text_color: tuple = (255, 255, 255, 255),
    font_name: str = "helvetica",
    font_size_name: str = "M",
    position: str = "bottom",
    text_bg: bool = False,
):
    from PIL import Image, ImageDraw, ImageFilter

    scale = 3
    canvas_size = size * scale
    font_size = FONT_SIZES.get(font_size_name, 34) * scale
    font = _load_font(font_name, font_size)

    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    text_layer = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    text_mask = Image.new("L", (canvas_size, canvas_size), 0)
    draw = ImageDraw.Draw(img)

    center_x = canvas_size // 2
    center_y = canvas_size // 2
    radius_inset = max(
        (88 if position == "bottom" else 72) * scale,
        font_size + 40 * scale,
    )
    radius = canvas_size // 2 - radius_inset

    units = _curve_units(text)
    metrics = []
    for unit in units:
        if unit["kind"] == "space":
            advance = max(1, int(font_size * _SPACE_ADVANCE_EM))
            metrics.append({"text": " ", "kind": "space", "advance": advance})
            continue
        bbox = draw.textbbox((0, 0), unit["text"], font=font)
        metrics.append(
            {
                "text": unit["text"],
                "kind": "text",
                "bbox": bbox,
                "width": max(1, bbox[2] - bbox[0] + 1),
                "height": max(1, bbox[3] - bbox[1] + 1),
                "advance": max(1, bbox[2] - bbox[0] + 1),
            }
        )

    total_text_width = sum(item["advance"] for item in metrics)
    if total_text_width == 0:
        img.resize((size, size), Image.Resampling.LANCZOS).save(output_path, "PNG")
        return

    total_angle = min(math.radians(112), total_text_width / radius)
    half_angle = total_angle / 2

    if position == "top":
        # Arc along the top of the circle
        arc_center_angle = -math.pi / 2
        arc_start = arc_center_angle - half_angle  # leftmost
        def get_angle(t): return arc_start + t * total_angle
        def get_rotation(a): return 270 - math.degrees(a)
    else:
        # Arc along the bottom of the circle
        arc_center_angle = math.pi / 2
        arc_start = arc_center_angle + half_angle  # leftmost
        def get_angle(t): return arc_start - t * total_angle
        def get_rotation(a): return 90 - math.degrees(a)

    # Draw each character rotated tangentially
    padding = max(18 * scale, font_size // 2)
    cumulative_width = 0

    for item in metrics:
        advance = item["advance"]
        t = (cumulative_width + advance / 2) / total_text_width
        cumulative_width += advance

        if item["kind"] == "space":
            continue

        angle = get_angle(t)
        rotation_deg = get_rotation(angle)

        x = int(center_x + radius * math.cos(angle))
        y = int(center_y + radius * math.sin(angle))

        bbox = item["bbox"]
        unit_w = item["width"]
        unit_h = item["height"]
        unit_img_w = max(1, unit_w + padding * 2)
        unit_img_h = max(1, unit_h + padding * 2)
        unit_img = Image.new("RGBA", (unit_img_w, unit_img_h), (0, 0, 0, 0))
        unit_draw = ImageDraw.Draw(unit_img)
        mask_img = Image.new("L", (unit_img_w, unit_img_h), 0)
        mask_draw = ImageDraw.Draw(mask_img)

        # textbbox may have negative origin for accents/descenders;
        # compensate for that so glyphs are fully drawn before rotation
        ox = padding - bbox[0]
        oy = padding - bbox[1]
        mask_draw.text((ox, oy), item["text"], font=font, fill=255)

        # Drop shadow (modern look instead of outline)
        unit_draw.text((ox + 4, oy + 6), item["text"], font=font, fill=(0, 0, 0, 140))
        # Main text
        unit_draw.text((ox, oy), item["text"], font=font, fill=text_color)

        rotated = unit_img.rotate(rotation_deg, expand=True, resample=Image.Resampling.BICUBIC)
        rw, rh = rotated.size
        rotated_mask = mask_img.rotate(
            rotation_deg,
            expand=True,
            resample=Image.Resampling.BICUBIC,
        )
        text_layer.paste(rotated, (x - rw // 2, y - rh // 2), rotated)
        text_mask.paste(rotated_mask, (x - rw // 2, y - rh // 2), rotated_mask)

    if text_bg:
        spread = max(scale * 17, int(font_size * 0.34))
        spread = spread + 1 if spread % 2 == 0 else spread
        bbox = text_mask.getbbox()
        if bbox:
            blur_radius = max(3, scale * 2)
            pad = spread + blur_radius * 3
            left = max(0, bbox[0] - pad)
            top = max(0, bbox[1] - pad)
            right = min(canvas_size, bbox[2] + pad)
            bottom = min(canvas_size, bbox[3] + pad)

            cropped_mask = text_mask.crop((left, top, right, bottom))
            cropped_mask = cropped_mask.filter(ImageFilter.MaxFilter(spread))
            cropped_mask = cropped_mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            bg_alpha = cropped_mask.point(lambda p: min(156, p))

            bg_layer = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            bg_crop = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
            bg_crop.putalpha(bg_alpha)
            bg_layer.paste(bg_crop, (left, top), bg_crop)
            img.alpha_composite(bg_layer)

    img.alpha_composite(text_layer)

    img = img.resize((size, size), Image.Resampling.LANCZOS)
    img.save(output_path, "PNG")
