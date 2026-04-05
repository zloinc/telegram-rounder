import asyncio
import html
import logging
import logging.handlers
import os
import secrets
import tempfile
from datetime import datetime
from time import monotonic

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

from bot_logic import clear_caption_state, normalize_caption_mode, resolve_caption_strategy
from processor import create_style_preview_image, probe_video, video_to_circle
from speech import extract_speech_to_text, warmup
from storage import Storage

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in .env file")

_admin_id_raw = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(_admin_id_raw) if _admin_id_raw.isdigit() else None


def _parse_int_set(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdigit():
            result.add(int(item))
    return result

AUTO_CAPTION = os.getenv("AUTO_CAPTION", "true").lower() == "true"
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ru")
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "2"))
TRANSCRIBE_CONCURRENT = int(os.getenv("TRANSCRIBE_CONCURRENT", "1"))
RENDER_CONCURRENT = int(os.getenv("RENDER_CONCURRENT", str(MAX_CONCURRENT)))
ENABLE_FILE_LOG = os.getenv("ENABLE_FILE_LOG", "true").lower() == "true"
INVITE_ONLY = os.getenv("INVITE_ONLY", "false").lower() == "true"
ALLOWED_USER_IDS = _parse_int_set(os.getenv("ALLOWED_USER_IDS", ""))
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "").strip()
MAX_VIDEO_DURATION = int(os.getenv("MAX_VIDEO_DURATION", "60"))
MAX_VIDEO_FILE_SIZE_MB = int(os.getenv("MAX_VIDEO_FILE_SIZE_MB", "25"))
RENDER_TIMEOUT_SECONDS = int(os.getenv("RENDER_TIMEOUT_SECONDS", "90"))
SQLITE_BACKUP_INTERVAL_HOURS = int(os.getenv("SQLITE_BACKUP_INTERVAL_HOURS", "12"))
SQLITE_BACKUP_KEEP = int(os.getenv("SQLITE_BACKUP_KEEP", "14"))
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "").strip()
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
DASHBOARD_COOKIE_NAME = "circlebot_admin_session"
ALLOWED_UPDATES = [
    item.strip()
    for item in os.getenv("ALLOWED_UPDATES", "message,callback_query").split(",")
    if item.strip()
]

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()          # e.g. https://example.com/bot
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/bot").strip()    # path on your server
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0").strip()
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.getenv("DATA_DIR", BASE_DIR).strip() or BASE_DIR
LOG_DIR = os.getenv("LOG_DIR", DATA_DIR).strip() or DATA_DIR

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
DATABASE_FILE = os.path.join(DATA_DIR, "bot.sqlite3")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

# ── logging ───────────────────────────────────────────────────────────────────

def _setup_logging():
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    if ENABLE_FILE_LOG:
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

_setup_logging()
logger = logging.getLogger(__name__)

# ── bot / dispatcher ──────────────────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

_processing_semaphore = asyncio.Semaphore(MAX_CONCURRENT)
_transcribe_semaphore = asyncio.Semaphore(TRANSCRIBE_CONCURRENT)
_render_semaphore = asyncio.Semaphore(RENDER_CONCURRENT)
_processing_queue: list[int] = []
_user_active: set[int] = set()     # rate-limit: one video per user at a time

# ── per-user state (in-memory + persisted) ────────────────────────────────────

user_captions: dict[int, str] = {}
user_caption_mode: dict[int, str] = {}
user_text_color: dict[int, str] = {}
user_font: dict[int, str] = {}
user_font_size: dict[int, str] = {}   # "S" | "M" | "L"
user_text_position: dict[int, str] = {}   # "top" | "bottom"
user_text_bg: dict[int, bool] = {}

storage = Storage(
    db_path=DATABASE_FILE,
    legacy_users_file=USERS_FILE,
    legacy_settings_file=SETTINGS_FILE,
    backups_dir=BACKUPS_DIR,
    backup_keep=SQLITE_BACKUP_KEEP,
)
_dashboard_sessions: set[str] = set()

TEXT_COLORS = {
    "white":  (255, 255, 255, 255),
    "yellow": (255, 230, 0,   255),
    "cyan":   (0,   210, 255, 255),
    "orange": (255, 140, 0,   255),
}
COLOR_LABELS = {
    "white":  "⚪ Белый",
    "yellow": "🟡 Жёлтый",
    "cyan":   "🔵 Голубой",
    "orange": "🟠 Оранжевый",
}
FONT_LABELS = {
    "helvetica": "Iskra",
    "verdana":   "Gill Sans Light",
    "georgia":   "Rodchenko Bold",
    "impact":    "Fortnite SHA",
}
SIZE_LABELS = {
    "S": "S — маленький",
    "M": "M — средний",
    "L": "L — крупный",
}
POSITION_LABELS = {
    "bottom": "↓ Снизу",
    "top":    "↑ Сверху",
}

# ── settings persistence ───────────────────────────────────────────────────────

def _load_all_settings():
    """Load persisted per-user settings from disk into memory."""
    try:
        data = storage.load_all_settings()
        for uid_str, s in data.items():
            uid = int(uid_str)
            if s.get("manual_caption"):
                user_captions[uid] = s["manual_caption"]
            user_caption_mode[uid] = normalize_caption_mode(
                s.get("caption_mode"),
                s.get("auto_caption", AUTO_CAPTION),
            )
            if "text_color"    in s: user_text_color[uid]    = s["text_color"]
            if "font"          in s: user_font[uid]          = s["font"]
            if "font_size"     in s: user_font_size[uid]     = s["font_size"]
            if "text_position" in s: user_text_position[uid] = s["text_position"]
            if "text_bg"       in s: user_text_bg[uid]       = s["text_bg"]
        logger.info(f"Loaded settings for {len(data)} users.")
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")


