from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


RSS_URL = "https://feeds.fireside.fm/bibleinayear/rss"
SITE_URL = "https://bibleinayear.fireside.fm"
USER_AGENT = "nightstand-audio-bible-in-a-year-downloader/1.0"
DEFAULT_OUTPUT_DIR = Path("media/buttons/button-1")
DAY_RANGE = range(1, 366)
MIN_FREE_BYTES_AFTER_DOWNLOAD = 100 * 1024 * 1024


@dataclass(frozen=True)
class Episode:
    day_number: int
    title: str
    url: str
    audio_url: str
    filename: str
    expected_bytes: int | None = None
    source: str = "rss"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download Bible in a Year MP3 episodes from Fireside."
    )
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--feed-url", default=RSS_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest-name", default="manifest.json")
    parser.add_argument("--ignore-space-check", action="store_true")
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching RSS feed: {args.feed_url}")
    episodes = collect_episodes(args.feed_url, args.year)
    missing = sorted(set(DAY_RANGE) - set(episodes))

    if missing:
        print(f"RSS is missing days: {format_ranges(missing)}")
        crawled = crawl_missing_episodes(missing, args.year)
        episodes.update(crawled)

    missing = sorted(set(DAY_RANGE) - set(episodes))
    if missing:
        raise SystemExit(f"Could not find audio for days: {format_ranges(missing)}")

    ordered = [episodes[day] for day in DAY_RANGE]
    write_manifest(output_dir / args.manifest_name, ordered, args.feed_url, args.year)

    if args.manifest_only:
        print(f"Wrote manifest only: {output_dir / args.manifest_name}")
        return 0

    ensure_space_available(output_dir, ordered, args.ignore_space_check)
    download_episodes(output_dir, ordered)
    write_manifest(output_dir / args.manifest_name, ordered, args.feed_url, args.year)

    print(f"Done. Downloaded or verified {len(ordered)} episodes in {output_dir}")
    return 0


def collect_episodes(feed_url: str, year: int) -> dict[int, Episode]:
    root = ET.fromstring(fetch_bytes(feed_url))
    by_day: dict[int, Episode] = {}

    for item in root.findall("./channel/item"):
        raw_title = (item.findtext("title") or "").strip()
        if f"({year})" not in raw_title:
            continue

        day_number = parse_day_number(raw_title)
        if day_number is None or day_number not in DAY_RANGE:
            continue

        enclosure = item.find("enclosure")
        audio_url = enclosure.attrib.get("url", "").strip() if enclosure is not None else ""
        if not audio_url:
            continue

        page_url = (item.findtext("link") or "").strip()
        title = clean_episode_title(raw_title, day_number, year)
        expected = parse_int(enclosure.attrib.get("length")) if enclosure is not None else None
        episode = Episode(
            day_number=day_number,
            title=title,
            url=page_url,
            audio_url=audio_url,
            filename=episode_filename(day_number, title),
            expected_bytes=expected,
        )

        existing = by_day.get(day_number)
        if existing is None or is_better_episode_url(episode.url, existing.url, day_number, year):
            by_day[day_number] = episode

    return by_day


def crawl_missing_episodes(days: Iterable[int], year: int) -> dict[int, Episode]:
    found: dict[int, Episode] = {}
    remaining = set(days)

    for day in sorted(remaining):
        episode = crawl_day_page(day, year)
        if episode:
            found[day] = episode
            print(f"Recovered Day {day} from {episode.url}")

    remaining -= set(found)
    if remaining:
        found.update(crawl_archive_pages(remaining, year))

    return found


def crawl_day_page(day: int, year: int) -> Episode | None:
    slugs = [
        f"/day-{day}-{year}",
        f"/{year}-day-{day}",
        f"/day{day}-{year}",
        f"/biy-day{day}-{year}",
    ]
    for slug in slugs:
        page_url = urljoin(SITE_URL, slug)
        try:
            episode = episode_from_page(page_url, day, year)
        except HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        except URLError:
            continue
        if episode:
            return episode
    return None


def crawl_archive_pages(days: set[int], year: int) -> dict[int, Episode]:
    found: dict[int, Episode] = {}
    for page_number in range(1, 100):
        page_url = f"{SITE_URL}/episodes" if page_number == 1 else f"{SITE_URL}/episodes/page/{page_number}"
        try:
            text = fetch_text(page_url)
        except HTTPError as exc:
            if exc.code == 404:
                break
            raise

        links = sorted(set(re.findall(r'href=["\']([^"\']+)["\']', text)))
        for link in links:
            absolute = urljoin(SITE_URL, html.unescape(link))
            match = re.search(rf"(?:{year}-day-|day-?|biy-day)(\d{{1,3}}).*{year}", absolute)
            if not match:
                continue
            day = int(match.group(1))
            if day not in days or day in found:
                continue
            episode = episode_from_page(absolute, day, year)
            if episode:
                found[day] = episode
                print(f"Recovered Day {day} from archive page {page_number}")

        if days <= set(found):
            break
    return found


