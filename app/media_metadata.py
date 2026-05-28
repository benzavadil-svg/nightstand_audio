from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote_plus


_EPISODE_PREFIX_RE = re.compile(
    r"^\s*(?:ep(?:isode)?\.?\s*)([0-9]{1,5})\s*(?:[-:–—]\s*)?(.*)$",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"\s+(?:-|–|—)\s+")
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ParsedDisplayTitle:
    title: str
    episode_label: str = ""
    show_title: str = ""

    @property
    def metadata_label(self) -> str:
        return self.episode_label


def clean_metadata_text(value: str | None) -> str:
    if not value:
        return ""
    decoded = unquote_plus(str(value))
    decoded = decoded.replace("_", " ")
    decoded = decoded.replace("\u2013", "-").replace("\u2014", "-")
    decoded = _WHITESPACE_RE.sub(" ", decoded)
    return decoded.strip()


def clean_display_title(raw_title: str) -> str:
    return parse_display_title(raw_title).title


def parse_display_title(raw_title: str, show_hint: str | None = None) -> ParsedDisplayTitle:
    cleaned = clean_metadata_text(raw_title)
    if not cleaned:
        return ParsedDisplayTitle(title="Untitled")

    episode_label = ""
    working = cleaned
    match = _EPISODE_PREFIX_RE.match(working)
    if match:
        episode_label = f"Ep {int(match.group(1)):03d}"
        working = match.group(2).strip()

    parts = [part.strip(" -") for part in _SEPARATOR_RE.split(working) if part.strip(" -")]
    show_title = ""
    title = working
    if len(parts) >= 2:
        show_title = parts[0]
        title = " - ".join(parts[1:])

    title = _strip_show_branding(title, show_hint)
    title = _strip_show_branding(title, show_title)
    title = _normalize_title_text(title)

    return ParsedDisplayTitle(
        title=title or cleaned,
        episode_label=episode_label,
        show_title=_normalize_title_text(show_title),
    )


def _strip_show_branding(title: str, show_name: str | None) -> str:
    normalized_show = clean_metadata_text(show_name)
    if not normalized_show:
        return title
    title_clean = clean_metadata_text(title)
    if title_clean.lower() == normalized_show.lower():
        return ""
    prefix = f"{normalized_show} - "
    if title_clean.lower().startswith(prefix.lower()):
        return title_clean[len(prefix) :]
    return title_clean


def _normalize_title_text(title: str) -> str:
    normalized = clean_metadata_text(title).strip(" -")
    normalized = re.sub(r"\b[Vv][Ss]\.?(?=\s|$)", "vs", normalized)
    return _WHITESPACE_RE.sub(" ", normalized).strip()