def _save_user_settings(uid: int):
    """Persist settings for a single user."""
    try:
        storage.save_user_settings(
            uid,
            {
                "caption_mode": user_caption_mode.get(
                    uid, normalize_caption_mode(None, AUTO_CAPTION)
                ),
                "manual_caption": user_captions.get(uid),
                "text_color": user_text_color.get(uid, "white"),
                "font": user_font.get(uid, "helvetica"),
                "font_size": user_font_size.get(uid, "M"),
                "text_position": user_text_position.get(uid, "bottom"),
                "text_bg": user_text_bg.get(uid, False),
            },
        )
    except Exception as e:
        logger.error(f"Failed to save settings for {uid}: {e}")


# ── users registry ─────────────────────────────────────────────────────────────

def _load_users() -> dict:
    return storage.load_users()


def _register_user(user: types.User):
    storage.register_user(
        {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "first_seen": datetime.now().isoformat(timespec="seconds"),
        }
    )


def _is_admin(user_id: int) -> bool:
    return ADMIN_ID is not None and user_id == ADMIN_ID


def _dashboard_enabled() -> bool:
    return bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD)


def _is_allowed_user(user_id: int) -> bool:
    if _is_admin(user_id):
        return True
    if not INVITE_ONLY:
        return True
    return user_id in ALLOWED_USER_IDS


async def _deny_access_message(message: Message):
    await message.answer(
        "🔒 Доступ к боту ограничен.\n"
        "Напиши администратору, чтобы тебя добавили в список доступа."
    )


async def _deny_access_callback(callback: CallbackQuery):
    await callback.answer("🔒 Нет доступа", show_alert=True)


def _validate_video(video: types.Video) -> str | None:
    if not (video.mime_type or "").startswith("video/"):
        return "🎬 Нужен обычный видеофайл Telegram."
    if video.duration and video.duration > MAX_VIDEO_DURATION:
        return (
            f"⏱ Видео слишком длинное.\n"
            f"Лимит: до {MAX_VIDEO_DURATION} секунд."
        )
    max_size_bytes = MAX_VIDEO_FILE_SIZE_MB * 1024 * 1024
    if video.file_size and video.file_size > max_size_bytes:
        return (
            f"📦 Файл слишком большой.\n"
            f"Лимит: до {MAX_VIDEO_FILE_SIZE_MB} MB."
        )
    return None


def _format_bytes(num_bytes: int | None) -> str:
    if not num_bytes:
        return "0 B"
    value = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num_bytes} B"


def _format_ms(value: int | None) -> str:
    if not value:
        return "—"
    if value >= 1000:
        return f"{value / 1000:.1f}s"
    return f"{value}ms"


def _progress_bar(progress: float, width: int = 10) -> str:
    progress = max(0.0, min(1.0, progress))
    filled = round(progress * width)
    return "█" * filled + "░" * (width - filled)


def _status_text(stage_idx: int, total: int, title: str, detail: str, progress: float) -> str:
    pct = int(max(0.0, min(1.0, progress)) * 100)
    return (
        f"{stage_idx}/{total} {title}\n"
        f"{_progress_bar(progress)} {pct}%\n"
        f"{detail}"
    )


async def _safe_edit_status(message: Message | None, text: str):
    if not message:
        return
    try:
        await message.edit_text(text)
    except Exception:
        pass


async def _animate_stage(
    message: Message | None,
    *,
    stage_idx: int,
    total: int,
    title: str,
    detail: str,
    expected_seconds: float,
):
    started = monotonic()
    progress = 0.0
    while True:
        elapsed = monotonic() - started
        target = min(0.94, elapsed / max(expected_seconds, 1.0))
        progress = max(progress, target)
        await _safe_edit_status(
            message,
            _status_text(stage_idx, total, title, detail, progress),
        )
        await asyncio.sleep(1.2)


async def _validate_downloaded_video(input_path: str) -> tuple[int | None, int | None]:
    probe = await probe_video(input_path)
    streams = probe.get("streams", [])
    format_info = probe.get("format", {})
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not video_streams:
        raise ValueError("В файле не найден видеопоток.")

    duration_raw = format_info.get("duration") or video_streams[0].get("duration")
    duration = int(float(duration_raw)) if duration_raw else None
    size_raw = format_info.get("size")
    size = int(size_raw) if size_raw and str(size_raw).isdigit() else None

    if duration and duration > MAX_VIDEO_DURATION:
        raise ValueError(f"Видео длиннее {MAX_VIDEO_DURATION} секунд.")
    max_size_bytes = MAX_VIDEO_FILE_SIZE_MB * 1024 * 1024
    if size and size > max_size_bytes:
        raise ValueError(f"Файл больше {MAX_VIDEO_FILE_SIZE_MB} MB.")

    return duration, size


