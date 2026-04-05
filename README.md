# telegram-rounder

Telegram bot that turns regular videos into Telegram video notes and can add curved Russian captions, speech-to-text, style presets, and admin stats.

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

Recommended profile for a small `2 vCPU` VPS:

- `WHISPER_MODEL=base`
- `MAX_CONCURRENT=4`
- `TRANSCRIBE_CONCURRENT=1`
- `RENDER_CONCURRENT=2`

Recommended production setup:

- run behind nginx with webhook mode
- keep runtime data outside the app directory
- disable file logs and rely on `journald`
- enable `WEBHOOK_SECRET_TOKEN`
- use `INVITE_ONLY=true` if the bot is not public

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

## Repository Hygiene

Ignored by default:

- `.env`
- local virtualenvs
- logs
- debug renders
- SQLite files
- migrated runtime files

That keeps the repo safe to push as a private project without leaking deployment state.
