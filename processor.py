import asyncio
import json
import math
import os
import re
import tempfile

from config import FFMPEG, FFPROBE

BASE_DIR = os.path.dirname(__file__)
FONTS_DIR = os.path.join(BASE_DIR, "fonts")


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

PRESET_STYLES: dict[str, dict] = {
    "clean": {
        "grade": None,
        "grain": 0,
        "sharpen": 0.35,
        "ring": None,
        "animation": {"max_words": 3, "max_gap": 0.85, "cumulative": True},
    },
    "bold": {
        "grade": "warm",
        "grain": 3,
        "sharpen": 0.85,
        "ring": {"color": (255, 150, 64, 225), "glow": (255, 120, 0, 120)},
        "animation": {"max_words": 2, "max_gap": 0.65, "cumulative": True},
    },
    "meme": {
        "grade": "punchy",
        "grain": 5,
        "sharpen": 1.0,
        "ring": {"color": (64, 230, 255, 230), "glow": (0, 200, 255, 135)},
        "animation": {"max_words": 1, "max_gap": 0.55, "cumulative": True},
    },
    "cinema": {
        "grade": "noir",
        "grain": 7,
        "sharpen": 0.45,
        "ring": {"color": (255, 255, 255, 110), "glow": (255, 255, 255, 55)},
        "animation": {"max_words": 2, "max_gap": 0.95, "cumulative": False},
    },
    "editorial": {
        "grade": "cold",
        "grain": 2,
        "sharpen": 0.25,
        "ring": None,
        "animation": {"max_words": 3, "max_gap": 0.8, "cumulative": False},
    },
}

RING_STYLES: dict[str, dict[str, tuple[int, int, int, int]] | None] = {
    "white": {"color": (255, 255, 255, 170), "glow": (255, 255, 255, 80)},
    "orange": {"color": (255, 150, 64, 225), "glow": (255, 120, 0, 120)},
    "cyan": {"color": (64, 230, 255, 230), "glow": (0, 200, 255, 135)},
}
SHARPNESS_LEVELS = {
    "off": 0.0,
    "low": 0.25,
    "medium": 0.55,
    "high": 0.9,
}
GRAIN_LEVELS = {
    "off": 0,
    "low": 2,
    "medium": 5,
    "high": 8,
}
VIGNETTE_LEVELS = {
    "soft":   "PI/5",
    "medium": "PI/4",
    "strong": "PI/3",
}
FISHEYE_LEVELS = {
    "soft":   "cx=0.5:cy=0.5:k1=-0.1:k2=-0.1",
    "strong": "cx=0.5:cy=0.5:k1=-0.25:k2=-0.25",
}
CHROMA_LEVELS = {
    "subtle": 2,
    "strong": 5,
}


def _normalize_preset(preset: str | None) -> str:
    return preset if preset in PRESET_STYLES else "clean"


def _resolve_style(
    preset: str | None,
    *,
    fx_grade: str | None = None,
    fx_sharpness: str | None = None,
    fx_grain: str | None = None,
    fx_ring: str | None = None,
    fx_vignette: str | None = None,
    fx_chroma: str | None = None,
    fx_fisheye: str | None = None,
) -> dict:
    base = dict(PRESET_STYLES[_normalize_preset(preset)])
    if fx_grade and fx_grade != "auto":
        base["grade"] = None if fx_grade == "off" else fx_grade
    if fx_sharpness and fx_sharpness != "auto":
        base["sharpen"] = SHARPNESS_LEVELS.get(fx_sharpness, base["sharpen"])
    if fx_grain and fx_grain != "auto":
        base["grain"] = GRAIN_LEVELS.get(fx_grain, base["grain"])
    if fx_ring and fx_ring != "auto":
        base["ring"] = None if fx_ring == "off" else RING_STYLES.get(fx_ring, base["ring"])
    if fx_vignette and fx_vignette != "auto":
        base["vignette"] = None if fx_vignette == "off" else VIGNETTE_LEVELS.get(fx_vignette)
    if fx_chroma and fx_chroma != "auto":
        base["chroma"] = 0 if fx_chroma == "off" else CHROMA_LEVELS.get(fx_chroma, 0)
    if fx_fisheye and fx_fisheye != "auto":
        base["fisheye"] = None if fx_fisheye == "off" else FISHEYE_LEVELS.get(fx_fisheye)
    return base


