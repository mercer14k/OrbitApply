from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "source",
    "ref",
    "referrer",
}


def slug_component(value: str, fallback: str = "Unknown", max_length: int = 70) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", value)
    value = re.sub(r"[^A-Za-z0-9._ -]+", " ", value)
    value = re.sub(r"[\s._-]+", "_", value).strip("._ ")
    if not value:
        value = fallback
    return value[:max_length].rstrip("._ ")


def canonical_url(value: str) -> str:
    if not value:
        return ""
    try:
        parts = urlsplit(value.strip())
        query = [
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in TRACKING_QUERY_KEYS
            and not key.casefold().startswith("utm_")
        ]
        return urlunsplit(
            (
                parts.scheme.casefold(),
                parts.netloc.casefold(),
                parts.path.rstrip("/"),
                urlencode(query),
                "",
            )
        )
    except ValueError:
        return value.strip()


def stable_listing_id(url: str, company: str, title: str, location: str) -> str:
    source = canonical_url(url) or f"{company}|{title}|{location}".casefold()
    return hashlib.sha256(source.encode("utf-8", errors="ignore")).hexdigest()[:16]


def ensure_unique_folder(root: Path, base_name: str) -> Path:
    candidate = root / base_name
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = root / f"{base_name}_{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def extract_numbers(value: str) -> set[str]:
    return set(re.findall(r"(?<!\w)[+$]?\d[\d,.]*(?:%|[KkMmBb])?(?!\w)", value or ""))


def escape_paragraph(value: str) -> str:
    return html.escape(value or "").replace("\n", "<br/>")
