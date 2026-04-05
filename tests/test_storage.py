import tempfile
import unittest
from pathlib import Path

from storage import Storage


class StorageTests(unittest.TestCase):
    def test_storage_roundtrip_and_legacy_migration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            users_file = temp_path / "users.json"
            settings_file = temp_path / "settings.json"
            db_file = temp_path / "bot.sqlite3"
            backups_dir = temp_path / "backups"

            users_file.write_text(
                '{"1":{"id":1,"username":"user","first_name":"A","last_name":"B","first_seen":"2026-01-01T00:00:00"}}',
                encoding="utf-8",
            )
            settings_file.write_text(
                '{"1":{"caption_mode":"manual","manual_caption":"hello","text_color":"white","font":"helvetica","font_size":"M","text_position":"bottom","text_bg":true}}',
                encoding="utf-8",
            )

            storage = Storage(
                str(db_file),
                str(users_file),
                str(settings_file),
                str(backups_dir),
            )
            storage.initialize()

            users = storage.load_users()
            settings = storage.load_all_settings()

            self.assertIn("1", users)
            self.assertEqual(settings[1]["manual_caption"], "hello")
            self.assertTrue((temp_path / "users.json.migrated").exists())
            self.assertTrue((temp_path / "settings.json.migrated").exists())

    def test_backup_rotation_keeps_latest_files_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_file = temp_path / "bot.sqlite3"
            users_file = temp_path / "users.json"
            settings_file = temp_path / "settings.json"
            backups_dir = temp_path / "backups"

            storage = Storage(
                str(db_file),
                str(users_file),
                str(settings_file),
                str(backups_dir),
                backup_keep=2,
            )
            storage.initialize()
            storage.increment_metric("start_count")

            first = Path(storage.backup_database())
            second = backups_dir / "bot-99999999-000001.sqlite3"
            third = backups_dir / "bot-99999999-000002.sqlite3"
            second.write_bytes(first.read_bytes())
            third.write_bytes(first.read_bytes())
            storage.rotate_backups()

            backups = storage.list_backups()

            self.assertEqual(len(backups), 2)
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            self.assertTrue(third.exists())

    def test_processing_job_stats_include_timings_and_fallbacks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            storage = Storage(
                str(temp_path / "bot.sqlite3"),
                str(temp_path / "users.json"),
                str(temp_path / "settings.json"),
                str(temp_path / "backups"),
            )
            storage.initialize()

            job_id = storage.create_processing_job(
                user_id=1,
                source_duration=12,
                source_file_size=1024,
                source_mime_type="video/mp4",
                caption_mode="auto",
                manual_caption_used=False,
            )
            storage.complete_processing_job(
                job_id=job_id,
                status="success",
                had_caption=False,
                auto_caption_used=False,
                manual_caption_used=False,
                caption_length=0,
                transcribe_ms=1200,
                render_ms=3400,
                fallback_without_caption=True,
            )

            stats = storage.get_dashboard_stats()

            self.assertEqual(stats["avg_transcribe_ms"], 1200)
            self.assertEqual(stats["avg_render_ms"], 3400)
            self.assertEqual(stats["fallback_jobs"], 1)
            self.assertEqual(stats["recent_jobs"][0]["fallback_without_caption"], 1)


if __name__ == "__main__":
    unittest.main()