def _build_style_filters(
    preset: str | None,
    *,
    fx_grade: str | None = None,
    fx_sharpness: str | None = None,
    fx_grain: str | None = None,
    fx_vignette: str | None = None,
    fx_fisheye: str | None = None,
) -> str:
    style = _resolve_style(
        preset,
        fx_grade=fx_grade,
        fx_sharpness=fx_sharpness,
        fx_grain=fx_grain,
        fx_vignette=fx_vignette,
        fx_fisheye=fx_fisheye,
    )
    filters: list[str] = []
    grade = style["grade"]
    if grade == "warm":
        filters.extend(
            [
                "eq=saturation=1.08:contrast=1.04:brightness=0.015",
                "colorbalance=rs=.05:gs=.01:bs=-.03",
            ]
        )
    elif grade == "cold":
        filters.extend(
            [
                "eq=saturation=0.96:contrast=1.05:brightness=0.0",
                "colorbalance=rs=-.035:gs=0.0:bs=.055",
            ]
        )
    elif grade == "noir":
        filters.extend(
            [
                "hue=s=0",
                "eq=contrast=1.12:brightness=-0.02",
                "curves=all='0/0 0.30/0.24 0.75/0.82 1/1'",
            ]
        )
    elif grade == "punchy":
        filters.extend(
            [
                "eq=saturation=1.25:contrast=1.12:brightness=0.01",
                "colorbalance=rs=.03:gs=.01:bs=.02",
            ]
        )

    sharpen = float(style["sharpen"])
    if sharpen > 0:
        filters.append(f"unsharp=5:5:{sharpen:.2f}:5:5:0.0")

    grain = int(style["grain"])
    if grain > 0:
        filters.append(f"noise=alls={grain}:allf=t+u")

    vignette_angle = style.get("vignette")
    if vignette_angle:
        filters.append(f"vignette=angle={vignette_angle}:mode=backward")

    fisheye_args = style.get("fisheye")
    if fisheye_args:
        filters.append(f"lenscorrection={fisheye_args}")

    return ",".join(filters)


