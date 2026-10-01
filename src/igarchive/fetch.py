"""Downloading saved content.

Always resumes where it stopped: a ledger (ledger.json) records what is done,
what failed and what has gone. Interrupting the program, losing the network or
being rate limited by Instagram never sends you back to the start.

The module knows nothing of terminals or interfaces: it reports progress
through a callback, which is what lets the command line and the web interface
share exactly the same code.
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
    AbortDownloadException,
    ConnectionException,
    InstaloaderException,
    LoginRequiredException,
    PrivateProfileNotFollowedException,
    QueryReturnedBadRequestException,
    QueryReturnedForbiddenException,
    QueryReturnedNotFoundException,
    TooManyRequestsException,
)

from igarchive import i18n
from igarchive import catalog
from igarchive.catalog import Record
from igarchive.config import Config

# Terminal errors: the item is gone, or no longer reachable.
UNAVAILABLE = (
    PrivateProfileNotFollowedException,
    QueryReturnedNotFoundException,
    QueryReturnedForbiddenException,
    QueryReturnedBadRequestException,
)

# AbortDownloadException descends from Exception, not from InstaloaderException,
# so catching the latter misses it entirely. It has to be named explicitly.
WALK_ERRORS = (AbortDownloadException, ConnectionException, InstaloaderException, OSError)

# Instagram signals a rate limit in several shapes; "feedback_required" is the
# one it returns to an account it has flagged for automated behaviour.
THROTTLE_MARKERS = ("feedback_required", "429", "too many requests",
                    "please wait a few minutes", "rate limit")


def is_throttled(error: BaseException) -> bool:
    """True when the failure is Instagram holding the account back."""
    if isinstance(error, TooManyRequestsException):
        return True
    text = str(error).lower()
    return any(marker in text for marker in THROTTLE_MARKERS)


def walk_saved(iterator: Any, progress: Progress) -> "Iterator[Post]":
    """Yield saved posts, turning a failure mid-walk into a readable stop.

    Paginating the saved library is itself a request, so it can be refused
    halfway through. Left unguarded it escaped as a raw exception string --
    the user saw instaloader's wording rather than what to do about it.
    """
    source = iter(iterator)
    while True:
        try:
            yield next(source)
        except StopIteration:
            return
        except WALK_ERRORS as exc:
            if is_throttled(exc):
                progress.rate_limited = True
                progress.error = i18n.t("rate_limited")
            else:
                progress.error = i18n.t("refused", error=exc)
            return


# A reel runs 90 s at most; beyond that Instagram calls it a video.
REEL_MAX_SECONDS = 91


@dataclass
class Progress:
    """A snapshot of a backup in progress, readable by any interface."""

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
    """A folder-name fragment that is safe on macOS as well as on Linux."""
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
    """Some fields trigger a request and can fail on their own."""
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
    """Walk the saved library and back up whatever is missing."""
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
        progress.error = i18n.t("session_expired_midrun")
        progress.finished = True
        on_progress(progress)
        return progress
    except WALK_ERRORS as exc:
        progress.phase = "erreur"
        progress.error = (i18n.t("rate_limited") if is_throttled(exc)
                          else i18n.t("refused", error=exc))
        progress.finished = True
        on_progress(progress)
        return progress

    progress.phase = "en cours"
    consecutive_known = 0

    for rank, post in enumerate(walk_saved(iterator, progress)):
        if cancel.is_set():
            progress.message = i18n.t("stopped_on_request")
            break

        progress.scanned = rank + 1
        code = post.shortcode
        entry = ledger.get(code, {})

        # Already archived: only the rank is refreshed, since it shifts every
        # time something new is saved. Nothing is downloaded again.
        if entry.get("status") == "ok":
            progress.skipped += 1
            consecutive_known += 1
            _refresh_rank(config, code, rank)
            if config.stop_after_known and consecutive_known >= config.stop_after_known:
                progress.message = i18n.t("known_streak", count=config.stop_after_known)
                break
            on_progress(progress)
            continue
        consecutive_known = 0

        if config.limit_per_run and (progress.done + progress.failed) >= config.limit_per_run:
            progress.message = i18n.t("limit_reached", limit=config.limit_per_run)
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

        except WALK_ERRORS as exc:
            if is_throttled(exc):
                # Instagram is limiting the account; stopping at once protects it.
                progress.rate_limited = True
                progress.error = i18n.t("rate_limited")
                break
            if isinstance(exc, UNAVAILABLE):
                raise
            ledger[code] = {"status": "failed", "rank": rank,
                            "reason": type(exc).__name__, "detail": str(exc)[:200],
                            "seen_at": datetime.now(timezone.utc).isoformat()}
            progress.failed += 1
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

        # A random pause: it is a steady rhythm that triggers rate limiting.
        delay = random.uniform(config.sleep_min, config.sleep_max)
        if cancel.wait(delay):
            progress.message = i18n.t("stopped_on_request")
            break

    write_ledger(config, ledger)
    progress.phase = "termine" if not progress.error else "erreur"
    progress.finished = True
    progress.current = ""
    on_progress(progress)
    return progress


def _refresh_rank(config: Config, shortcode: str, rank: int) -> None:
    """Update the save rank of a record already written."""
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
    """The state of the archive, for the interface and the command line."""
    ledger = read_ledger(config)
    counts: dict[str, int] = {}
    for entry in ledger.values():
        status = entry.get("status", "?")
        counts[status] = counts.get(status, 0) + 1
    saved_dates = read_saved_dates(config)
    # The ledger already carries the date of each retrieval; keeping another one
    # would only give it something to drift out of step with.
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
