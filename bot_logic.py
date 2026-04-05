def normalize_caption_mode(mode: str | None, auto_caption_default: bool) -> str:
    if mode in {"manual", "auto", "off"}:
        return mode
    return "auto" if auto_caption_default else "off"


def resolve_caption_strategy(
    mode: str | None, manual_caption: str | None, auto_caption_default: bool
) -> tuple[bool, str | None, str]:
    normalized_mode = normalize_caption_mode(mode, auto_caption_default)

    if normalized_mode == "manual" and manual_caption:
        return False, manual_caption, normalized_mode
    if normalized_mode == "auto":
        return True, None, normalized_mode
    return False, None, normalized_mode


def clear_caption_state(auto_caption_default: bool) -> tuple[str, str | None]:
    return normalize_caption_mode(None, auto_caption_default), None