def episode_from_page(page_url: str, day: int, year: int) -> Episode | None:
    text = fetch_text(page_url)
    title = extract_page_title(text)
    if parse_day_number(title) != day:
        return None

    audio_urls = re.findall(r'https?://[^"\'<>\s]+\.mp3(?:\?[^"\'<>\s]*)?', text)
    if not audio_urls:
        return None

    title = clean_episode_title(f"{title} ({year})", day, year)
    audio_url = html.unescape(audio_urls[0])
    expected = head_content_length(audio_url)
    return Episode(
        day_number=day,
        title=title,
        url=page_url,
        audio_url=audio_url,
        filename=episode_filename(day, title),
        expected_bytes=expected,
        source="crawl",
    )


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        return response.read()


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8", "replace")


def head_content_length(url: str) -> int | None:
    request = Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response:
            return parse_int(response.headers.get("Content-Length"))
    except (HTTPError, URLError, TimeoutError):
        return None


def parse_day_number(title: str) -> int | None:
    match = re.search(r"\bDay\s+(\d{1,3})\b", html.unescape(title), re.IGNORECASE)
    return int(match.group(1)) if match else None


def clean_episode_title(raw_title: str, day_number: int, year: int) -> str:
    title = html.unescape(raw_title).strip()
    title = re.sub(rf"\s*\({year}\)\s*$", "", title)
    title = re.sub(rf"^\s*Day\s+{day_number}\s*:\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def extract_page_title(text: str) -> str:
    patterns = [
        r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        r"<h1[^>]*>(.*?)</h1>",
        r"<title[^>]*>(.*?)</title>",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            title = re.sub(r"<[^>]+>", "", match.group(1))
            title = html.unescape(title)
            return title.split(": ", 1)[-1].strip() if "Bible in a Year" in title else title.strip()
    return ""


def episode_filename(day_number: int, title: str) -> str:
    safe_title = sanitize_filename(title)
    return f"{day_number:03d} - Day {day_number} - {safe_title}.mp3"


def sanitize_filename(value: str) -> str:
    value = html.unescape(value)
    value = value.replace("/", "-").replace("\\", "-")
    value = re.sub(r'[:*?"<>|]', "", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:180]


def is_better_episode_url(candidate: str, existing: str, day: int, year: int) -> bool:
    canonical = f"/day-{day}-{year}"
    legacy = f"/{year}-day-{day}"
    candidate_score = (candidate.endswith(canonical), candidate.endswith(legacy), not candidate.endswith("-"))
    existing_score = (existing.endswith(canonical), existing.endswith(legacy), not existing.endswith("-"))
    return candidate_score > existing_score


def parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def ensure_space_available(output_dir: Path, episodes: list[Episode], ignore_check: bool) -> None:
    if ignore_check:
        return

    required = 0
    unknown = 0
    for episode in episodes:
        target = output_dir / episode.filename
        if target.exists() and target.stat().st_size > 0:
            continue
        if episode.expected_bytes is None:
            unknown += 1
            continue
        required += episode.expected_bytes

    available = shutil.disk_usage(output_dir).free
    needed = required + MIN_FREE_BYTES_AFTER_DOWNLOAD
    if required and available < needed:
        short = needed - available
        raise SystemExit(
            "Not enough free disk space for remaining downloads. "
            f"Need about {bytes_to_gib(needed):.2f} GiB including a small safety margin; "
            f"available {bytes_to_gib(available):.2f} GiB; short {bytes_to_gib(short):.2f} GiB. "
            "Free space, then rerun this script."
        )
    if unknown:
        print(f"Warning: {unknown} episodes have unknown sizes; continuing after free-space check.")


def download_episodes(output_dir: Path, episodes: list[Episode]) -> None:
    for index, episode in enumerate(episodes, start=1):
        target = output_dir / episode.filename
        part = target.with_suffix(target.suffix + ".part")
        if target.exists() and target.stat().st_size > 0:
            print(f"[{index:03d}/365] Skip existing {target.name}")
            continue

        if part.exists():
            part.unlink()

        print(f"[{index:03d}/365] Download {target.name}")
        download_file(episode.audio_url, part)
        if part.stat().st_size == 0:
            raise RuntimeError(f"Downloaded empty file for {target.name}")
        part.replace(target)


def download_file(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    last_report = time.monotonic()
    bytes_written = 0

    with urlopen(request, timeout=120) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 512)
            if not chunk:
                break
            handle.write(chunk)
            bytes_written += len(chunk)
            now = time.monotonic()
            if now - last_report >= 10:
                print(f"    {bytes_to_mib(bytes_written):.1f} MiB...")
                last_report = now


def write_manifest(path: Path, episodes: list[Episode], feed_url: str, year: int) -> None:
    payload = {
        "source": "The Bible in a Year (with Fr. Mike Schmitz)",
        "feed_url": feed_url,
        "year": year,
        "episode_count": len(episodes),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "episodes": [
            {
                "title": episode.title,
                "day_number": episode.day_number,
                "url": episode.url,
                "audio_url": episode.audio_url,
                "filename": episode.filename,
            }
            for episode in episodes
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote manifest: {path}")


def bytes_to_mib(value: int) -> float:
    return value / 1024 / 1024


def bytes_to_gib(value: int) -> float:
    return value / 1024 / 1024 / 1024


def format_ranges(values: Iterable[int]) -> str:
    values = sorted(values)
    if not values:
        return ""
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(f"{start}" if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(f"{start}" if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