async def _create_ring_overlay(
    size: int,
    output_path: str,
    preset: str | None,
    *,
    fx_ring: str | None = None,
):
    from PIL import Image, ImageDraw, ImageFilter

    style = _resolve_style(preset, fx_ring=fx_ring)
    ring = style.get("ring")
    if not ring:
        return

    # Draw at 2x resolution for anti-aliased edges, then downsample.
    SS = 2
    hi = size * SS
    inset = max(10, size // 28) * SS
    stroke = max(6, size // 64) * SS
    glow_width = max(18, size // 22) * SS

    img = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    glow = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (inset, inset, hi - inset - 1, hi - inset - 1),
        outline=ring["glow"],
        width=glow_width,
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(6, size // 60) * SS))
    img.alpha_composite(glow)

    draw.ellipse(
        (inset, inset, hi - inset - 1, hi - inset - 1),
        outline=ring["color"],
        width=stroke,
    )

    # Downsample with LANCZOS — smooths jagged edges
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    img.save(output_path, "PNG")


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
    preset: str = "clean",
    fx_grade: str | None = None,
    fx_sharpness: str | None = None,
    fx_grain: str | None = None,
    fx_ring: str | None = None,
    fx_vignette: str | None = None,
    fx_chroma: str | None = None,
    fx_fisheye: str | None = None,
):
    from PIL import Image, ImageDraw

    with tempfile.TemporaryDirectory() as temp_dir:
        base_path = os.path.join(temp_dir, "preview-base.png")
        styled_base_path = os.path.join(temp_dir, "preview-base-styled.png")
        overlay_path = os.path.join(temp_dir, "preview-overlay.png")
        ring_path = os.path.join(temp_dir, "preview-ring.png")

        base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(base)
        draw.ellipse((0, 0, size - 1, size - 1), fill=(248, 249, 251, 255))
        draw.ellipse((16, 16, size - 17, size - 17), fill=(255, 255, 255, 255))
        draw.ellipse((28, 28, size - 29, size - 29), fill=(255, 255, 255, 255))
        base.save(base_path, "PNG")

        await _apply_style_to_image(
            input_path=base_path,
            output_path=styled_base_path,
            preset=preset,
            fx_grade=fx_grade,
            fx_sharpness=fx_sharpness,
            fx_grain=fx_grain,
            fx_vignette=fx_vignette,
            fx_fisheye=fx_fisheye,
            size=size,
        )
        await _create_curved_text_overlay(
            text=caption,
            size=size,
            output_path=overlay_path,
            text_color=text_color,
            font_name=font_name,
            font_size_name=font_size_name,
            position=position,
            text_bg=text_bg,
            fx_chroma=fx_chroma,
        )
        await _create_ring_overlay(size, ring_path, preset, fx_ring=fx_ring)

        base = Image.open(styled_base_path).convert("RGBA")
        if os.path.exists(ring_path):
            ring = Image.open(ring_path).convert("RGBA")
            base.alpha_composite(ring)
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
    preset: str = "clean",
    fx_grade: str | None = None,
    fx_sharpness: str | None = None,
    fx_grain: str | None = None,
    fx_ring: str | None = None,
    fx_vignette: str | None = None,
    fx_chroma: str | None = None,
    fx_fisheye: str | None = None,
) -> str:
    render_kwargs = dict(
        text_color=text_color,
        font_name=font_name,
        font_size_name=font_size_name,
        position=position,
        text_bg=text_bg,
        preset=preset,
        fx_grade=fx_grade,
        fx_sharpness=fx_sharpness,
        fx_grain=fx_grain,
        fx_ring=fx_ring,
        fx_vignette=fx_vignette,
        fx_chroma=fx_chroma,
        fx_fisheye=fx_fisheye,
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
        return await _process_without_caption(
            input_path, output_path, size, temp_dir, preset=preset,
            fx_grade=fx_grade, fx_sharpness=fx_sharpness, fx_grain=fx_grain,
            fx_ring=fx_ring, fx_vignette=fx_vignette, fx_fisheye=fx_fisheye,
        )


async def _create_circle_mask(size: int, output_path: str):
    from PIL import Image, ImageDraw
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    img.save(output_path)


async def _process_without_caption(
    input_path: str,
    output_path: str,
    size: int,
    temp_dir: str,
    *,
    preset: str = "clean",
    fx_grade: str | None = None,
    fx_sharpness: str | None = None,
    fx_grain: str | None = None,
    fx_ring: str | None = None,
    fx_vignette: str | None = None,
    fx_fisheye: str | None = None,
) -> str:
    mask_path = os.path.join(temp_dir, "mask.png")
    await _create_circle_mask(size, mask_path)
    style_filters = _build_style_filters(
        preset,
        fx_grade=fx_grade,
        fx_sharpness=fx_sharpness,
        fx_grain=fx_grain,
        fx_vignette=fx_vignette,
        fx_fisheye=fx_fisheye,
    )
    ring_path = os.path.join(temp_dir, "ring.png")
    await _create_ring_overlay(size, ring_path, preset, fx_ring=fx_ring)

    video_chain = (
        f"scale={size}:{size}:force_original_aspect_ratio=increase,"
        f"crop={size}:{size}"
    )
    if style_filters:
        video_chain = f"{video_chain},{style_filters}"

    filter_complex = [
        f"[0:v]{video_chain}[cropped]",
        "[1:v]format=gray[mask]",
        "[cropped]format=rgba[rgb]",
        "[rgb][mask]alphamerge[base]",
    ]
    cmd = [
        FFMPEG, "-y", "-i", input_path, "-i", mask_path,
    ]
    if os.path.exists(ring_path):
        cmd += ["-i", ring_path]
        filter_complex.append("[2:v]format=rgba[ring]")
        filter_complex.append("[base][ring]overlay=0:0[v]")
    else:
        filter_complex.append("[base]copy[v]")

    cmd += [
        "-filter_complex", ";".join(filter_complex),
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


async def _apply_style_to_image(
    *,
    input_path: str,
    output_path: str,
    preset: str = "clean",
    fx_grade: str | None = None,
    fx_sharpness: str | None = None,
    fx_grain: str | None = None,
    fx_vignette: str | None = None,
    fx_fisheye: str | None = None,
    size: int = 640,
) -> str:
    style_filters = _build_style_filters(
        preset,
        fx_grade=fx_grade,
        fx_sharpness=fx_sharpness,
        fx_grain=fx_grain,
        fx_vignette=fx_vignette,
        fx_fisheye=fx_fisheye,
    )
    if not style_filters:
        from shutil import copyfile

        copyfile(input_path, output_path)
        return output_path

    cmd = [
        FFMPEG,
        "-y",
        "-i",
        input_path,
        "-vf",
        style_filters,
        "-frames:v",
        "1",
        "-update",
        "1",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg preview styling failed: {stderr.decode()}")
    return output_path


async def _process_with_caption(
    input_path: str, output_path: str, caption: str,
    size: int, temp_dir: str, **render_kwargs
) -> str:
    circle_video = os.path.join(temp_dir, "circle.mp4")
    await _process_without_caption(
        input_path,
        circle_video,
        size,
        temp_dir,
        preset=render_kwargs.get("preset", "clean"),
        fx_grade=render_kwargs.get("fx_grade"),
        fx_sharpness=render_kwargs.get("fx_sharpness"),
        fx_grain=render_kwargs.get("fx_grain"),
        fx_ring=render_kwargs.get("fx_ring"),
        fx_vignette=render_kwargs.get("fx_vignette"),
        fx_fisheye=render_kwargs.get("fx_fisheye"),
    )

    text_overlay = os.path.join(temp_dir, "text_overlay.png")
    await _create_curved_text_overlay(caption, size, text_overlay, **render_kwargs)

    return await _overlay_text_on_video(circle_video, text_overlay, output_path)


def _group_words_into_chunks(
    words: list[dict],
    max_words: int = 2,
    max_gap: float = 0.7,
    cumulative: bool = False,
) -> list[dict]:
    if not words:
        return []
    result = []
    current: list[dict] = []

    for idx, w in enumerate(words):
        next_word = words[idx + 1] if idx + 1 < len(words) else None
        if current and (
            len(current) >= max_words or (w["start"] - current[-1]["end"]) > max_gap
        ):
            current = []
        current.append(w)
        if cumulative:
            segment = current
        elif len(current) < max_words and next_word and (
            (next_word["start"] - w["end"]) <= max_gap
        ):
            continue
        else:
            segment = current
            current = []

        result.append(
            {
                "text": _join_caption_tokens([item["word"] for item in segment]),
                "start": max(0.0, segment[-1]["start"] if cumulative else segment[0]["start"]),
                "word_end": segment[-1]["end"],
                "cumulative": cumulative,
            }
        )

    for i, item in enumerate(result):
        if i + 1 < len(result):
            next_start = result[i + 1]["start"] - 0.02
            if item["cumulative"]:
                item["end"] = max(item["start"] + 0.1, next_start)
            else:
                item["end"] = max(item["start"] + 0.1, min(item["word_end"], next_start))
        else:
            item["end"] = item["word_end"] + 0.1
        item.pop("word_end", None)
        item.pop("cumulative", None)
    return result


def _join_caption_tokens(tokens: list[str]) -> str:
    text = " ".join(token.strip() for token in tokens if token and token.strip())
    text = re.sub(r"\s+([,.!?:;)\]}\"»…])", r"\1", text)
    text = re.sub(r"([(\[{\"«])\s+", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _apply_chroma_aberration(img, pixels: int):
    """Shift R channel right and B channel left by `pixels` for chromatic aberration."""
    if pixels <= 0:
        return img
    from PIL import Image
    r, g, b, a = img.split()
    # Shift R right, B left
    r = r.transform(r.size, Image.Transform.AFFINE, (1, 0, -pixels, 0, 1, 0))
    b = b.transform(b.size, Image.Transform.AFFINE, (1, 0,  pixels, 0, 1, 0))
    return Image.merge("RGBA", (r, g, b, a))


async def _process_with_dynamic_captions(
    input_path: str, output_path: str, word_timings: list[dict],
    size: int, temp_dir: str, **render_kwargs
) -> str:
    circle_video = os.path.join(temp_dir, "circle.mp4")
    await _process_without_caption(
        input_path,
        circle_video,
        size,
        temp_dir,
        preset=render_kwargs.get("preset", "clean"),
        fx_grade=render_kwargs.get("fx_grade"),
        fx_sharpness=render_kwargs.get("fx_sharpness"),
        fx_grain=render_kwargs.get("fx_grain"),
        fx_ring=render_kwargs.get("fx_ring"),
        fx_vignette=render_kwargs.get("fx_vignette"),
        fx_fisheye=render_kwargs.get("fx_fisheye"),
    )

    preset = render_kwargs.get("preset", "clean")
    animation = _resolve_style(
        preset,
        fx_grade=render_kwargs.get("fx_grade"),
        fx_sharpness=render_kwargs.get("fx_sharpness"),
        fx_grain=render_kwargs.get("fx_grain"),
        fx_ring=render_kwargs.get("fx_ring"),
        fx_vignette=render_kwargs.get("fx_vignette"),
        fx_chroma=render_kwargs.get("fx_chroma"),
        fx_fisheye=render_kwargs.get("fx_fisheye"),
    )["animation"]
    chunks = _group_words_into_chunks(
        word_timings,
        max_words=animation["max_words"],
        max_gap=animation["max_gap"],
        cumulative=animation["cumulative"],
    )
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
    fx_chroma: str | None = None,
    **_ignored,
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

    # Apply chromatic aberration at high-res before downsampling
    chroma_pixels = CHROMA_LEVELS.get(fx_chroma or "", 0) if fx_chroma and fx_chroma not in ("auto", "off") else 0
    if chroma_pixels > 0:
        img = _apply_chroma_aberration(img, chroma_pixels * scale)

    img = img.resize((size, size), Image.Resampling.LANCZOS)
    img.save(output_path, "PNG")
