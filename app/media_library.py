from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.media_metadata import ParsedDisplayTitle, clean_metadata_text, parse_display_title
from app.models import AUDIO_EXTENSIONS, SOURCE_DEFINITIONS, MediaItem, SourceMetadata
from app.services.logger import get_logger
from app.state_store import StateStore


class MediaLibrary:
    def __init__(self, media_dir: Path, store: StateStore) -> None:
        self.media_dir = media_dir
        self.store = store
        self._metadata_cache: dict[str, SourceMetadata] = {}
        self.log = get_logger("STATE")

    def scan(self) -> int:
        items: list[MediaItem] = []
        self._metadata_cache = {}
        for source in SOURCE_DEFINITIONS.values():
            source_dir = self.media_dir / source.relative_dir
            source_dir.mkdir(parents=True, exist_ok=True)
            metadata = self.get_source_metadata(source.id)
            scan_dirs = [source_dir]

            legacy_dir = self.media_dir / source.legacy_relative_dir if source.legacy_relative_dir else None
            if legacy_dir and legacy_dir.exists() and not self._has_audio_files(source_dir):
                scan_dirs.append(legacy_dir)

            for scan_dir in scan_dirs:
                for index, path in enumerate(self._ordered_audio_files(scan_dir, metadata.ordering)):
                    display = self._display_metadata_for_file(path, metadata.display_name)
                    items.append(
                        MediaItem(
                            source_id=source.id,
                            file_path=str(path),
                            title=display.title,
                            artist=display.metadata_label,
                            sort_key=self._sort_key(scan_dir, path, index),
                        )
                    )
        return self.store.upsert_media_items(items)

    def ensure_demo_library(self) -> int:
        demo_specs = {
            "button-1": ["Slot 1 Episode 001", "Slot 1 Episode 002", "Slot 1 Episode 003"],
            "button-2": ["Slot 2 Episode 001", "Slot 2 Episode 002", "Slot 2 Episode 003"],
            "button-3": ["Slot 3 Track 001", "Slot 3 Track 002", "Slot 3 Track 003"],
            "sounds": ["Brown Noise", "Window Rain", "Distant Fan"],
        }
        demo_items: list[MediaItem] = []
        for source_id, titles in demo_specs.items():
            if self.store.get_source_queue(source_id):
                continue
            metadata = self.get_source_metadata(source_id)
            for index, title in enumerate(titles, start=1):
                demo_items.append(
                    MediaItem(
                        source_id=source_id,
                        file_path=f"demo://{source_id}/{index:02d}",
                        title=title,
                        artist=metadata.display_name,
                        duration_seconds=30 * 60,
                        sort_key=f"{source_id}/{index:04d}",
                    )
                )
        return self.store.upsert_media_items(demo_items)

    def get_resume_item(self, source_id: str) -> MediaItem | None:
        if self.is_source_complete(source_id):
            return None
        return self.store.get_resume_item(source_id)

    def get_queue(self, source_id: str) -> list[MediaItem]:
        return self.store.get_source_queue(source_id)

    def get_item_at_index(self, source_id: str, index: int) -> MediaItem | None:
        queue = self.get_queue(source_id)
        if 0 <= index < len(queue):
            return queue[index]
        return None

    def index_for_item(self, source_id: str, item_id: int | None) -> int | None:
        if item_id is None:
            return None
        for index, item in enumerate(self.get_queue(source_id)):
            if item.id == item_id:
                return index
        return None

    def get_source_metadata(self, source_id: str) -> SourceMetadata:
        if source_id in self._metadata_cache:
            return self._metadata_cache[source_id]

        source = SOURCE_DEFINITIONS.get(source_id)
        source_dir = self.media_dir / source.relative_dir if source else self.media_dir / source_id
        data = self._read_metadata(source_dir / ".source.json")
        display_name = clean_metadata_text(data.get("display_name")) or self._display_name_from_source_id(source_id)
        source_type = data.get("source_type") or self._infer_source_type(source_dir)
        metadata = SourceMetadata(
            source_id=source_id,
            display_name=clean_metadata_text(str(display_name)),
            source_type=str(source_type),
            ordering=str(data.get("ordering", "filename_asc")),
            resume_policy=str(data.get("resume_policy", "resume_playlist")),
            completion_threshold_percent=int(data.get("completion_threshold_percent", 95)),
            end_behavior=str(data.get("end_behavior", "stop")),
            loop_enabled=bool(data.get("loop_enabled", False)),
        )
        self._metadata_cache[source_id] = metadata
        return metadata

    def get_source_label(self, source_id: str | None) -> str:
        if not source_id:
            return ""
        return self.get_source_metadata(source_id).display_name

    def should_loop(self, source_id: str) -> bool:
        metadata = self.get_source_metadata(source_id)
        return source_id == "sounds" or metadata.source_type == "ambient" or metadata.loop_enabled or metadata.end_behavior == "loop"

    def is_source_complete(self, source_id: str) -> bool:
        metadata = self.get_source_metadata(source_id)
        if metadata.source_type != "podcast":
            return False
        queue = self.get_queue(source_id)
        return bool(queue) and self.get_completed_count(source_id) >= len(queue)

    def get_completed_count(self, source_id: str) -> int:
        return self.store.get_completed_count(source_id)

    def reset_source_progress(self, source_id: str) -> None:
        self.store.reset_source_progress(source_id)

    def completion_threshold(self, source_id: str | None) -> float:
        if not source_id:
            return 0.95
        percent = self.get_source_metadata(source_id).completion_threshold_percent
        return max(1, min(100, percent)) / 100

    def _read_metadata(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _ordered_audio_files(self, directory: Path, ordering: str) -> list[Path]:
        files = [
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        ]
        reverse = ordering == "filename_desc"
        return sorted(files, key=lambda path: str(path.relative_to(directory)).lower(), reverse=reverse)

    def _has_audio_files(self, directory: Path) -> bool:
        return bool(self._ordered_audio_files(directory, "filename_asc"))

    def _display_metadata_for_file(self, path: Path, source_label: str):
        tags = self._read_audio_tags(path)
        raw_title = tags.get("title") or self._title_from_path(path)
        show_hint = tags.get("album") or tags.get("artist") or source_label
        try:
            parsed = parse_display_title(raw_title, show_hint=show_hint)
        except Exception as exc:
            self.log.warning("Metadata parsing failed path=%s error=%s", path, exc)
            return parse_display_title(self._title_from_path(path), show_hint=source_label)
        metadata_label = parsed.metadata_label or tags.get("album") or tags.get("artist") or ""
        return ParsedDisplayTitle(
            title=parsed.title,
            episode_label=metadata_label,
            show_title=parsed.show_title,
        )

    def _read_audio_tags(self, path: Path) -> dict[str, str]:
        if path.stat().st_size == 0:
            return {}
        try:
            from mutagen import File as MutagenFile
        except ImportError:
            self.log.debug("Mutagen unavailable; using filename metadata path=%s", path)
            return {}

        try:
            audio = MutagenFile(path, easy=True)
        except Exception as exc:
            self.log.warning("Audio metadata read failed path=%s error=%s", path, exc)
            return {}
        if not audio or not getattr(audio, "tags", None):
            return {}

        tags: dict[str, str] = {}
        for key in ("title", "album", "artist"):
            value = audio.tags.get(key) if audio.tags else None
            text = self._first_tag_value(value)
            if text:
                tags[key] = clean_metadata_text(text)
        return tags

    def _first_tag_value(self, value: Any) -> str:
        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else ""
        return str(value) if value is not None else ""

    def _title_from_path(self, path: Path) -> str:
        decoded = clean_metadata_text(path.stem)
        normalized = re.sub(r"(?<=\S)-(?=\S)", " ", decoded)
        return re.sub(r"\s+", " ", normalized).strip().title()

    def _sort_key(self, base_dir: Path, path: Path, index: int) -> str:
        try:
            relative = path.relative_to(base_dir)
        except ValueError:
            relative = Path(path.name)
        return f"{index:06d}-{str(relative).lower()}"

    def _display_name_from_source_id(self, source_id: str) -> str:
        defaults = {
            "button-1": "Bible in a Year",
            "button-2": "Sleep Baseball",
            "button-3": "Button 3",
            "sounds": "Sleep Sounds",
        }
        if source_id in defaults:
            return defaults[source_id]
        return source_id.replace("-", " ").title()

    def _infer_source_type(self, source_dir: Path) -> str:
        parts = {part.lower() for part in source_dir.parts}
        if "sounds" in parts:
            return "ambient"
        if source_dir.exists() and any(path.is_dir() for path in source_dir.iterdir()):
            return "music"
        return "playlist"
