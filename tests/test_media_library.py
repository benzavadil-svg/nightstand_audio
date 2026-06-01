from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from app.media_library import MediaLibrary
from app.models import MediaItem
from app.state_store import StateStore


class MediaLibraryBehaviorTest(unittest.TestCase):
    def test_podcast_prefers_partial_then_next_unplayed_in_sort_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                        duration_seconds=100,
                    ),
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/002",
                        title="Day 002",
                        sort_key="002",
                        duration_seconds=100,
                    ),
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/003",
                        title="Day 003",
                        sort_key="003",
                        duration_seconds=100,
                    ),
                ]
            )
            first = store.get_resume_item("button-1")
            self.assertEqual(first.title, "Day 001")

            store.update_playback_position(first.id, 99, completed=True)
            second = store.get_resume_item("button-1")
            self.assertEqual(second.title, "Day 002")

            store.update_playback_position(second.id, 20, completed=False)
            resumed = store.get_resume_item("button-1")
            self.assertEqual(resumed.title, "Day 002")

    def test_music_uses_folder_path_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-3",
                        file_path="demo://music/b",
                        title="B",
                        sort_key="02-album/01-track",
                    ),
                    MediaItem(
                        source_id="button-3",
                        file_path="demo://music/a",
                        title="A",
                        sort_key="01-album/01-track",
                    ),
                ]
            )

            self.assertEqual(store.get_resume_item("button-3").title, "A")

    def test_button_folder_metadata_controls_label_and_scan_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_dir = root / "media"
            button_dir = media_dir / "buttons" / "button-1"
            button_dir.mkdir(parents=True)
            (button_dir / ".source.json").write_text(
                json.dumps(
                    {
                        "display_name": "Morning Lectures",
                        "source_type": "podcast",
                        "ordering": "filename_desc",
                        "loop_enabled": True,
                    }
                ),
                encoding="utf-8",
            )
            (button_dir / "001-first.mp3").write_text("", encoding="utf-8")
            (button_dir / "002-second.mp3").write_text("", encoding="utf-8")
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(media_dir, store)

            library.scan()
            metadata = library.get_source_metadata("button-1")
            queue = library.get_queue("button-1")

            self.assertEqual(metadata.display_name, "Morning Lectures")
            self.assertTrue(metadata.loop_enabled)
            self.assertEqual([item.title for item in queue], ["002 Second", "001 First"])
            self.assertEqual(
                queue[0].file_path,
                "buttons/button-1/002-second.mp3",
            )

    def test_scan_decodes_url_encoded_file_names_for_display(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_dir = root / "media"
            button_dir = media_dir / "buttons" / "button-2"
            button_dir.mkdir(parents=True)
            (button_dir / "Ep%20040%20-%20Northwoods%20Baseball.mp3").write_text(
                "",
                encoding="utf-8",
            )
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(media_dir, store)

            library.scan()
            queue = library.get_queue("button-2")

            self.assertEqual(library.get_source_label("button-2"), "Sleep Baseball")
            self.assertEqual(queue[0].title, "Northwoods Baseball")
            self.assertEqual(queue[0].artist, "Ep 040")

    def test_scan_stores_discovered_duration_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_dir = root / "media"
            button_dir = media_dir / "buttons" / "button-1"
            button_dir.mkdir(parents=True)
            (button_dir / "001-first.mp3").write_text("not real audio", encoding="utf-8")
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(media_dir, store)

            with patch.object(MediaLibrary, "_duration_seconds_for_file", return_value=123):
                library.scan()

            self.assertEqual(library.get_queue("button-1")[0].duration_seconds, 123)

    def test_scan_strips_repeated_podcast_branding_from_episode_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_dir = root / "media"
            button_dir = media_dir / "buttons" / "button-2"
            button_dir.mkdir(parents=True)
            encoded_name = (
                "Ep%20040%20-%20Northwoods%20Baseball%20Sleep%20Radio%20-%20"
                "Lake%20City%20Loons%20vs.%20South%20Haven%20Ravens.mp3"
            )
            (button_dir / encoded_name).write_text(
                "",
                encoding="utf-8",
            )
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(media_dir, store)

            library.scan()
            queue = library.get_queue("button-2")

            self.assertEqual(library.get_source_label("button-1"), "Bible in a Year")
            self.assertEqual(library.get_source_label("button-2"), "Sleep Baseball")
            self.assertEqual(queue[0].title, "Lake City Loons vs South Haven Ravens")
            self.assertEqual(queue[0].artist, "Ep 040")

    def test_cached_index_uses_relative_paths_and_resolves_current_media_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_dir = root / "media"
            button_dir = media_dir / "buttons" / "button-1"
            button_dir.mkdir(parents=True)
            media_file = button_dir / "001-first.mp3"
            media_file.write_text("", encoding="utf-8")
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(media_dir, store)

            library.scan()
            cached = json.loads(library.index_path.read_text(encoding="utf-8"))
            self.assertEqual(cached["items"][0]["file_path"], "buttons/button-1/001-first.mp3")

            store2 = StateStore(root / "second.sqlite")
            library2 = MediaLibrary(media_dir, store2)
            self.assertEqual(library2.load_cached_index(), 1)
            item = library2.get_queue("button-1")[0]
            self.assertEqual(
                str(library2.resolve_media_path(item.file_path)),
                str(media_file),
            )

    def test_cached_index_rejects_host_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_dir = root / "media"
            media_dir.mkdir()
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(media_dir, store)
            library.index_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "items": [
                            {
                                "source_id": "button-1",
                                "file_path": "/Users/benzabs/dev_work/nightstand-audio/media/buttons/button-1/old.mp3",
                                "title": "Old",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(library.load_cached_index(), 0)
            self.assertEqual(library.get_queue("button-1"), [])


if __name__ == "__main__":
    unittest.main()
