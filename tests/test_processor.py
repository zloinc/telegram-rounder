import unittest

from processor import _group_words_into_chunks


class ProcessorTests(unittest.TestCase):
    def test_group_words_by_gap_and_max_words(self):
        words = [
            {"word": "one", "start": 0.0, "end": 0.2},
            {"word": "two", "start": 0.25, "end": 0.4},
            {"word": "three", "start": 1.4, "end": 1.7},
            {"word": "four", "start": 1.8, "end": 2.0},
            {"word": "five", "start": 2.1, "end": 2.3},
        ]

        chunks = _group_words_into_chunks(words, max_words=2, max_gap=0.7)

        self.assertEqual(
            [chunk["text"] for chunk in chunks],
            ["one two", "three four", "five"],
        )
        self.assertAlmostEqual(chunks[0]["start"], 0.0)
        self.assertAlmostEqual(chunks[0]["end"], 0.4)
        self.assertGreater(chunks[-1]["end"], chunks[-1]["start"])

    def test_group_words_handles_empty_input(self):
        self.assertEqual(_group_words_into_chunks([]), [])

    def test_group_words_supports_cumulative_reveal(self):
        words = [
            {"word": "one", "start": 0.0, "end": 0.2},
            {"word": "two", "start": 0.3, "end": 0.5},
            {"word": "three", "start": 1.5, "end": 1.7},
        ]

        chunks = _group_words_into_chunks(
            words,
            max_words=2,
            max_gap=0.7,
            cumulative=True,
        )

        self.assertEqual(
            [chunk["text"] for chunk in chunks],
            ["one", "one two", "three"],
        )
        self.assertAlmostEqual(chunks[1]["start"], 0.3)
        self.assertGreater(chunks[1]["end"], chunks[1]["start"])


if __name__ == "__main__":
    unittest.main()
