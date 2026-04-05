import unittest

from bot_logic import clear_caption_state, normalize_caption_mode, resolve_caption_strategy


class BotLogicTests(unittest.TestCase):
    def test_normalize_caption_mode_uses_default(self):
        self.assertEqual(normalize_caption_mode(None, True), "auto")
        self.assertEqual(normalize_caption_mode(None, False), "off")

    def test_manual_caption_wins_only_in_manual_mode(self):
        use_auto, manual_caption, mode = resolve_caption_strategy("manual", "hello", True)
        self.assertFalse(use_auto)
        self.assertEqual(manual_caption, "hello")
        self.assertEqual(mode, "manual")

    def test_auto_mode_ignores_stored_manual_caption(self):
        use_auto, manual_caption, mode = resolve_caption_strategy("auto", "stale", True)
        self.assertTrue(use_auto)
        self.assertIsNone(manual_caption)
        self.assertEqual(mode, "auto")

    def test_clear_caption_state(self):
        self.assertEqual(clear_caption_state(True), ("auto", None))
        self.assertEqual(clear_caption_state(False), ("off", None))


if __name__ == "__main__":
    unittest.main()
