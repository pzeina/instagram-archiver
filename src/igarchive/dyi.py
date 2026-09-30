"""Lecture de l'export officiel Instagram (« Download Your Information »).

C'est la seule source qui donne la *date d'enregistrement* d'un contenu :
l'interface d'Instagram n'expose que l'ordre, jamais la date. L'export, lui,
ne contient pas les videos enregistrees. Les deux se completent, d'ou ce module.

Le format bouge d'une version a l'autre et depend de la langue du compte, donc
rien n'est suppose ici : on descend recursivement dans le JSON et on retient
toute entree qui porte un lien vers un contenu Instagram.
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SAVED_FILENAMES = ("saved_posts.json", "saved_collections.json")
SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")


class ExportError(RuntimeError):
    """Export illisible ou ne contenant pas les fichiers attendus."""


def shortcode_from_url(url: str) -> str | None:
    match = SHORTCODE_RE.search(url or "")
    return match.group(1) if match else None


def fix_mojibake(text: str) -> str:
    """Les exports encodent l'UTF-8 en latin-1 : « Ã© » la ou il faut « é »."""
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _iter_files(source: Path) -> Iterator[tuple[str, bytes]]:
    """Rend (nom, contenu) pour les fichiers voulus, depuis un .zip ou un dossier."""
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
        raise ExportError(
            f"Ni un fichier .zip ni un dossier : {source}\n"
            "Indique le .zip recu par mail, ou le dossier obtenu en le decompressant."
        )


def _walk_entries(node: Any) -> Iterator[dict]:
    """Descend dans une structure JSON de forme inconnue jusqu'aux entrees utiles."""
    if isinstance(node, dict):
        if isinstance(node.get("string_map_data"), dict):
            yield node
        for value in node.values():
            yield from _walk_entries(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_entries(value)


def _merge(previous: dict, incoming: dict) -> dict:
    """Fusionne deux vues d'un meme contenu.

    Un post figure a la fois dans saved_posts.json et dans chaque collection qui
    le contient : ecraser ferait perdre soit la date, soit la collection. On garde
    la date la plus ancienne (le premier enregistrement) et l'union des collections.
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
    """Rend {identifiant du contenu: fiche de date} pour tout l'export."""
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
            # Le nom du champ depend de la langue : « Saved on », « Enregistré le »...
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
        raise ExportError(
            f"Aucun fichier {' ni '.join(SAVED_FILENAMES)} dans {source}.\n"
            "Verifie d'avoir demande l'export au format JSON, en cochant « Elements "
            "enregistres »."
        )
    return found


def merge_into(existing: dict[str, dict], parsed: dict[str, dict]) -> tuple[dict, int]:
    """Ajoute un export a ce qui est deja connu. Rend (total, nouveaux)."""
    added = 0
    for code, record in parsed.items():
        if code in existing:
            existing[code] = _merge(existing[code], record)
        else:
            existing[code] = record
            added += 1
    return existing, added
