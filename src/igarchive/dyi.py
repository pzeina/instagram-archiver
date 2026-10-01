"""Reading Instagram's official export ("Download Your Information").

It is the only source that gives an item's *save date*: Instagram's interface
exposes the order and never the date. The export, in turn, does not contain the
saved videos. The two complete each other, which is why this module exists.

The format shifts between versions and depends on the account's language, so
nothing is assumed here: the JSON is walked recursively and every entry
carrying a link to an Instagram item is kept.
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from igarchive import i18n

SAVED_FILENAMES = ("saved_posts.json", "saved_collections.json")
SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")

# Folders the export lands in, depending on the system language.
DOWNLOAD_DIR_NAMES = (
    "Downloads", "Telechargements", "Téléchargements", "Descargas",
    "Downloads", "Scaricati", "Transferencias",
)
DESKTOP_DIR_NAMES = ("Desktop", "Bureau", "Escritorio", "Scrivania")


class ExportError(RuntimeError):
    """Export unreadable, or missing the files we are looking for."""


def shortcode_from_url(url: str) -> str | None:
    match = SHORTCODE_RE.search(url or "")
    return match.group(1) if match else None


def fix_mojibake(text: str) -> str:
    """Exports encode UTF-8 as latin-1: "Ã©" where "é" belongs."""
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _iter_files(source: Path) -> Iterator[tuple[str, bytes]]:
    """Yield (name, contents) for the wanted files, from a .zip or a folder."""
    if source.is_file() and source.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source) as archive:
                for info in archive.infolist():
                    if Path(info.filename).name in SAVED_FILENAMES:
                        yield info.filename, archive.read(info)
        except zipfile.BadZipFile as exc:
            raise ExportError(f"Archive illisible : {exc}") from exc
    elif source.is_dir():
        for name in SAVED_FILENAMES:
            for path in sorted(source.rglob(name)):
                yield str(path.relative_to(source)), path.read_bytes()
    else:
        raise ExportError(i18n.t("bad_export_source", source=source))


def _walk_entries(node: Any) -> Iterator[dict]:
    """Walk a JSON structure of unknown shape down to the useful entries."""
    if isinstance(node, dict):
        if isinstance(node.get("string_map_data"), dict):
            yield node
        for value in node.values():
            yield from _walk_entries(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_entries(value)


def _merge(previous: dict, incoming: dict) -> dict:
    """Merge two views of the same item.

    A post appears both in saved_posts.json and in every collection holding it:
    overwriting would lose either the date or the collection. The earliest date
    is kept -- the first time it was saved -- along with the union of collections.
    """
    incoming_ts = incoming.get("saved_timestamp")
    previous_ts = previous.get("saved_timestamp")
    keep_incoming_date = incoming_ts is not None and (
        previous_ts is None or incoming_ts < previous_ts
    )

    merged = {**previous, **{k: v for k, v in incoming.items() if v is not None}}
    if not keep_incoming_date:
        merged["saved_timestamp"] = previous_ts
        merged["saved_at"] = previous.get("saved_at")
        merged["source_file"] = previous.get("source_file")

    collections = set(previous.get("collections") or [])
    for source in (previous, incoming):
        if source.get("collection"):
            collections.add(source["collection"])
    if collections:
        merged["collections"] = sorted(collections)
        merged["collection"] = merged["collections"][0]
    return merged


def parse(source: Path) -> dict[str, dict]:
    """Return {item id: date record} for the whole export."""
    found: dict[str, dict] = {}
    files_seen = 0

    for filename, raw in _iter_files(source):
        files_seen += 1
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        is_collection = "saved_collections" in filename

        for entry in _walk_entries(payload):
            # The field name follows the account language: "Saved on", "Enregistré le"...
            for value in entry["string_map_data"].values():
                code = shortcode_from_url(value.get("href", ""))
                if not code:
                    continue
                timestamp = value.get("timestamp") or None
                title = fix_mojibake(entry.get("title") or "") or None
                record = {
                    "shortcode": code,
                    "url": value.get("href"),
                    "saved_timestamp": timestamp,
                    "saved_at": (
                        datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
                        if timestamp else None
                    ),
                    "author_hint": None if is_collection else title,
                    "collection": title if is_collection else None,
                    "source_file": filename,
                }
                found[code] = _merge(found[code], record) if code in found else record
                break

    if files_seen == 0:
        raise ExportError(i18n.t("export_missing_files",
                                 names=" / ".join(SAVED_FILENAMES), source=source))
    return found


def merge_into(existing: dict[str, dict], parsed: dict[str, dict]) -> tuple[dict, int]:
    """Add an export to what is already known. Returns (total, newly added)."""
    added = 0
    for code, record in parsed.items():
        if code in existing:
            existing[code] = _merge(existing[code], record)
        else:
            existing[code] = record
            added += 1
    return existing, added


# ---------------------------------------------------------------------------
# automatic detection
# ---------------------------------------------------------------------------

def looks_like_export(source: Path) -> bool:
    """True if this archive or folder holds the files we are after.

    The contents are inspected rather than the name: Instagram has renamed its
    exports several times, and a name is no proof of anything anyway.
    """
    try:
        if source.is_file() and source.suffix.lower() == ".zip":
            with zipfile.ZipFile(source) as archive:
                # Reading the index is enough; nothing is decompressed.
                return any(Path(n).name in SAVED_FILENAMES for n in archive.namelist())
        if source.is_dir():
            return any(next(source.rglob(name), None) is not None
                       for name in SAVED_FILENAMES)
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    return False


# An Instagram export carries "instagram" or "meta" in its name. That serves as
# a pre-filter: without it, detection would open every archive in the downloads
# folder, which is slow and has no business happening to files unrelated to
# this program.
EXPORT_NAME_HINTS = ("instagram", "meta-", "meta_")


def find_exports(extra_dirs: "list[Path] | None" = None, *,
                 search_home: bool = True) -> list[Path]:
    """Plausible Instagram exports, most recent first.

    A large export arrives split across several archives ("part-1", "part-2"...):
    this returns all of them, and the caller reads all of them.

    `search_home=False` limits the search to the folders passed in. Reading
    "Downloads", "Desktop" or the home folder raises a macOS permission prompt,
    attributed to whichever application launched the program: acceptable when
    the user has just typed "igarchive dyi", out of place in the middle of a
    backup they never connected to those folders.
    """
    roots: list[Path] = list(extra_dirs or [])
    if search_home:
        home = Path.home()
        roots += [home / name for name in DOWNLOAD_DIR_NAMES]
        roots += [home / name for name in DESKTOP_DIR_NAMES]
        roots.append(home)

    found: dict[Path, float] = {}
    for root in roots:
        if not root.is_dir():
            continue
        # One level only: walking the whole home folder would be far too slow.
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for candidate in entries:
            name = candidate.name.lower()
            if not any(hint in name for hint in EXPORT_NAME_HINTS):
                continue
            if candidate.is_file() and candidate.suffix.lower() != ".zip":
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in found or not looks_like_export(candidate):
                continue
            try:
                found[resolved] = candidate.stat().st_mtime
            except OSError:
                found[resolved] = 0.0
    return [path for path, _ in sorted(found.items(), key=lambda kv: -kv[1])]


def parse_all(sources: "list[Path]") -> dict[str, dict]:
    """Read several exports and merge them -- the split-export case."""
    merged: dict[str, dict] = {}
    for source in sources:
        merged, _ = merge_into(merged, parse(source))
    return merged