def _render_login_page(error: str | None = None) -> str:
    error_html = (
        f"<p style='color:#b42318;margin:0 0 16px'>{html.escape(error)}</p>" if error else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Circle Bot Admin</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background:#f5f2ea; color:#1f1f1f; margin:0; }}
    .wrap {{ min-height:100vh; display:grid; place-items:center; padding:24px; }}
    .card {{ width:min(420px,100%); background:#fffdf8; border:1px solid #ddd4c6; border-radius:20px; padding:28px; box-shadow:0 20px 60px rgba(0,0,0,.08); }}
    h1 {{ margin:0 0 10px; font-size:28px; }}
    p {{ color:#5f5a52; }}
    input {{ width:100%; box-sizing:border-box; margin:8px 0 14px; padding:14px 16px; border-radius:12px; border:1px solid #cdc3b3; font-size:16px; }}
    button {{ width:100%; padding:14px 16px; border:0; border-radius:12px; background:#1f1f1f; color:#fff; font-size:16px; cursor:pointer; }}
  </style>
</head>
<body>
  <div class="wrap">
    <form class="card" method="post" action="/admin/circle-bot/login">
      <h1>🔐 Circle Bot Admin</h1>
      <p>Войди, чтобы открыть dashboard и admin-команды.</p>
      {error_html}
      <label>Логин</label>
      <input name="username" autocomplete="username">
      <label>Пароль</label>
      <input name="password" type="password" autocomplete="current-password">
      <button type="submit">Войти</button>
    </form>
  </div>
</body>
</html>"""


def _render_dashboard(stats: dict, users: dict[str, dict]) -> str:
    recent_jobs = "".join(
        f"<tr><td>{job['id']}</td><td>{job['user_id']}</td><td>{html.escape(job['status'])}</td>"
        f"<td>{job['source_duration'] or '—'}</td><td>{_format_bytes(job['source_file_size'])}</td>"
        f"<td>{'да' if job['had_caption'] else 'нет'}</td><td>{job['caption_length'] or 0}</td>"
        f"<td>{_format_ms(job.get('transcribe_ms'))}</td><td>{_format_ms(job.get('render_ms'))}</td>"
        f"<td>{'да' if job.get('fallback_without_caption') else 'нет'}</td></tr>"
        for job in stats["recent_jobs"]
    ) or "<tr><td colspan='10'>Пока нет обработок</td></tr>"
    backups = "".join(
        f"<li>{html.escape(item['name'])} · {_format_bytes(item['size'])}</li>"
        for item in stats["backups"][:8]
    ) or "<li>Нет бэкапов</li>"
    user_rows = "".join(
        f"<tr><td>{u['id']}</td><td>{html.escape(u.get('username') or '—')}</td>"
        f"<td>{html.escape(((u.get('first_name') or '') + ' ' + (u.get('last_name') or '')).strip() or '—')}</td>"
        f"<td>{html.escape(u['first_seen'])}</td></tr>"
        for u in list(users.values())[-20:]
    ) or "<tr><td colspan='4'>Нет пользователей</td></tr>"
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Circle Bot Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background:#f5f2ea; color:#181818; margin:0; }}
    .wrap {{ padding:24px; max-width:1200px; margin:0 auto; }}
    .top {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:24px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin-bottom:24px; }}
    .card, .panel {{ background:#fffdf8; border:1px solid #ddd4c6; border-radius:18px; padding:18px; box-shadow:0 18px 40px rgba(0,0,0,.06); }}
    .metric {{ font-size:32px; font-weight:700; margin:8px 0 0; }}
    .label {{ color:#6a6257; font-size:14px; }}
    .cols {{ display:grid; grid-template-columns:1.1fr .9fr; gap:16px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th, td {{ padding:10px 8px; border-bottom:1px solid #eee1cf; text-align:left; }}
    textarea {{ width:100%; min-height:120px; box-sizing:border-box; border:1px solid #cdc3b3; border-radius:12px; padding:12px; }}
    button, .linkbtn {{ border:0; border-radius:12px; padding:12px 14px; background:#1f1f1f; color:#fff; cursor:pointer; text-decoration:none; display:inline-block; }}
    ul {{ margin:0; padding-left:18px; }}
    @media (max-width: 900px) {{ .cols {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1 style="margin:0 0 8px">📊 Circle Bot Dashboard</h1>
        <div class="label">Статистика бота, пользователей и обработок</div>
      </div>
      <div style="display:flex;gap:10px">
        <form method="post" action="/admin/circle-bot/backup"><button type="submit">💾 Backup Now</button></form>
        <a class="linkbtn" href="/admin/circle-bot/logout">🚪 Выйти</a>
      </div>
    </div>
    <div class="grid">
      <div class="card"><div class="label">👥 Пользователи</div><div class="metric">{stats['total_users']}</div></div>
      <div class="card"><div class="label">👋 Visits (/start)</div><div class="metric">{stats['start_count']}</div></div>
      <div class="card"><div class="label">🎬 Кружки</div><div class="metric">{stats['success_jobs']}</div></div>
      <div class="card"><div class="label">💬 С подписью</div><div class="metric">{stats['captioned_jobs']}</div></div>
      <div class="card"><div class="label">🎤 Auto captions</div><div class="metric">{stats['auto_captioned_jobs']}</div></div>
      <div class="card"><div class="label">✍️ Manual captions</div><div class="metric">{stats['manual_captioned_jobs']}</div></div>
      <div class="card"><div class="label">⏱ Avg transcribe</div><div class="metric">{_format_ms(stats['avg_transcribe_ms'])}</div></div>
      <div class="card"><div class="label">🎞 Avg render</div><div class="metric">{_format_ms(stats['avg_render_ms'])}</div></div>
      <div class="card"><div class="label">🛟 Fallback jobs</div><div class="metric">{stats['fallback_jobs']}</div></div>
    </div>
    <div class="cols">
      <div class="panel">
        <h2 style="margin-top:0">🧾 Recent Jobs</h2>
        <table>
          <thead><tr><th>ID</th><th>User</th><th>Status</th><th>Sec</th><th>Size</th><th>Caption</th><th>Len</th><th>ASR</th><th>Render</th><th>Fallback</th></tr></thead>
          <tbody>{recent_jobs}</tbody>
        </table>
      </div>
      <div style="display:grid;gap:16px">
        <div class="panel">
          <h2 style="margin-top:0">📣 Broadcast</h2>
          <form method="post" action="/admin/circle-bot/broadcast">
            <textarea name="text" placeholder="Сообщение для всех пользователей"></textarea>
            <div style="margin-top:12px"><button type="submit">Отправить</button></div>
          </form>
        </div>
        <div class="panel">
          <h2 style="margin-top:0">💾 SQLite Backups</h2>
          <ul>{backups}</ul>
        </div>
        <div class="panel">
          <h2 style="margin-top:0">🧍 Recent Users</h2>
          <table>
            <thead><tr><th>ID</th><th>Username</th><th>Name</th><th>First seen</th></tr></thead>
            <tbody>{user_rows}</tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""


def _get_dashboard_session(request: web.Request) -> str | None:
    return request.cookies.get(DASHBOARD_COOKIE_NAME)


def _is_dashboard_authenticated(request: web.Request) -> bool:
    session_id = _get_dashboard_session(request)
    return bool(session_id and session_id in _dashboard_sessions)


async def _dashboard_login_page(_: web.Request) -> web.Response:
    if not _dashboard_enabled():
        raise web.HTTPNotFound()
    return web.Response(text=_render_login_page(), content_type="text/html")


async def _dashboard_login_submit(request: web.Request) -> web.Response:
    if not _dashboard_enabled():
        raise web.HTTPNotFound()
    data = await request.post()
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))
    if not (
        secrets.compare_digest(username, DASHBOARD_USERNAME)
        and secrets.compare_digest(password, DASHBOARD_PASSWORD)
    ):
        return web.Response(
            text=_render_login_page("Неверный логин или пароль."),
            content_type="text/html",
            status=401,
        )
    session_id = secrets.token_urlsafe(32)
    _dashboard_sessions.add(session_id)
    response = web.HTTPFound("/admin/circle-bot")
    response.set_cookie(
        DASHBOARD_COOKIE_NAME,
        session_id,
        httponly=True,
        secure=True,
        samesite="Strict",
        max_age=60 * 60 * 12,
    )
    return response


async def _dashboard_logout(request: web.Request) -> web.Response:
    session_id = _get_dashboard_session(request)
    if session_id:
        _dashboard_sessions.discard(session_id)
    response = web.HTTPFound("/admin/circle-bot/login")
    response.del_cookie(DASHBOARD_COOKIE_NAME)
    return response


async def _require_dashboard_auth(request: web.Request):
    if not _dashboard_enabled():
        raise web.HTTPNotFound()
    if not _is_dashboard_authenticated(request):
        raise web.HTTPFound("/admin/circle-bot/login")


async def _dashboard_home(request: web.Request) -> web.Response:
    await _require_dashboard_auth(request)
    storage.increment_metric("dashboard_views")
    stats = storage.get_dashboard_stats()
    users = _load_users()
    return web.Response(
        text=_render_dashboard(stats, users),
        content_type="text/html",
    )


async def _dashboard_broadcast(request: web.Request) -> web.Response:
    await _require_dashboard_auth(request)
    data = await request.post()
    text = str(data.get("text", "")).strip()
    if not text:
        raise web.HTTPFound("/admin/circle-bot")
    users = _load_users()
    for user in users.values():
        try:
            await bot.send_message(user["id"], text)
        except Exception:
            logger.exception("Dashboard broadcast failed for user %s", user["id"])
        await asyncio.sleep(0.05)
    raise web.HTTPFound("/admin/circle-bot")


async def _dashboard_backup(request: web.Request) -> web.Response:
    await _require_dashboard_auth(request)
    storage.backup_database()
    raise web.HTTPFound("/admin/circle-bot")


async def _sqlite_backup_loop():
    if SQLITE_BACKUP_INTERVAL_HOURS <= 0:
        return
    while True:
        await asyncio.sleep(SQLITE_BACKUP_INTERVAL_HOURS * 3600)
        try:
            storage.backup_database()
            logger.info("SQLite backup created.")
        except Exception:
            logger.exception("SQLite backup failed")


# ── keyboards ──────────────────────────────────────────────────────────────────

def _settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    mode = user_caption_mode.get(user_id, normalize_caption_mode(None, AUTO_CAPTION))
    enabled = mode == "auto"
    ac_label = "🎤 Авто-субтитры: ВКЛ ✅" if enabled else "🔇 Авто-субтитры: ВЫКЛ ❌"

    color_key = user_text_color.get(user_id, "white")
    font_key  = user_font.get(user_id, "helvetica")
    size_key  = user_font_size.get(user_id, "M")
    pos_key   = user_text_position.get(user_id, "bottom")
    bg_on     = user_text_bg.get(user_id, False)

    color_label = COLOR_LABELS.get(color_key, "⚪ Белый")
    font_label  = FONT_LABELS.get(font_key, "Helvetica")
    size_label  = size_key
    pos_label   = POSITION_LABELS.get(pos_key, "↓ Снизу")
    bg_label    = "🟩 Фон: ВКЛ" if bg_on else "⬛ Фон: ВЫКЛ"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ac_label, callback_data="toggle_autocaption")],
        [InlineKeyboardButton(text=f"🎨 {color_label}", callback_data="menu_color")],
        [InlineKeyboardButton(text=f"🔤 {font_label}", callback_data="menu_font")],
        [InlineKeyboardButton(text=f"🔡 Размер: {size_label}", callback_data="menu_size")],
        [InlineKeyboardButton(text=f"📍 {pos_label}", callback_data="menu_position")],
        [InlineKeyboardButton(text=bg_label, callback_data="toggle_text_bg")],
    ])


async def _edit_settings_message(message: Message, text: str, reply_markup: InlineKeyboardMarkup):
    if message.photo:
        await message.edit_caption(caption=text, reply_markup=reply_markup)
        return
    await message.edit_text(text, reply_markup=reply_markup)


async def _send_settings_panel(message: Message, text: str = "⚙️ Настройки:"):
    uid = message.from_user.id
    await message.answer(text, reply_markup=_settings_keyboard(uid))


def _color_keyboard(user_id: int) -> InlineKeyboardMarkup:
    current = user_text_color.get(user_id, "white")
    rows = []
    for key, label in COLOR_LABELS.items():
        rows.append(
            [InlineKeyboardButton(
                text=f"{label} ✓" if key == current else label,
                callback_data=f"color_{key}",
            )]
        )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _font_keyboard(user_id: int) -> InlineKeyboardMarkup:
    current = user_font.get(user_id, "helvetica")
    rows = []
    for key, label in FONT_LABELS.items():
        rows.append(
            [InlineKeyboardButton(
                text=f"{label} ✓" if key == current else label,
                callback_data=f"font_{key}",
            )]
        )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _size_keyboard(user_id: int) -> InlineKeyboardMarkup:
    current = user_font_size.get(user_id, "M")
    rows = []
    for key, label in SIZE_LABELS.items():
        rows.append(
            [InlineKeyboardButton(
                text=f"{label} ✓" if key == current else label,
                callback_data=f"size_{key}",
            )]
        )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _position_keyboard(user_id: int) -> InlineKeyboardMarkup:
    current = user_text_position.get(user_id, "bottom")
    rows = []
    for key, label in POSITION_LABELS.items():
        rows.append(
            [InlineKeyboardButton(
                text=f"{label} ✓" if key == current else label,
                callback_data=f"position_{key}",
            )]
        )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── commands ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if not _is_allowed_user(message.from_user.id):
        await _deny_access_message(message)
        return

    _register_user(message.from_user)
    storage.increment_metric("start_count")
    await message.answer(
        "👋 Привет! Я превращаю видео в Telegram-кружок.\n\n"
        "📌 Кратко как пользоваться:\n"
        "1. Отправь обычное видео\n"
        "2. Я сделаю кружок\n"
        "3. Если включены авто-субтитры, попробую распознать речь\n\n"
        "⚠️ Ограничения:\n"
        f"• До {MAX_VIDEO_DURATION} секунд\n"
        f"• До {MAX_VIDEO_FILE_SIZE_MB} MB\n"
        "• Нужен именно video, не video note\n\n"
        "🛠 Команды:\n"
        "`/caption Текст` — своя подпись\n"
        "`/clear` — убрать подпись\n"
        "`/settings` — настройки",
        reply_markup=_settings_keyboard(message.from_user.id),
    )


@dp.message(Command("settings"))
async def cmd_settings(message: Message):
    if not _is_allowed_user(message.from_user.id):
        await _deny_access_message(message)
        return
    await _send_settings_panel(message)


@dp.message(Command("preview"))
async def cmd_preview(message: Message):
    if not _is_allowed_user(message.from_user.id):
        await _deny_access_message(message)
        return

    preview_text = message.text.split("/preview", 1)[-1].strip()
    if not preview_text:
        preview_text = user_captions.get(message.from_user.id) or "Пример подписи"

    uid = message.from_user.id
    status = await message.answer("🖼 Готовлю превью стиля...")
    with tempfile.TemporaryDirectory() as temp_dir:
        preview_path = os.path.join(temp_dir, "preview.png")
        await create_style_preview_image(
            output_path=preview_path,
            caption=preview_text,
            text_color=TEXT_COLORS[user_text_color.get(uid, "white")],
            font_name=user_font.get(uid, "helvetica"),
            font_size_name=user_font_size.get(uid, "M"),
            position=user_text_position.get(uid, "bottom"),
            text_bg=user_text_bg.get(uid, False),
            size=640,
        )
        await message.answer_photo(
            photo=FSInputFile(preview_path),
            caption=f"🖼 Превью: «{preview_text}»",
        )
    await status.edit_text(
        "✅ Превью готово.\n"
        "Если нужен свой текст, используй `/preview Твой текст`."
    )


@dp.message(Command("caption"))
async def cmd_caption(message: Message):
    if not _is_allowed_user(message.from_user.id):
        await _deny_access_message(message)
        return
    caption = message.text.split("/caption", 1)[-1].strip()
    if not caption:
        await message.answer("Использование: `/caption Текст подписи`")
        return
    user_captions[message.from_user.id] = caption
    user_caption_mode[message.from_user.id] = "manual"
    _save_user_settings(message.from_user.id)
    await message.answer(f"Подпись установлена: «{caption}»\nТеперь отправь видео!")


@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if not _is_allowed_user(message.from_user.id):
        await _deny_access_message(message)
        return
    mode, manual_caption = clear_caption_state(AUTO_CAPTION)
    user_caption_mode[message.from_user.id] = mode
    if manual_caption is None:
        user_captions.pop(message.from_user.id, None)
    _save_user_settings(message.from_user.id)
    await message.answer("Подпись удалена. Отправь видео!")


@dp.message(Command("autocaption"))
async def cmd_autocaption(message: Message):
    if not _is_allowed_user(message.from_user.id):
        await _deny_access_message(message)
        return
    arg = message.text.split("/autocaption", 1)[-1].strip().lower()
    if arg in ("on", "вкл", "true", "1"):
        user_caption_mode[message.from_user.id] = "auto"
    elif arg in ("off", "выкл", "false", "0"):
        user_caption_mode[message.from_user.id] = "off"
    else:
        await message.answer("Использование: `/autocaption on` или `/autocaption off`")
        return
    _save_user_settings(message.from_user.id)
    await _send_settings_panel(message)


@dp.message(Command("users"))
async def cmd_users(message: Message):
    if not _is_admin(message.from_user.id):
        return
    users = _load_users()
    if not users:
        await message.answer("Пока нет зарегистрированных пользователей.")
        return
    lines = [f"👥 Всего пользователей: {len(users)}\n"]
    for u in users.values():
        name = f"@{u['username']}" if u["username"] else u["first_name"] or str(u["id"])
        full = f"{u['first_name'] or ''} {u['last_name'] or ''}".strip()
        since = u["first_seen"][:10]
        lines.append(f"• {name} ({full}) — с {since}")
    await message.answer("\n".join(lines))


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if not _is_admin(message.from_user.id):
        return
    text = message.text.split("/broadcast", 1)[-1].strip()
    if not text:
        await message.answer("Использование: `/broadcast Текст сообщения`")
        return
    users = _load_users()
    if not users:
        await message.answer("Нет пользователей для рассылки.")
        return
    status = await message.answer(f"📢 Начинаю рассылку для {len(users)} пользователей...")
    sent, failed = 0, 0
    for u in users.values():
        try:
            await bot.send_message(u["id"], text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await status.edit_text(
        f"✅ Рассылка завершена\n"
        f"Доставлено: {sent}\n"
        f"Не доставлено: {failed}"
    )


# ── callbacks ─────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "menu_main")
async def cb_menu_main(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    await _edit_settings_message(
        callback.message,
        "⚙️ Настройки:",
        _settings_keyboard(callback.from_user.id),
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_color")
async def cb_menu_color(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    await _edit_settings_message(
        callback.message,
        "🎨 Выбери цвет текста:",
        _color_keyboard(callback.from_user.id),
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_font")
async def cb_menu_font(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    await _edit_settings_message(
        callback.message,
        "🔤 Выбери шрифт:",
        _font_keyboard(callback.from_user.id),
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_size")
async def cb_menu_size(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    await _edit_settings_message(
        callback.message,
        "🔡 Выбери размер шрифта:",
        _size_keyboard(callback.from_user.id),
    )
    await callback.answer()


@dp.callback_query(F.data == "menu_position")
async def cb_menu_position(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    await _edit_settings_message(
        callback.message,
        "📍 Позиция текста:",
        _position_keyboard(callback.from_user.id),
    )
    await callback.answer()


@dp.callback_query(F.data == "toggle_autocaption")
async def cb_toggle_autocaption(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    uid = callback.from_user.id
    current_mode = user_caption_mode.get(uid, normalize_caption_mode(None, AUTO_CAPTION))
    user_caption_mode[uid] = "off" if current_mode == "auto" else "auto"
    _save_user_settings(uid)
    await _edit_settings_message(callback.message, "⚙️ Настройки:", _settings_keyboard(uid))
    state = "включено" if user_caption_mode[uid] == "auto" else "выключено"
    await callback.answer(f"Авто-субтитры {state}")


@dp.callback_query(F.data == "toggle_text_bg")
async def cb_toggle_text_bg(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    uid = callback.from_user.id
    current = user_text_bg.get(uid, False)
    user_text_bg[uid] = not current
    _save_user_settings(uid)
    await _edit_settings_message(callback.message, "⚙️ Настройки:", _settings_keyboard(uid))
    state = "включён" if user_text_bg[uid] else "выключен"
    await callback.answer(f"Фон {state}")


@dp.callback_query(F.data.startswith("color_"))
async def cb_set_color(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    uid = callback.from_user.id
    color_key = callback.data[len("color_"):]
    if color_key not in TEXT_COLORS:
        await callback.answer("Неизвестный цвет")
        return
    user_text_color[uid] = color_key
    _save_user_settings(uid)
    await _edit_settings_message(callback.message, "⚙️ Настройки:", _settings_keyboard(uid))
    await callback.answer(f"✓ {COLOR_LABELS[color_key]}")


@dp.callback_query(F.data.startswith("font_"))
async def cb_set_font(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    uid = callback.from_user.id
    font_key = callback.data[len("font_"):]
    if font_key not in FONT_LABELS:
        await callback.answer("Неизвестный шрифт")
        return
    user_font[uid] = font_key
    _save_user_settings(uid)
    await _edit_settings_message(callback.message, "⚙️ Настройки:", _settings_keyboard(uid))
    await callback.answer(f"✓ {FONT_LABELS[font_key]}")


@dp.callback_query(F.data.startswith("size_"))
async def cb_set_size(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    uid = callback.from_user.id
    size_key = callback.data[len("size_"):]
    if size_key not in SIZE_LABELS:
        await callback.answer("Неизвестный размер")
        return
    user_font_size[uid] = size_key
    _save_user_settings(uid)
    await _edit_settings_message(callback.message, "⚙️ Настройки:", _settings_keyboard(uid))
    await callback.answer(f"✓ {SIZE_LABELS[size_key]}")


@dp.callback_query(F.data.startswith("position_"))
async def cb_set_position(callback: CallbackQuery):
    if not _is_allowed_user(callback.from_user.id):
        await _deny_access_callback(callback)
        return
    uid = callback.from_user.id
    pos_key = callback.data[len("position_"):]
    if pos_key not in POSITION_LABELS:
        await callback.answer("Неизвестная позиция")
        return
    user_text_position[uid] = pos_key
    _save_user_settings(uid)
    await _edit_settings_message(callback.message, "⚙️ Настройки:", _settings_keyboard(uid))
    await callback.answer(f"✓ {POSITION_LABELS[pos_key]}")


# ── video handler ─────────────────────────────────────────────────────────────

@dp.message(lambda msg: msg.video)
async def handle_video(message: Message):
    uid = message.from_user.id
    if not _is_allowed_user(uid):
        await _deny_access_message(message)
        return

    validation_error = _validate_video(message.video)
    if validation_error:
        await message.answer(validation_error)
        return

    _register_user(message.from_user)

    status: Message | None = None
    job_id: int | None = None
    transcribe_ms: int | None = None
    render_ms: int | None = None
    fallback_without_caption = False
    _processing_queue.append(uid)

    try:
        # Rate limit: one active video per user
        if uid in _user_active:
            _processing_queue.remove(uid)
            await message.answer("⏳ Ваше предыдущее видео ещё обрабатывается. Пожалуйста, подождите.")
            return
        _user_active.add(uid)

        use_auto, manual_caption, _ = resolve_caption_strategy(
            user_caption_mode.get(uid),
            user_captions.get(uid),
            AUTO_CAPTION,
        )

        was_queued = len(_processing_queue) > MAX_CONCURRENT
        if was_queued:
            status = await message.answer(
                f"⏳ В очереди: {len(_processing_queue) - MAX_CONCURRENT} видео впереди вас...\n"
                f"Ожидайте, скоро начну обработку!"
            )
        else:
            status = await message.answer(
                _status_text(1, 4, "Скачиваю видео", "Получаю файл из Telegram...", 0.08)
            )

        video = message.video
        file = await bot.get_file(video.file_id)

        async with _processing_semaphore:
            if uid in _processing_queue:
                _processing_queue.remove(uid)
            if was_queued:
                await _safe_edit_status(
                    status,
                    _status_text(1, 4, "Скачиваю видео", "Очередь дошла до вас, начинаю...", 0.08),
                )

            with tempfile.TemporaryDirectory() as temp_dir:
                input_path  = os.path.join(temp_dir, "input.mp4")
                output_path = os.path.join(temp_dir, "output.mp4")

                await _safe_edit_status(
                    status,
                    _status_text(1, 4, "Скачиваю видео", "Забираю файл и проверяю контейнер...", 0.22),
                )
                await bot.download_file(file.file_path, input_path)
                actual_duration, actual_size = await _validate_downloaded_video(input_path)
                await _safe_edit_status(
                    status,
                    _status_text(1, 4, "Скачиваю видео", "Файл получен, готовлю обработку...", 0.30),
                )

                caption      = manual_caption
                word_timings = None
                resolved_mode = "manual" if manual_caption else ("auto" if use_auto else "off")
                job_id = storage.create_processing_job(
                    user_id=uid,
                    source_duration=actual_duration or video.duration,
                    source_file_size=actual_size or video.file_size,
                    source_mime_type=video.mime_type,
                    caption_mode=resolved_mode,
                    manual_caption_used=bool(manual_caption),
                )

                if use_auto and not manual_caption:
                    await _safe_edit_status(
                        status,
                        _status_text(2, 4, "Распознаю речь", "Подготавливаю аудио и запускаю Whisper...", 0.35),
                    )
                    anim_task = asyncio.create_task(
                        _animate_stage(
                            status,
                            stage_idx=2,
                            total=4,
                            title="Распознаю речь",
                            detail="Слушаю аудио и собираю подпись...",
                            expected_seconds=18,
                        )
                    )
                    transcribe_started = monotonic()
                    try:
                        async with _transcribe_semaphore:
                            detected_text, detected_words = await extract_speech_to_text(
                                input_path, WHISPER_LANGUAGE
                            )
                    finally:
                        anim_task.cancel()
                        transcribe_ms = int((monotonic() - transcribe_started) * 1000)

                    if detected_text:
                        caption      = detected_text
                        word_timings = detected_words if detected_words else None
                        await _safe_edit_status(
                            status,
                            _status_text(
                                2,
                                4,
                                "Распознаю речь",
                                f"Готово за {_format_ms(transcribe_ms)}. Перехожу к рендеру...",
                                0.52,
                            ),
                        )
                        logger.info(f"Auto-caption for user {uid}: {caption}")
                    else:
                        await _safe_edit_status(
                            status,
                            _status_text(
                                2,
                                4,
                                "Распознаю речь",
                                "Речь не нашлась, продолжу без подписи.",
                                0.52,
                            ),
                        )
                        logger.info(f"No speech detected for user {uid}")
                else:
                    await _safe_edit_status(
                        status,
                        _status_text(2, 4, "Пропускаю распознавание", "Использую ручную подпись или чистый режим.", 0.52),
                    )

                try:
                    color_key = user_text_color.get(uid, "white")
                    font_key  = user_font.get(uid, "helvetica")
                    size_key  = user_font_size.get(uid, "M")
                    pos_key   = user_text_position.get(uid, "bottom")
                    bg_on     = user_text_bg.get(uid, False)

                    render_started = monotonic()
                    render_anim = asyncio.create_task(
                        _animate_stage(
                            status,
                            stage_idx=3,
                            total=4,
                            title="Собираю кружок",
                            detail="FFmpeg и графика рендерят итоговый файл...",
                            expected_seconds=45,
                        )
                    )
                    try:
                        async with _render_semaphore:
                            await asyncio.wait_for(
                                video_to_circle(
                                    input_path=input_path,
                                    output_path=output_path,
                                    caption=caption if not word_timings else None,
                                    word_timings=word_timings,
                                    text_color=TEXT_COLORS[color_key],
                                    font_name=font_key,
                                    font_size_name=size_key,
                                    position=pos_key,
                                    text_bg=bg_on,
                                    size=640,
                                ),
                                timeout=RENDER_TIMEOUT_SECONDS,
                            )
                    except Exception:
                        if caption or word_timings:
                            fallback_without_caption = True
                            logger.exception(
                                "Caption render failed for user %s, retrying without text",
                                uid,
                            )
                            await _safe_edit_status(
                                status,
                                _status_text(
                                    3,
                                    4,
                                    "Собираю кружок",
                                    "Подпись сломалась, повторяю рендер без текста...",
                                    0.76,
                                ),
                            )
                            await asyncio.wait_for(
                                video_to_circle(
                                    input_path=input_path,
                                    output_path=output_path,
                                    caption=None,
                                    word_timings=None,
                                    text_color=TEXT_COLORS[color_key],
                                    font_name=font_key,
                                    font_size_name=size_key,
                                    position=pos_key,
                                    text_bg=False,
                                    size=640,
                                ),
                                timeout=RENDER_TIMEOUT_SECONDS,
                            )
                            caption = None
                            word_timings = None
                        else:
                            raise
                    finally:
                        render_anim.cancel()
                    render_ms = int((monotonic() - render_started) * 1000)

                    await _safe_edit_status(
                        status,
                        _status_text(
                            4,
                            4,
                            "Отправляю кружок",
                            f"Рендер готов за {_format_ms(render_ms)}, загружаю результат в Telegram...",
                            0.96,
                        ),
                    )
                    await bot.send_video_note(
                        chat_id=message.chat.id,
                        video_note=FSInputFile(output_path),
                    )

                    if caption:
                        if job_id:
                            storage.complete_processing_job(
                                job_id=job_id,
                                status="success",
                                had_caption=True,
                                auto_caption_used=bool(word_timings),
                                manual_caption_used=bool(manual_caption),
                                caption_length=len(caption),
                                transcribe_ms=transcribe_ms,
                                render_ms=render_ms,
                                fallback_without_caption=fallback_without_caption,
                            )
                        await _safe_edit_status(
                            status,
                            f"✅ Готово!\nПодпись: «{caption}»\nASR: {_format_ms(transcribe_ms)} • Render: {_format_ms(render_ms)}",
                        )
                    else:
                        if job_id:
                            storage.complete_processing_job(
                                job_id=job_id,
                                status="success",
                                had_caption=False,
                                auto_caption_used=False,
                                manual_caption_used=False,
                                caption_length=0,
                                transcribe_ms=transcribe_ms,
                                render_ms=render_ms,
                                fallback_without_caption=fallback_without_caption,
                            )
                        await _safe_edit_status(
                            status,
                            "✅ Готово без подписи!"
                            if fallback_without_caption
                            else f"✅ Готово!\nRender: {_format_ms(render_ms)}"
                        )

                except Exception as e:
                    logger.exception("Error processing video")
                    if job_id:
                        storage.complete_processing_job(
                            job_id=job_id,
                            status="failed",
                            had_caption=bool(caption),
                            auto_caption_used=bool(word_timings),
                            manual_caption_used=bool(manual_caption),
                            caption_length=len(caption or ""),
                            transcribe_ms=transcribe_ms,
                            render_ms=render_ms,
                            fallback_without_caption=fallback_without_caption,
                            error_message=str(e)[:500],
                        )
                    await status.edit_text(
                        "❌ Не удалось обработать видео.\n"
                        "Попробуй ещё раз чуть позже или отправь другой файл."
                    )

    except ValueError as e:
        logger.info("Video validation failed for user %s: %s", uid, e)
        if status:
            await status.edit_text(f"⚠️ {e}")
        else:
            await message.answer(f"⚠️ {e}")
    except Exception:
        logger.exception("Unhandled video processing error for user %s", uid)
        if status:
            await status.edit_text(
                "❌ Не удалось обработать видео.\n"
                "Попробуй ещё раз чуть позже или отправь другой файл."
            )
        else:
            await message.answer(
                "❌ Не удалось обработать видео.\n"
                "Попробуй ещё раз чуть позже или отправь другой файл."
            )
    finally:
        _user_active.discard(uid)
        if uid in _processing_queue:
            _processing_queue.remove(uid)


@dp.message(lambda msg: msg.video_note)
async def handle_circle(message: Message):
    if not _is_allowed_user(message.from_user.id):
        await _deny_access_message(message)
        return
    await message.answer("Это уже кружок! Отправь обычное видео для конвертации.")


# ── entry point ───────────────────────────────────────────────────────────────

async def main():
    storage.initialize()
    storage.backup_database()
    _load_all_settings()
    logger.info("Settings loaded.")

    if AUTO_CAPTION:
        logger.info("Pre-loading Whisper model in background...")
        asyncio.create_task(warmup())
    if SQLITE_BACKUP_INTERVAL_HOURS > 0:
        asyncio.create_task(_sqlite_backup_loop())

    if WEBHOOK_URL:
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

        if not WEBHOOK_SECRET_TOKEN:
            raise ValueError("WEBHOOK_SECRET_TOKEN is required when webhook mode is enabled")

        await bot.set_webhook(
            url=WEBHOOK_URL + WEBHOOK_PATH,
            drop_pending_updates=True,
            allowed_updates=ALLOWED_UPDATES,
            secret_token=WEBHOOK_SECRET_TOKEN,
        )
        logger.info(f"Webhook set: {WEBHOOK_URL + WEBHOOK_PATH}")

        app = web.Application()
        SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=WEBHOOK_SECRET_TOKEN,
        ).register(app, path=WEBHOOK_PATH)
        if _dashboard_enabled():
            app.router.add_get("/admin/circle-bot", _dashboard_home)
            app.router.add_get("/admin/circle-bot/login", _dashboard_login_page)
            app.router.add_post("/admin/circle-bot/login", _dashboard_login_submit)
            app.router.add_get("/admin/circle-bot/logout", _dashboard_logout)
            app.router.add_post("/admin/circle-bot/broadcast", _dashboard_broadcast)
            app.router.add_post("/admin/circle-bot/backup", _dashboard_backup)
        setup_application(app, dp, bot=bot)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
        await site.start()
        logger.info(f"Webhook server started on {WEBHOOK_HOST}:{WEBHOOK_PORT}")
        # Keep running indefinitely
        await asyncio.Event().wait()
    else:
        logger.info("Starting polling...")
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    asyncio.run(main())
