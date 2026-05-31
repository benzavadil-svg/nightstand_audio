from __future__ import annotations

from app.config import get_settings
from app.media_library import MediaLibrary
from app.state_store import StateStore


def main() -> None:
    settings = get_settings()
    store = StateStore(settings.db_path)
    library = MediaLibrary(settings.media_dir, store)
    count = library.rebuild_index()
    print(f"Rebuilt media index with {count} items.")
    print(f"Cache: {library.index_path}")


if __name__ == "__main__":
    main()
