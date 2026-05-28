from __future__ import annotations

import unittest

from app.media_metadata import clean_display_title, clean_metadata_text, parse_display_title


class MediaMetadataTest(unittest.TestCase):
    def test_clean_display_title_decodes_and_strips_podcast_branding(self) -> None:
        raw = (
            "Ep%20040%20-%20Northwoods%20Baseball%20Sleep%20Radio%20-%20"
            "Lake%20City%20Loons%20vs.%20South%20Haven%20Ravens"
        )

        self.assertEqual(
            clean_display_title(raw),
            "Lake City Loons vs South Haven Ravens",
        )

    def test_parse_display_title_extracts_episode_label_and_show(self) -> None:
        parsed = parse_display_title(
            "Ep%20040%20-%20Northwoods%20Baseball%20Sleep%20Radio%20-%20"
            "Lake%20City%20Loons%20vs.%20South%20Haven%20Ravens"
        )

        self.assertEqual(parsed.episode_label, "Ep 040")
        self.assertEqual(parsed.show_title, "Northwoods Baseball Sleep Radio")
        self.assertEqual(parsed.title, "Lake City Loons vs South Haven Ravens")

    def test_clean_metadata_text_url_decodes_common_metadata_strings(self) -> None:
        self.assertEqual(
            clean_metadata_text("Northwoods%20Baseball+Sleep%20Radio"),
            "Northwoods Baseball Sleep Radio",
        )


if __name__ == "__main__":
    unittest.main()
