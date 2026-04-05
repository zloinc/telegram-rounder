import unittest

from processor import _curve_units, _group_words_into_chunks, _join_caption_tokens
from speech import (
    _dedupe_repeated_clauses,
    _dedupe_word_timestamps,
    _merge_punctuation_tokens,
    _normalize_caption_text,
    _post_process_transcription,
)


class CaptionTextTests(unittest.TestCase):
    def test_normalize_caption_text_removes_spaces_before_punctuation(self):
        self.assertEqual(
            _normalize_caption_text('Друзья , у нас есть русский язык .'),
            'Друзья, у нас есть русский язык.',
        )

    def test_merge_punctuation_tokens_attaches_to_previous_word(self):
        words = [
            {"word": "Друзья", "start": 0.0, "end": 0.4},
            {"word": ",", "start": 0.4, "end": 0.45},
            {"word": "у", "start": 0.5, "end": 0.6},
            {"word": "нас", "start": 0.6, "end": 0.8},
            {"word": ".", "start": 0.8, "end": 0.85},
        ]

        merged = _merge_punctuation_tokens(words)

        self.assertEqual([item["word"] for item in merged], ["Друзья,", "у", "нас."])

    def test_join_caption_tokens_keeps_punctuation_near_words(self):
        self.assertEqual(
            _join_caption_tokens(["Друзья,", "у", "нас", "есть", "язык."]),
            "Друзья, у нас есть язык.",
        )

    def test_group_words_into_chunks_preserves_punctuation_spacing(self):
        words = [
            {"word": "Привет,", "start": 0.0, "end": 0.2},
            {"word": "мир!", "start": 0.21, "end": 0.5},
        ]

        chunks = _group_words_into_chunks(words, max_words=2, max_gap=0.7)

        self.assertEqual(chunks[0]["text"], "Привет, мир!")

    def test_curve_units_attach_trailing_punctuation(self):
        units = _curve_units("Привет, мир.")

        self.assertEqual(
            [unit["text"] for unit in units],
            ["П", "р", "и", "в", "е", "т,", " ", "м", "и", "р."],
        )

    def test_dedupe_repeated_clauses_removes_adjacent_duplicates(self):
        self.assertEqual(
            _dedupe_repeated_clauses("Друзья, у нас есть язык. Друзья, у нас есть язык."),
            "Друзья, у нас есть язык.",
        )

    def test_dedupe_word_timestamps_skips_noise_and_duplicates(self):
        words = [
            {"word": "эм", "start": 0.0, "end": 0.1},
            {"word": "привет", "start": 0.2, "end": 0.3},
            {"word": "привет", "start": 0.31, "end": 0.4},
            {"word": "мир", "start": 0.5, "end": 0.7},
        ]
        self.assertEqual(
            [item["word"] for item in _dedupe_word_timestamps(words)],
            ["привет", "мир"],
        )

    def test_post_process_transcription_normalizes_case_and_noise(self):
        text, words = _post_process_transcription(
            ["эм", "друзья , у нас есть язык .", "друзья , у нас есть язык ."],
            [
                {"word": "эм", "start": 0.0, "end": 0.1},
                {"word": "друзья", "start": 0.2, "end": 0.4},
                {"word": ",", "start": 0.4, "end": 0.41},
                {"word": "друзья", "start": 0.5, "end": 0.7},
            ],
        )
        self.assertEqual(text, "Друзья, у нас есть язык.")
        self.assertEqual([item["word"] for item in words], ["друзья,"])


if __name__ == "__main__":
    unittest.main()
