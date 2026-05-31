from __future__ import annotations

import json
import re
import time
import threading
from dataclasses import replace
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
        self.index_path = store.db_path.parent / "media_index.json"
        self._metadata_cache: dict[str, SourceMetadata] = {}
        self.log = get_logger("MEDIA")
        self._scan_lock = threading.Lock()
        self._background_scan_started = False

    def scan(self, source_id: str | None = None, write_cache: bool = True) -> int:
        with self._scan_lock:
            return self._scan_locked(source_id=source_id, write_cache=write_cache)

    def _scan_locked(self, source_id: str | None = None, write_cache: bool = True) -> int:
        items: list[MediaItem] = []
        self._metadata_cache = {}
        sources = (
            [SOURCE_DEFINITIONS[source_id]]
            if source_id and source_id in SOURCE_DEFINITIONS
            else list(SOURCE_DEFINITIONS.values())
        )
        for source in sources:
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
                            file_path=self._relative_media_path(path),
                            title=display.title,
                            artist=display.metadata_label,
                            sort_key=self._sort_key(scan_dir, path, index),
                        )
                    )
        count = self.store.upsert_media_items(items, prune_source_ids=[source.id for source in sources])
        if write_cache and source_id is None:
            self._write_index_cache(items)
        return count

    def prepare_startup_index(self) -> int:
        self._purge_invalid_media_items()
        started = time.perf_counter()
        loaded = self.load_cached_index()
        self.log.info(
            "Loaded cached index duration_ms=%.1f items=%s",
            (time.perf_counter() - started) * 1000,
            loaded,
        )
        return loaded

    def start_background_scan(self) -> None:
        if self._background_scan_started:
            return
        self._background_scan_started = True
        self.log.info("Background scan started")
        thread = threading.Thread(target=self._background_scan, name="media-scan", daemon=True)
        thread.start()

    def _background_scan(self) -> None:
        started = time.perf_counter()
        try:
            count = self.scan(write_cache=True)
        except Exception as exc:
            self.log.error("Background scan failed error=%s", exc)
            return
        self.log.info(
            "Background scan complete duration_ms=%.1f items=%s",
            (time.perf_counter() - started) * 1000,
            count,
        )

    def scan_source(self, source_id: str) -> int:
        started = time.perf_counter()
        count = self.scan(source_id=source_id, write_cache=False)
        self._refresh_cache_from_store()
        self.log.info(
            "Lazy scan source=%s duration_ms=%.1f items=%s",
            source_id,
            (time.perf_counter() - started) * 1000,
            count,
        )
        return count

    def ensure_source_ready(self, source_id: str) -> None:
        queue = self.get_queue(source_id)
        if not queue or any(self._stored_path_invalid_without_stat(item.file_path) for item in queue):
            self.scan_source(source_id)

    def load_cached_index(self) -> int:
        if not self.index_path.exists():
            return 0
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.log.info("cache_invalidated reason=read_error error=%s", exc)
            return 0
        if payload.get("version") != 1:
            self.log.info("cache_invalidated reason=version_mismatch")
            return 0
        items = []
        for raw in payload.get("items", []):
            try:
                item = MediaItem(**raw)
            except TypeError:
                self.log.info("cache_invalidated reason=invalid_item")
                return 0
            reason = self._cache_path_invalid_reason(item.file_path)
            if reason:
                self.log.info("cache_invalidated reason=%s path=%s", reason, item.file_path)
                return 0
            items.append(item)
        return self.store.upsert_media_items(items, prune_source_ids=SOURCE_DEFINITIONS.keys())

    def rebuild_index(self) -> int:
        self._purge_invalid_media_items()
        return self.scan(write_cache=True)

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

    def resolve_item(self, item: MediaItem) -> MediaItem:
        if item.file_path.startswith("demo://"):
            return item
        return replace(item, file_path=str(self.resolve_media_path(item.file_path)))

    def resolve_media_path(self, file_path: str) -> Path:
        if file_path.startswith("demo://"):
            return Path(file_path)
        path = Path(file_path).expanduser()
        if path.is_absolute():
            resolved = path
        else:
            resolved = self.media_dir / path
        exists = resolved.exists()
        self.log.info("resolved_path=%s exists=%s", resolved, str(exists).lower())
        return resolved

    def is_playable_item(self, item: MediaItem) -> bool:
        if item.file_path.startswith("demo://"):
            return True
        return self.resolve_media_path(item.file_path).exists()

    def _stored_path_invalid_without_stat(self, file_path: str) -> bool:
        if file_path.startswith("demo://"):
            return False
        path = Path(file_path).expanduser()
        return path.is_absolute()

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

    def _write_index_cache(self, items: list[MediaItem]) -> None:
        payload = {
            "version": 1,
            "items": [
                self._item_to_cache_payload(item)
                for item in items
                if not item.file_path.startswith("demo://")
                and not self._cache_path_invalid_reason(item.file_path)
            ],
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _item_to_cache_payload(self, item: MediaItem) -> dict[str, Any]:
        return {
            "source_id": item.source_id,
            "file_path": item.file_path,
            "title": item.title,
            "artist": item.artist,
            "duration_seconds": item.duration_seconds,
            "sort_key": item.sort_key,
            "last_position_seconds": item.last_position_seconds,
            "completed": item.completed,
            "play_count": item.play_count,
        }

    def _refresh_cache_from_store(self) -> None:
        items = [
            item
            for item in self.store.list_media()
            if not item.file_path.startswith("demo://")
            and not self._cache_path_invalid_reason(item.file_path)
        ]
        self._write_index_cache(items)

    def _cache_path_invalid_reason(self, file_path: str) -> str | None:
        if file_path.startswith("demo://"):
            return None
        path = Path(file_path).expanduser()
        if path.is_absolute():
            if str(path).startswith("/Users/"):
                return "host_path_mismatch"
            return "absolute_path"
        if not (self.media_dir / path).exists():
            return "missing_file"
        return None

    def _purge_invalid_media_items(self) -> int:
        invalid_paths = [
            item.file_path
            for item in self.store.list_media()
            if self._cache_path_invalid_reason(item.file_path)
        ]
        deleted = self.store.delete_media_items_by_paths(invalid_paths)
        if deleted:
            self.log.info(
                "cache_invalidated reason=host_path_mismatch deleted_items=%s",
                deleted,
            )
        return deleted

    def _ordered_audio_files(self, directory: Path, ordering: str) -> list[Path]:
        files = [
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
        ]
        reverse = ordering == "filename_desc"
        return sorted(files, key=lambda path: str(path.relative_to(directory)).lower(), reverse=reverse)

    def _relative_media_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.media_dir))
        except ValueError:
            return str(path)

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
