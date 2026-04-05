import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any


class Storage:
    def __init__(
        self,
        db_path: str,
        legacy_users_file: str,
        legacy_settings_file: str,
        backups_dir: str,
        backup_keep: int = 14,
    ):
        self.db_path = db_path
        self.legacy_users_file = legacy_users_file
        self.legacy_settings_file = legacy_settings_file
        self.backups_dir = backups_dir
        self.backup_keep = backup_keep
        self._lock = threading.Lock()

    def initialize(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        os.makedirs(self.backups_dir, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    caption_mode TEXT,
                    manual_caption TEXT,
                    text_color TEXT,
                    font TEXT,
                    font_size TEXT,
                    text_position TEXT,
                    text_bg INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metrics (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processing_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    source_duration INTEGER,
                    source_file_size INTEGER,
                    source_mime_type TEXT,
                    caption_mode TEXT,
                    had_caption INTEGER NOT NULL DEFAULT 0,
                    auto_caption_used INTEGER NOT NULL DEFAULT 0,
                    manual_caption_used INTEGER NOT NULL DEFAULT 0,
                    caption_length INTEGER NOT NULL DEFAULT 0,
                    transcribe_ms INTEGER,
                    render_ms INTEGER,
                    fallback_without_caption INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                )
                """
            )
            self._ensure_column(
                conn, "processing_jobs", "transcribe_ms", "INTEGER"
            )
            self._ensure_column(
                conn, "processing_jobs", "render_ms", "INTEGER"
            )
            self._ensure_column(
                conn,
                "processing_jobs",
                "fallback_without_caption",
                "INTEGER NOT NULL DEFAULT 0",
            )
        os.chmod(self.db_path, 0o600)
        self._migrate_legacy_files()

    def load_all_settings(self) -> dict[int, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, caption_mode, manual_caption, text_color, font,
                       font_size, text_position, text_bg
                FROM user_settings
                """
            ).fetchall()
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            result[row["user_id"]] = {
                "caption_mode": row["caption_mode"],
                "manual_caption": row["manual_caption"],
                "text_color": row["text_color"],
                "font": row["font"],
                "font_size": row["font_size"],
                "text_position": row["text_position"],
                "text_bg": bool(row["text_bg"]),
            }
        return result

    def save_user_settings(self, user_id: int, settings: dict[str, Any]):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (
                    user_id, caption_mode, manual_caption, text_color, font,
                    font_size, text_position, text_bg
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    caption_mode=excluded.caption_mode,
                    manual_caption=excluded.manual_caption,
                    text_color=excluded.text_color,
                    font=excluded.font,
                    font_size=excluded.font_size,
                    text_position=excluded.text_position,
                    text_bg=excluded.text_bg
                """,
                (
                    user_id,
                    settings.get("caption_mode"),
                    settings.get("manual_caption"),
                    settings.get("text_color"),
                    settings.get("font"),
                    settings.get("font_size"),
                    settings.get("text_position"),
                    1 if settings.get("text_bg") else 0,
                ),
            )

    def load_users(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, username, first_name, last_name, first_seen
                FROM users
                ORDER BY first_seen ASC
                """
            ).fetchall()
        return {
            str(row["user_id"]): {
                "id": row["user_id"],
                "username": row["username"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "first_seen": row["first_seen"],
            }
            for row in rows
        }

    def register_user(self, user_data: dict[str, Any]):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name, first_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (
                    user_data["id"],
                    user_data.get("username"),
                    user_data.get("first_name"),
                    user_data.get("last_name"),
                    user_data["first_seen"],
                ),
            )

    def increment_metric(self, key: str, amount: int = 1):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO metrics (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = value + excluded.value
                """,
                (key, amount),
            )

    def create_processing_job(
        self,
        user_id: int,
        source_duration: int | None,
        source_file_size: int | None,
        source_mime_type: str | None,
        caption_mode: str,
        manual_caption_used: bool,
    ) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO processing_jobs (
                    user_id, created_at, status, source_duration, source_file_size,
                    source_mime_type, caption_mode, manual_caption_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    self._now(),
                    "started",
                    source_duration,
                    source_file_size,
                    source_mime_type,
                    caption_mode,
                    1 if manual_caption_used else 0,
                ),
            )
            return int(cursor.lastrowid)

    def complete_processing_job(
        self,
        job_id: int,
        status: str,
        had_caption: bool,
        auto_caption_used: bool,
        manual_caption_used: bool,
        caption_length: int,
        transcribe_ms: int | None = None,
        render_ms: int | None = None,
        fallback_without_caption: bool = False,
        error_message: str | None = None,
    ):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE processing_jobs
                SET completed_at = ?, status = ?, had_caption = ?, auto_caption_used = ?,
                    manual_caption_used = ?, caption_length = ?, transcribe_ms = ?,
                    render_ms = ?, fallback_without_caption = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    self._now(),
                    status,
                    1 if had_caption else 0,
                    1 if auto_caption_used else 0,
                    1 if manual_caption_used else 0,
                    caption_length,
                    transcribe_ms,
                    render_ms,
                    1 if fallback_without_caption else 0,
                    error_message,
                    job_id,
                ),
            )

    def get_dashboard_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            starts = conn.execute(
                "SELECT COALESCE((SELECT value FROM metrics WHERE key = 'start_count'), 0)"
            ).fetchone()[0]
            dashboard_views = conn.execute(
                "SELECT COALESCE((SELECT value FROM metrics WHERE key = 'dashboard_views'), 0)"
            ).fetchone()[0]
            jobs = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_jobs,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_jobs,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_jobs,
                    SUM(CASE WHEN status = 'success' AND had_caption = 1 THEN 1 ELSE 0 END) AS captioned_jobs,
                    SUM(CASE WHEN status = 'success' AND auto_caption_used = 1 THEN 1 ELSE 0 END) AS auto_captioned_jobs,
                    SUM(CASE WHEN status = 'success' AND manual_caption_used = 1 THEN 1 ELSE 0 END) AS manual_captioned_jobs,
                    AVG(CASE WHEN transcribe_ms IS NOT NULL THEN transcribe_ms END) AS avg_transcribe_ms,
                    AVG(CASE WHEN render_ms IS NOT NULL THEN render_ms END) AS avg_render_ms,
                    SUM(CASE WHEN fallback_without_caption = 1 THEN 1 ELSE 0 END) AS fallback_jobs
                FROM processing_jobs
                """
            ).fetchone()
            recent_jobs = conn.execute(
                """
                SELECT id, user_id, created_at, completed_at, status, source_duration,
                       source_file_size, caption_mode, had_caption, auto_caption_used,
                       manual_caption_used, caption_length, transcribe_ms, render_ms,
                       fallback_without_caption, error_message
                FROM processing_jobs
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()
        return {
            "total_users": total_users,
            "start_count": starts,
            "dashboard_views": dashboard_views,
            "total_jobs": jobs["total_jobs"] or 0,
            "success_jobs": jobs["success_jobs"] or 0,
            "failed_jobs": jobs["failed_jobs"] or 0,
            "captioned_jobs": jobs["captioned_jobs"] or 0,
            "auto_captioned_jobs": jobs["auto_captioned_jobs"] or 0,
            "manual_captioned_jobs": jobs["manual_captioned_jobs"] or 0,
            "avg_transcribe_ms": int(jobs["avg_transcribe_ms"] or 0),
            "avg_render_ms": int(jobs["avg_render_ms"] or 0),
            "fallback_jobs": jobs["fallback_jobs"] or 0,
            "recent_jobs": [dict(row) for row in recent_jobs],
            "backups": self.list_backups(),
        }

    def backup_database(self) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(self.backups_dir, f"bot-{timestamp}.sqlite3")
        with self._lock:
            source = sqlite3.connect(self.db_path, timeout=5)
            dest = sqlite3.connect(backup_path)
            try:
                source.backup(dest)
            finally:
                dest.close()
                source.close()
        os.chmod(backup_path, 0o600)
        self.rotate_backups()
        return backup_path

    def rotate_backups(self):
        backups = self.list_backups()
        for item in backups[self.backup_keep :]:
            os.unlink(item["path"])

    def list_backups(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.backups_dir):
            return []
        items = []
        for name in sorted(os.listdir(self.backups_dir), reverse=True):
            path = os.path.join(self.backups_dir, name)
            if not os.path.isfile(path):
                continue
            stat = os.stat(path)
            items.append(
                {
                    "name": name,
                    "path": path,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
        return items

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_def: str,
    ):
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"
            )

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _migrate_legacy_files(self):
        if os.path.exists(self.legacy_users_file):
            with open(self.legacy_users_file, "r", encoding="utf-8") as f:
                users = json.load(f)
            for user in users.values():
                if isinstance(user, dict) and "id" in user and "first_seen" in user:
                    self.register_user(user)
            self._archive_legacy_file(self.legacy_users_file)

        if os.path.exists(self.legacy_settings_file):
            with open(self.legacy_settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
            for user_id, row in settings.items():
                if not str(user_id).isdigit():
                    continue
                self.save_user_settings(
                    int(user_id),
                    {
                        "caption_mode": row.get("caption_mode"),
                        "manual_caption": row.get("manual_caption"),
                        "text_color": row.get("text_color"),
                        "font": row.get("font"),
                        "font_size": row.get("font_size"),
                        "text_position": row.get("text_position"),
                        "text_bg": bool(row.get("text_bg")),
                    },
                )
            self._archive_legacy_file(self.legacy_settings_file)

    def _archive_legacy_file(self, path: str):
        archived = f"{path}.migrated"
        if os.path.exists(archived):
            os.unlink(archived)
        os.replace(path, archived)
        os.chmod(archived, 0o600)
