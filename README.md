# telegram-rounder

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4?logo=telegram&logoColor=white)](https://core.telegram.org/bots/api)
[![Whisper](https://img.shields.io/badge/Speech-faster--whisper-111111)](https://github.com/SYSTRAN/faster-whisper)
[![Storage](https://img.shields.io/badge/Storage-SQLite-003B57?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![Rendering](https://img.shields.io/badge/Video-FFmpeg-007808?logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![Status](https://img.shields.io/badge/Repo-Private-6b7280)](https://github.com/zloinc/telegram-rounder)

Telegram bot for turning regular videos into Telegram video notes with optional Russian speech-to-text, curved caption overlays, admin metrics, and production-safe deployment primitives.

## What It Does

`telegram-rounder` takes a regular Telegram video and returns a rendered video note:

- converts the clip into a Telegram circle
- optionally recognizes speech with `faster-whisper`
- renders curved caption text with custom fonts and styles
- supports manual captions, automatic captions, and quick style preview
- records timing metrics and fallback behavior for admin monitoring

The project is designed for a small VPS setup and includes webhook mode, SQLite backups, invite-only access, and a protected admin dashboard.

## Features

- Converts incoming videos into Telegram video notes
- Optional speech recognition with `faster-whisper`
- Curved text overlay with custom fonts, color, size, position, and background
- `/preview` command for a fast style check on a sample circle
- Invite-only mode with allowlist support
- Webhook mode with Telegram secret header validation
- SQLite storage with backup rotation
- Built-in admin dashboard for usage and timing metrics
- Fallback render path: if caption overlay fails, the bot can still send the circle without text

## User Flow

1. User sends a video to the bot.
2. Bot validates file type, size, and duration.
3. Bot downloads the file and optionally transcribes speech.
4. Bot renders the circle and curved overlay.
5. If overlay rendering fails, bot retries and returns the circle without text.
6. Metrics are stored in SQLite and shown in the dashboard.

## Stack

- Python 3
- `python-telegram-bot`
- `faster-whisper`
- `ffmpeg` / `ffprobe`
- SQLite
- `aiohttp`
- Pillow

## Quick Start

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Install `ffmpeg` and `ffprobe`:

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt update
sudo apt install ffmpeg
```

3. Create a local env file:

```bash
cp .env.example .env
```

4. Start the bot:

```bash
python3 bot.py
```

For local development, polling mode is enough. For production, use webhook mode behind nginx.

## Bot Commands

| Command | Description |
| --- | --- |
| `/start` | Short guide and bot limits |
| `/settings` | Caption style settings |
| `/preview [text]` | Render a quick sample image with the current style |
| `/caption <text>` | Set a manual caption |
| `/clear` | Clear manual caption |
| `/autocaption on` | Enable automatic speech captions |
| `/autocaption off` | Disable automatic speech captions |

## Preview and Styling

The bot supports:

- multiple custom fonts
- text color selection
- size and position controls
- optional text background
- `/preview [text]` for a cheap sample render without processing a full video

The preview command renders a clean white Telegram-like circle and overlays the current caption style on top of it.

## Configuration

Copy `.env.example` to `.env` and set your values there.

Important variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `BOT_TOKEN` | Telegram bot token from `@BotFather` | required |
| `AUTO_CAPTION` | Enable automatic speech recognition by default | `true` |
| `WHISPER_LANGUAGE` | Recognition language | `ru` |
| `WHISPER_MODEL` | Whisper model optimized for CPU usage | `base` |
| `MAX_CONCURRENT` | Max active jobs in the bot | `4` |
| `TRANSCRIBE_CONCURRENT` | Max parallel transcription jobs | `1` |
| `RENDER_CONCURRENT` | Max parallel render jobs | `2` |
| `RENDER_TIMEOUT_SECONDS` | Caption render timeout before fallback | `90` |
| `DATA_DIR` | Directory for SQLite DB and backups | `./data` |
| `LOG_DIR` | Directory for optional file logs | `./data` |
| `ENABLE_FILE_LOG` | Write logs to file in addition to stdout/journald | `true` |
| `WEBHOOK_BASE_URL` | Public base URL for webhook deployment | empty |
| `WEBHOOK_SECRET_TOKEN` | Telegram webhook secret header | empty |
| `ALLOWED_UPDATES` | Telegram update types | `message,callback_query` |
| `INVITE_ONLY` | Restrict usage to allowlisted users | `false` |
| `ALLOWED_USER_IDS` | Comma-separated Telegram user IDs | empty |
| `MAX_VIDEO_DURATION` | Max accepted input duration in seconds | `60` |
| `MAX_VIDEO_FILE_SIZE_MB` | Max accepted input file size in MB | `25` |
| `SQLITE_BACKUP_INTERVAL_HOURS` | SQLite backup interval | `12` |
| `SQLITE_BACKUP_KEEP` | Number of backup files to keep | `14` |
| `DASHBOARD_USERNAME` | Dashboard login | empty |
| `DASHBOARD_PASSWORD` | Dashboard password | empty |

## Performance Profile

Recommended profile for a small `2 vCPU` VPS:

- `WHISPER_MODEL=base`
- `MAX_CONCURRENT=4`
- `TRANSCRIBE_CONCURRENT=1`
- `RENDER_CONCURRENT=2`
- `RENDER_TIMEOUT_SECONDS=90`

Observed bottleneck in real usage is usually render time, not download time. The bot therefore includes:

- stage-based progress messages for the user
- separate concurrency caps for transcription and rendering
- fallback rendering without text if overlay generation fails or times out

## Storage

Runtime state is stored in SQLite, not JSON:

- database: `DATA_DIR/bot.sqlite3`
- backups: `DATA_DIR/backups/`

The bot tracks:

- known users
- user settings
- processing metrics
- render/transcribe timings
- fallback render events

## Deployment Notes

Recommended production setup:

- run behind nginx with webhook mode
- keep runtime data outside the app directory
- disable file logs and rely on `journald`
- enable `WEBHOOK_SECRET_TOKEN`
- use `INVITE_ONLY=true` if the bot is not public

Minimal production layout:

```text
/opt/circle-bot/app
/opt/circle-bot/data
/opt/circle-bot/env
```

## Security

Do not commit or expose:

- `.env`
- production env files
- SQLite database files
- dashboard credentials
- bot tokens
- webhook secret tokens

This repository is intended to stay clean of runtime and secret material via `.gitignore`.

For webhook checks without putting the bot token into the shell history:

```bash
python3 scripts/get_webhook_info.py /path/to/env-file
```

## Admin Dashboard

The bot can expose a protected dashboard with:

- usage counters
- recent jobs
- transcription and render timings
- fallback counts
- backup status

Typical URL:

```text
https://your-domain/admin/circle-bot
```

## Testing

```bash
python3 -m py_compile bot.py bot_logic.py processor.py speech.py storage.py
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Repository Hygiene

Ignored by default:

- `.env`
- local virtualenvs
- logs
- debug renders
- SQLite files
- migrated runtime files

That keeps the repo safe to push as a private project without leaking deployment state.

## Roadmap Ideas

- style presets for different caption looks
- separate title mode vs subtitle mode
- better admin controls from the dashboard
- smarter post-processing for speech captions
- faster simple-render mode for short captions
