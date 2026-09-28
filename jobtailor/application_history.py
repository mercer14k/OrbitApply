"""Persistent, candidate-scoped application history using the standard library."""

import hashlib
import os
import platform
import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from .utils import TRACKING_QUERY_KEYS


def history_path():
    override = os.environ.get("JOBTAILOR_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    elif platform.system() == "Windows":
        base = (
            Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
            / "LocalJobTailor"
        )
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "LocalJobTailor"
    else:
        base = (
            Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
            / "LocalJobTailor"
        )
    return base / "application_history.sqlite3"


def candidate_key(prepared):
    contact = (prepared or {}).get("profile", {}).get("contact", {})
    email = str(contact.get("email") or "").strip().casefold()
    name = " ".join(
        unicodedata.normalize("NFKC", str(contact.get("name") or "")).casefold().split()
    )
    identity = f"email:{email}" if email else f"name:{name}" if name else "local-user"
    return hashlib.sha256(identity.encode()).hexdigest()


def posting_keys(row):
    """Never deduplicate by company/title: different requisitions must survive."""
    urls = [row.get("url"), row.get("job_url"), *(row.get("source_urls") or [])]
    keys = set()
    tracking = TRACKING_QUERY_KEYS | {
        "trk",
        "trackingid",
        "refid",
        "from",
        "fromage",
        "feedid",
        "campaign",
        "src",
        "origin",
        "referral",
        "gh_src",
        "lever-source",
        "lever-origin",
        "iis",
        "iisn",
    }
    for url in urls:
        if not isinstance(url, str):
            continue
        try:
            parts = urlsplit(url.strip())
            host = (parts.hostname or "").lower().removeprefix("www.")
            if parts.scheme.lower() not in {"https", "http"} or not host:
                continue
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            stable_query = sorted(
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
                if key.lower() not in tracking and not key.lower().startswith("utm_")
            )
            authority = host + (f":{parts.port}" if parts.port else "")
            keys.add(
                "url:"
                + authority
                + parts.path.rstrip("/")
                + ("?" + urlencode(stable_query) if stable_query else "")
            )
            path = [part for part in parts.path.split("/") if part]
            if host == "linkedin.com" or host.endswith(".linkedin.com"):
                match = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)", parts.path)
                job_id = match.group(1) if match else query.get("currentJobId", "")
                if job_id.isdigit():
                    keys.add(f"linkedin:{job_id}")
            elif host == "indeed.com" or host.endswith(".indeed.com"):
                job_id = query.get("jk") or query.get("vjk", "")
                if re.fullmatch(r"[A-Za-z0-9_-]{4,100}", job_id):
                    keys.add(f"indeed:{job_id}")
            elif host in {
                "boards.greenhouse.io",
                "job-boards.greenhouse.io",
                "boards.eu.greenhouse.io",
                "job-boards.eu.greenhouse.io",
            }:
                job_id = (
                    path[path.index("jobs") + 1]
                    if "jobs" in path and len(path) > path.index("jobs") + 1
                    else query.get("gh_jid", "")
                )
                if path and job_id.isdigit():
                    keys.add(f"greenhouse:{path[0].lower()}:{job_id}")
            elif host in {"jobs.lever.co", "jobs.eu.lever.co"} and len(path) >= 2:
                keys.add(f"lever:{path[0].lower()}:{path[1]}")
            elif host.endswith(".myworkdayjobs.com"):
                match = re.search(
                    r"(?:^|[/_-])((?:JR|REQ|R)[_-]?\d+)(?=[/_-]|$)",
                    parts.path,
                    re.IGNORECASE,
                )
                if match:
                    keys.add(f"workday:{host.split('.')[0]}:{match.group(1).upper()}")
        except ValueError:
            continue
    return keys


class ApplicationHistory:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else history_path()

    @contextmanager
    def _database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY, candidate TEXT NOT NULL,
                    role TEXT NOT NULL, company TEXT NOT NULL, url TEXT NOT NULL,
                    folder TEXT NOT NULL, applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS posting_keys (
                    candidate TEXT NOT NULL, posting_key TEXT NOT NULL,
                    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                    PRIMARY KEY (candidate, posting_key)
                );
            """)
            with db:
                yield db

    def keys(self, candidate):
        if not self.path.exists():
            return set()
        with self._database() as db:
            return {
                row[0]
                for row in db.execute(
                    "SELECT posting_key FROM posting_keys WHERE candidate = ?",
                    (candidate,),
                )
            }

    def mark_applied(self, candidate, row):
        keys = sorted(posting_keys(row))
        if not keys:
            raise ValueError("This posting has no valid application link to remember.")
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in keys)
            existing = db.execute(
                f"SELECT application_id FROM posting_keys WHERE candidate = ? AND posting_key IN ({placeholders}) LIMIT 1",
                (candidate, *keys),
            ).fetchone()
            if existing:
                application_id = existing[0]
                if row.get("folder"):
                    db.execute(
                        "UPDATE applications SET folder = ? WHERE id = ?",
                        (str(row["folder"]), application_id),
                    )
            else:
                cursor = db.execute(
                    "INSERT INTO applications(candidate, role, company, url, folder, applied_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        candidate,
                        str(row.get("role") or row.get("title") or ""),
                        str(row.get("company") or ""),
                        str(row.get("url") or row.get("job_url") or ""),
                        str(row.get("folder") or ""),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                application_id = cursor.lastrowid
            db.executemany(
                "INSERT OR IGNORE INTO posting_keys VALUES (?, ?, ?)",
                [(candidate, key, application_id) for key in keys],
            )
        return application_id

    def list_applied(self, candidate):
        if not self.path.exists():
            return []
        with self._database() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id, role, company, url, folder, applied_at FROM applications WHERE candidate = ? ORDER BY applied_at DESC",
                    (candidate,),
                )
            ]

    def undo(self, candidate, application_id):
        with self._database() as db:
            db.execute(
                "DELETE FROM applications WHERE candidate = ? AND id = ?",
                (candidate, application_id),
            )
