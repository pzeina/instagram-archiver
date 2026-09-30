"""Telechargement des contenus enregistres.

Reprend toujours ou il s'est arrete : un registre (ledger.json) retient ce qui
est fait, ce qui a echoue et ce qui a disparu. Interrompre le programme, perdre
le reseau ou se faire limiter par Instagram ne fait jamais recommencer a zero.

Le module ne connait ni terminal ni interface : il signale son avancement par
un rappel de fonction, ce qui permet a la ligne de commande et a l'interface
web de partager exactement le meme code.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from instaloader import Instaloader, Post, Profile
from instaloader.exceptions import (
    ConnectionException,
    InstaloaderException,
    LoginRequiredException,
    PrivateProfileNotFollowedException,
    QueryReturnedBadRequestException,
    QueryReturnedForbiddenException,
    QueryReturnedNotFoundException,
    TooManyRequestsException,
)

from igarchive import catalog
from igarchive.catalog import Record
from igarchive.config import Config

# Erreurs definitives : le contenu n'existe plus ou n'est plus accessible.
UNAVAILABLE = (
    PrivateProfileNotFollowedException,
    QueryReturnedNotFoundException,
    QueryReturnedForbiddenException,
    QueryReturnedBadRequestException,
)

# Un reel dure au plus 90 s ; au-dela, Instagram parle de video.
REEL_MAX_SECONDS = 91


@dataclass
class Progress:
    """Etat instantane d'un archivage, lisible par n'importe quelle interface."""

    phase: str = "pret"
    message: str = ""
    done: int = 0
    failed: int = 0
    skipped: int = 0
    scanned: int = 0
    bytes_total: int = 0
    current: str = ""
    finished: bool = False
    error: str | None = None
    rate_limited: bool = False


ProgressCallback = Callable[[Progress], None]


def _noop(_: Progress) -> None:
    pass


# ---------------------------------------------------------------------------

def safe_component(text: str, limit: int = 40) -> str:
    """Fragment de nom de dossier sur, sur macOS comme sur Linux."""
    cleaned = re.sub(r"[^\w.\- ]+", "_", text, flags=re.UNICODE).strip(" ._-")
    return cleaned[:limit] or "inconnu"


def post_kind(post: Post) -> str:
    if post.typename == "GraphSidecar":
        return "carousel"
    if post.is_video:
        duration = quiet(post, "video_duration") or 0
        return "reel" if duration <= REEL_MAX_SECONDS else "video"
    return "image"


def quiet(post: Post, name: str, default: Any = None) -> Any:
    """Certains champs declenchent une requete et peuvent echouer isolement."""
    try:
        return getattr(post, name)
    except (InstaloaderException, KeyError, TypeError, AttributeError):
        return default


def read_ledger(config: Config) -> dict[str, dict]:
    path = config.ledger_file
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}


def write_ledger(config: Config, ledger: dict[str, dict]) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = config.ledger_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(config.ledger_file)


def read_saved_dates(config: Config) -> dict[str, dict]:
    path = config.saved_dates_file
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}


def write_saved_dates(config: Config, dates: dict[str, dict]) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = config.saved_dates_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(dates, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(config.saved_dates_file)


# ---------------------------------------------------------------------------

def configure_loader(loader: Instaloader, config: Config, directory: Path) -> None:
    loader.dirname_pattern = str(directory)
    loader.filename_pattern = "{date_utc:%Y-%m-%d_%H-%M-%S}_UTC"
    loader.download_pictures = True
    loader.download_videos = config.download_videos
    loader.download_video_thumbnails = config.download_thumbnails
    loader.download_comments = config.download_comments
    loader.download_geotags = False
    loader.save_metadata = True      # reponse brute d'Instagram, pour verification
    loader.compress_json = False
    loader.post_metadata_txt_pattern = "{caption}"
    loader.sanitize_paths = True


def build_record(post: Post, saved: dict | None, rank: int,
                 archive: Path, directory: Path, files: list[Path]) -> Record:
    location = quiet(post, "location")
    collections = (saved or {}).get("collections") or []
    return Record(
        shortcode=post.shortcode,
        url=f"https://www.instagram.com/p/{post.shortcode}/",
        kind=post_kind(post),
        typename=post.typename,
        author=quiet(post, "owner_username"),
        author_id=quiet(post, "owner_id"),
        posted_at_utc=post.date_utc.replace(tzinfo=timezone.utc).isoformat(),
        posted_at_local=post.date_local.isoformat(),
        saved_at=(saved or {}).get("saved_at"),
        saved_timestamp=(saved or {}).get("saved_timestamp"),
        saved_rank=rank,
        collection=(saved or {}).get("collection"),
        collections=list(collections),
        caption=post.caption or "",
        hashtags=sorted(quiet(post, "caption_hashtags", []) or []),
        mentions=sorted(quiet(post, "caption_mentions", []) or []),
        location=getattr(location, "name", None) if location else None,
        media_count=quiet(post, "mediacount", 1) or 1,
        is_video=post.is_video,
        video_duration_s=quiet(post, "video_duration"),
        video_views=quiet(post, "video_view_count"),
        likes=quiet(post, "likes"),
        comments=quiet(post, "comments"),
        is_sponsored=bool(quiet(post, "is_sponsored", False)),
        directory=str(directory.relative_to(archive)),
        files=sorted(f.relative_to(archive).as_posix() for f in files),
        bytes_total=sum(f.stat().st_size for f in files if f.exists()),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def run(config: Config, loader: Instaloader, *,
        on_progress: ProgressCallback = _noop,
        cancel: threading.Event | None = None,
        dry_run: bool = False) -> Progress:
    """Parcourt la bibliotheque « Enregistres » et archive ce qui manque."""
    cancel = cancel or threading.Event()
    progress = Progress(phase="demarrage", message="Ouverture de la bibliotheque...")
    on_progress(progress)

    config.ensure_dirs()
    ledger = read_ledger(config)
    saved_dates = read_saved_dates(config)

    try:
        profile = Profile.own_profile(loader.context)
        iterator = profile.get_saved_posts()
    except LoginRequiredException:
        progress.phase = "erreur"
        progress.error = ("La session a expire. Ouvre-la a nouveau depuis "
                          "l'onglet Compte.")
        progress.finished = True
        on_progress(progress)
        return progress
    except InstaloaderException as exc:
        progress.phase = "erreur"
        progress.error = f"Instagram a refuse la requete : {exc}"
        progress.finished = True
        on_progress(progress)
        return progress

    progress.phase = "en cours"
    consecutive_known = 0

    for rank, post in enumerate(iterator):
        if cancel.is_set():
            progress.message = "Arrete a la demande."
            break

        progress.scanned = rank + 1
        code = post.shortcode
        entry = ledger.get(code, {})

        # Deja archive : on rafraichit seulement le rang, qui bouge a chaque
        # nouvel enregistrement, sans retelecharger.
        if entry.get("status") == "ok":
            progress.skipped += 1
            consecutive_known += 1
            _refresh_rank(config, code, rank)
            if config.stop_after_known and consecutive_known >= config.stop_after_known:
                progress.message = (f"{config.stop_after_known} contenus deja connus "
                                    f"d'affilee : mise a jour terminee.")
                break
            on_progress(progress)
            continue
        consecutive_known = 0

        if config.limit_per_run and (progress.done + progress.failed) >= config.limit_per_run:
            progress.message = f"Plafond de {config.limit_per_run} atteint pour cette passe."
            break

        author = safe_component(quiet(post, "owner_username") or "inconnu")
        stamp = post.date_utc.strftime("%Y-%m-%d")
        directory = config.media_dir / post.date_utc.strftime("%Y") / f"{stamp}_{author}_{code}"
        progress.current = f"@{author} — {code}"
        on_progress(progress)

        if dry_run:
            progress.done += 1
            continue

        try:
            directory.mkdir(parents=True, exist_ok=True)
            configure_loader(loader, config, directory)
            loader.download_post(post, target=code)

            files = sorted(p for p in directory.iterdir() if p.is_file())
            record = build_record(post, saved_dates.get(code), rank,
                                  config.archive, directory, files)
            catalog.write_record(config.metadata_dir, record)
            ledger[code] = {
                "status": "ok", "rank": rank, "fetched_at": record.fetched_at,
                "files": len(files), "bytes": record.bytes_total,
            }
            progress.done += 1
            progress.bytes_total += record.bytes_total

        except TooManyRequestsException:
            # Instagram limite le compte : s'arreter tout de suite protege le compte.
            progress.rate_limited = True
            progress.error = ("Instagram limite les requetes. L'archivage s'est arrete "
                              "pour proteger le compte : relance dans une a deux heures, "
                              "il reprendra ou il en est.")
            break
        except UNAVAILABLE as exc:
            ledger[code] = {
                "status": "unavailable", "rank": rank, "reason": type(exc).__name__,
                "detail": str(exc)[:200],
                "seen_at": datetime.now(timezone.utc).isoformat(),
            }
            progress.failed += 1
        except (ConnectionException, InstaloaderException, OSError) as exc:
            ledger[code] = {
                "status": "failed", "rank": rank, "reason": type(exc).__name__,
                "detail": str(exc)[:200],
                "seen_at": datetime.now(timezone.utc).isoformat(),
            }
            progress.failed += 1

        on_progress(progress)
        if progress.done and progress.done % 10 == 0:
            write_ledger(config, ledger)

        # Pause aleatoire : un rythme regulier est ce qui declenche les limitations.
        delay = random.uniform(config.sleep_min, config.sleep_max)
        if cancel.wait(delay):
            progress.message = "Arrete a la demande."
            break

    write_ledger(config, ledger)
    progress.phase = "termine" if not progress.error else "erreur"
    progress.finished = True
    progress.current = ""
    on_progress(progress)
    return progress


def _refresh_rank(config: Config, shortcode: str, rank: int) -> None:
    """Met a jour le rang d'enregistrement d'une fiche deja ecrite."""
    path = config.metadata_dir / f"{shortcode}.json"
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return
    if raw.get("saved_rank") != rank:
        raw["saved_rank"] = rank
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def summary(config: Config) -> dict[str, Any]:
    """Etat de l'archive, pour l'interface et la ligne de commande."""
    ledger = read_ledger(config)
    counts: dict[str, int] = {}
    for entry in ledger.values():
        status = entry.get("status", "?")
        counts[status] = counts.get(status, 0) + 1
    saved_dates = read_saved_dates(config)
    # Le registre porte deja la date de chaque recuperation : inutile d'en tenir
    # une de plus, qui pourrait se desynchroniser.
    stamps = [e["fetched_at"] for e in ledger.values() if e.get("fetched_at")]
    return {
        "last_archived": max(stamps) if stamps else None,
        "archived": counts.get("ok", 0),
        "unavailable": counts.get("unavailable", 0),
        "failed": counts.get("failed", 0),
        "bytes": sum(e.get("bytes", 0) for e in ledger.values()),
        "known_dates": len(saved_dates),
        "pending_from_export": len([c for c in saved_dates if c not in ledger]),
        "archive_dir": str(config.archive),
        "exists": config.archive.exists(),
    }
