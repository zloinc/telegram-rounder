import asyncio
import logging
import os
import re
import tempfile
import threading

from config import FFMPEG


def _whisper_model() -> str:
    return os.getenv("WHISPER_MODEL", "base")

def _cpu_threads() -> int:
    return int(os.getenv("WHISPER_CPU_THREADS", "2"))


def _beam_size() -> int:
    return int(os.getenv("WHISPER_BEAM_SIZE", "5"))

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()

_TRAILING_PUNCT = set(",.!?:;)]}\"»…")
_LEADING_PUNCT = set("([{\"«")
_FILLER_WORDS = {
    "эм",
    "ээ",
    "эээ",
    "мм",
    "м-м",
    "хм",
    "кхм",
}


def _normalize_caption_text(text: str) -> str:
    text = re.sub(r"\s+([,.!?:;)\]}\"»…])", r"\1", text)
    text = re.sub(r"([(\[{\"«])\s+", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    if text:
        text = text[0].upper() + text[1:]
    return text


def _normalize_token_for_compare(token: str) -> str:
    return re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9]+", "", token).lower()


def _is_noise_word(token: str) -> bool:
    normalized = _normalize_token_for_compare(token)
    if not normalized:
        return True
    return normalized in _FILLER_WORDS


def _dedupe_word_timestamps(words: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    previous_normalized = ""
    for item in words:
        token = (item.get("word") or "").strip()
        normalized = _normalize_token_for_compare(token)
        if _is_noise_word(token):
            continue
        if normalized and normalized == previous_normalized:
            continue
        deduped.append(item)
        previous_normalized = normalized
    return deduped


def _dedupe_repeated_clauses(text: str) -> str:
    parts = re.split(r"([.!?]+)", text)
    clauses: list[tuple[str, str]] = []
    for index in range(0, len(parts), 2):
        clause = (parts[index] or "").strip()
        punct = parts[index + 1] if index + 1 < len(parts) else ""
        if not clause:
            continue
        normalized = _normalize_token_for_compare(clause)
        if clauses and normalized == _normalize_token_for_compare(clauses[-1][0]):
            continue
        clauses.append((clause, punct))

    return " ".join(f"{clause}{punct}".strip() for clause, punct in clauses).strip()


def _filter_low_signal_segments(segments: list[str]) -> list[str]:
    result: list[str] = []
    for segment in segments:
        normalized_words = [
            _normalize_token_for_compare(token)
            for token in segment.split()
            if _normalize_token_for_compare(token)
        ]
        if not normalized_words:
            continue
        if len(normalized_words) == 1 and normalized_words[0] in _FILLER_WORDS:
            continue
        if len("".join(normalized_words)) <= 2:
            continue
        result.append(segment)
    return result


def _post_process_transcription(
    text_parts: list[str],
    words: list[dict],
) -> tuple[str, list[dict]]:
    filtered_parts = _filter_low_signal_segments(text_parts)
    words = _merge_punctuation_tokens(words)
    words = _dedupe_word_timestamps(words)
    full_text = _normalize_caption_text(" ".join(filtered_parts).strip())
    full_text = _dedupe_repeated_clauses(full_text)
    return full_text, words


def _merge_punctuation_tokens(words: list[dict]) -> list[dict]:
    merged: list[dict] = []
    pending_prefix = ""

    for item in words:
        word = (item.get("word") or "").strip()
        if not word:
            continue

        if all(ch in _TRAILING_PUNCT for ch in word):
            if merged:
                merged[-1]["word"] += word
                merged[-1]["end"] = max(merged[-1]["end"], item["end"])
            continue

        if all(ch in _LEADING_PUNCT for ch in word):
            pending_prefix += word
            continue

        merged.append(
            {
                "word": f"{pending_prefix}{word}",
                "start": item["start"],
                "end": item["end"],
            }
        )
        pending_prefix = ""

    if pending_prefix and merged:
        merged[-1]["word"] = f"{pending_prefix}{merged[-1]['word']}"

    return merged


def _get_model():
    """Lazy-load the Whisper model."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel
                model_name = _whisper_model()
                threads = _cpu_threads()
                logger.info(f"Loading Whisper model: {model_name} (threads={threads})...")
                _model = WhisperModel(
                    model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=threads,
                )
                logger.info("Whisper model loaded.")
    return _model


async def warmup():
    """Pre-load the Whisper model in a background thread so the first request is fast."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _get_model)


async def _extract_and_preprocess_audio(video_path: str, audio_path: str) -> bool:
    """
    Extract audio from video and apply preprocessing:
    - highpass filter (cut rumble below 100 Hz)
    - loudnorm (EBU R128 loudness normalisation)
    - anlmdn (non-local means denoising)
    Returns True on success.
    """
    cmd = [
        FFMPEG, "-y",
        "-i", video_path,
        "-vn",
        "-af", "highpass=f=100,loudnorm,anlmdn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        audio_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"FFmpeg audio preprocessing failed: {stderr.decode()}")
    return proc.returncode == 0


def _transcribe_sync(audio_path: str, language: str) -> tuple[str, list[dict]]:
    """
    Blocking transcription — must be called in a thread executor.
    Returns (full_text, words) where words = [{"word": str, "start": float, "end": float}].
    """
    model = _get_model()
    initial_prompt = (
        "Разговорная русская речь. Корректно распознавай слова и расставляй знаки препинания."
        if language == "ru"
        else None
    )
    segments, _ = model.transcribe(
        audio_path,
        language=language,
        beam_size=_beam_size(),
        temperature=0.0,
        condition_on_previous_text=True,
        initial_prompt=initial_prompt,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"threshold": 0.3, "min_speech_duration_ms": 50},
    )
    text_parts = []
    words = []
    for segment in segments:
        if segment.no_speech_prob > 0.5:
            logger.info(f"Skipping segment (no_speech_prob={segment.no_speech_prob:.2f}): {segment.text.strip()}")
            continue
        if segment.avg_logprob < -1.0:
            logger.info(f"Skipping segment (avg_logprob={segment.avg_logprob:.2f}): {segment.text.strip()}")
            continue
        text_parts.append(segment.text.strip())
        if segment.words:
            for w in segment.words:
                word = w.word.strip()
                if word:
                    words.append({"word": word, "start": w.start, "end": w.end})

    full_text, words = _post_process_transcription(text_parts, words)
    logger.info(f"Transcribed: {full_text} ({len(words)} words)")
    return full_text, words


async def extract_speech_to_text(
    video_path: str, language: str = "ru"
) -> tuple[str, list[dict]]:
    """
    Extract audio from video, preprocess it, and transcribe speech to text.
    Returns (text, words) — words have start/end timestamps.
    Returns ("", []) if no speech detected or transcription fails.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        audio_path = os.path.join(temp_dir, "audio.wav")

        ok = await _extract_and_preprocess_audio(video_path, audio_path)
        if not ok:
            return "", []

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _transcribe_sync, audio_path, language)
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return "", []
