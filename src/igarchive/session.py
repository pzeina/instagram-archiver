"""Opening and keeping an Instagram session.

Four routes, from the simplest to the most universal:

1. Firefox cookies        -- read straight from the profile, no dependency
2. another browser's cookies -- through browser_cookie3 (optional package)
3. pasting the sessionid cookie -- works anywhere, even with no local browser
4. username and password  -- handled by instaloader, nothing is kept

No password is ever written to disk. Only the session token is, with mode 600,
and it is treated as the equivalent of a password.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from instaloader import Instaloader
from instaloader.exceptions import InstaloaderException

from igarchive import i18n, paths

# Browsers delegated to browser_cookie3, which decrypts the macOS Keychain or
# the GNOME/KDE wallet on Linux.
DELEGATED_BROWSERS = ("chrome", "chromium", "brave", "edge", "opera", "safari")
SUPPORTED_BROWSERS = ("firefox", *DELEGATED_BROWSERS)


class SessionError(RuntimeError):
    """Sign-in failure, carrying a message meant for the user."""


# ---------------------------------------------------------------------------
# reading cookies
# ---------------------------------------------------------------------------

def firefox_cookies() -> dict[str, str]:
    """instagram.com cookies from the most recently used Firefox profile."""
    roots = paths.firefox_profile_roots()
    if not roots:
        raise SessionError(
i18n.t("no_firefox_profile"))
    databases = [db for root in roots for db in root.glob("*/cookies.sqlite")]
    if not databases:
        raise SessionError(i18n.t("firefox_no_cookies"))
    database = max(databases, key=lambda p: p.stat().st_mtime)

    # Firefox keeps the file locked while it runs, so a copy is read instead.
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "cookies.sqlite"
        shutil.copy2(database, copy)
        connection = sqlite3.connect(f"file:{copy}?immutable=1", uri=True)
        try:
            rows = connection.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE '%instagram.com'"
            ).fetchall()
        finally:
            connection.close()
    return {name: value for name, value in rows}


def delegated_cookies(browser: str) -> dict[str, str]:
    """Cookies of a browser whose store is encrypted, via browser_cookie3."""
    try:
        import browser_cookie3
    except ImportError as exc:
        raise SessionError(
i18n.t("needs_browser_cookie3", browser=browser)) from exc
    loader = getattr(browser_cookie3, browser, None)
    if loader is None:
        raise SessionError(i18n.t("unknown_browser", browser=browser))
    try:
        jar = loader(domain_name="instagram.com")
    except Exception as exc:  # browser_cookie3 raises a wide variety of errors
        raise SessionError(i18n.t("cookie_read_failed", browser=browser, error=exc)) from exc
    return {cookie.name: cookie.value for cookie in jar}


def browser_cookies(browser: str) -> dict[str, str]:
    browser = browser.lower().strip()
    if browser == "firefox":
        return firefox_cookies()
    if browser in DELEGATED_BROWSERS:
        return delegated_cookies(browser)
    raise SessionError(i18n.t("unsupported_browser", browser=browser,
                              choices=", ".join(SUPPORTED_BROWSERS)))


def parse_cookie_blob(blob: str) -> dict[str, str]:
    """Accept either the bare sessionid value or a whole Cookie header.

    Pasting the full header from the developer tools is the most reliable route
    when automatic reading fails: a hardened Linux, an unusual browser, a remote
    machine.
    """
    blob = blob.strip().strip(";")
    if not blob:
        raise SessionError(i18n.t("no_cookie_given"))
    if "=" not in blob:
        return {"sessionid": blob}          # the sessionid value on its own
    cookies: dict[str, str] = {}
    for part in blob.split(";"):
        if "=" in part:
            name, _, value = part.partition("=")
            cookies[name.strip()] = value.strip()
    return cookies


# ---------------------------------------------------------------------------
# opening, verifying, keeping
# ---------------------------------------------------------------------------

def _client() -> Instaloader:
    """The only way this module builds an instaloader client.

    Its error() method prints to standard error regardless of the "quiet"
    option, so an account rate limited by Instagram filled the terminal with the
    same message over and over. Replacing the method keeps every message in
    error_log -- where _probe reads them to tell an expired session from a check
    that could not be made -- without any of them reaching the terminal.

    Going through one factory is what stops a future call site from quietly
    reintroducing the noise.
    """
    loader = Instaloader(quiet=True)
    context = loader.context

    def collect(msg, repeat_at_end=True):  # noqa: ARG001 -- signature is imposed
        context.error_log.append(msg)

    context.error = collect
    return loader


def _persist(loader: Instaloader, username: str) -> Path:
    directory = paths.sessions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    target = directory / f"{username}.session"
    loader.save_session_to_file(str(target))
    target.chmod(0o600)
    invalidate(username)
    return target


def _verify(loader: Instaloader, expected: str) -> str:
    """Ask Instagram which account the session actually opens."""
    try:
        actual = loader.test_login()
    except InstaloaderException as exc:
        raise SessionError(i18n.t("session_refused", error=exc)) from exc
    if not actual:
        raise SessionError(
i18n.t("session_invalid"))
    if expected and actual.lower() != expected.lower():
        # Trust Instagram over whatever was typed in.
        return actual
    return actual


def open_from_cookies(cookies: dict[str, str], username: str = "") -> tuple[str, Path]:
    """Open a session from cookies already in hand. Returns (account, file)."""
    if "sessionid" not in cookies:
        raise SessionError(
i18n.t("no_sessionid"))
    loader = _client()
    loader.load_session(username or "unknown", cookies)
    actual = _verify(loader, username)
    loader.context.username = actual
    return actual, _persist(loader, actual)


def open_from_browser(browser: str, username: str = "") -> tuple[str, Path]:
    return open_from_cookies(browser_cookies(browser), username)


def open_from_blob(blob: str, username: str = "") -> tuple[str, Path]:
    return open_from_cookies(parse_cookie_blob(blob), username)


def open_interactive(username: str, password: str | None = None) -> tuple[str, Path]:
    """Sign in with credentials. Without a password, instaloader asks at the terminal.

    The password is neither kept nor logged; only the resulting token is.
    """
    loader = _client()
    try:
        if password:
            loader.login(username, password)
        else:
            loader.interactive_login(username)
    except InstaloaderException as exc:
        raise SessionError(i18n.t("login_refused", error=exc)) from exc
    actual = _verify(loader, username)
    return actual, _persist(loader, actual)


def open_auto(username: str = "") -> tuple[str, Path]:
    """Open a session without asking anything: every browser is tried in turn.

    Picking a browser from a list teaches nobody anything. Firefox is tried
    first, then the others if they can be read, and only the outcome is
    reported. If all fail, the message gathers what each one answered.
    """
    troubles: list[str] = []
    for browser in SUPPORTED_BROWSERS:
        try:
            cookies = browser_cookies(browser)
        except SessionError as exc:
            troubles.append(f"{browser} : {str(exc).splitlines()[0]}")
            continue
        if "sessionid" not in cookies:
            troubles.append(f"{browser}: no Instagram session open")
            continue
        try:
            return open_from_cookies(cookies, username)
        except SessionError as exc:
            troubles.append(f"{browser} : {str(exc).splitlines()[0]}")

    raise SessionError(i18n.t("no_browser_session") + "\n\n" + "\n".join(troubles))


def load(username: str) -> Instaloader:
    """Reload a session already opened. Raises SessionError if it is missing."""
    target = paths.sessions_dir() / f"{username}.session"
    if not target.exists():
        raise SessionError(i18n.t("no_saved_session", account=username))
    loader = _client()
    try:
        loader.load_session_from_file(username, str(target))
    except (OSError, InstaloaderException) as exc:
        raise SessionError(i18n.t("session_unreadable", error=exc)) from exc
    return loader


# Checking a session is a network request. Without a guard, showing a status
# indicator cost thousands of requests an hour -- precisely the rate that gets an
# account limited. The interface now only checks when the user asks it to.
STATUS_TTL = 120.0
# When Instagram refuses to answer -- "feedback_required" -- pressing on can only
# sustain the limit. Checks are spaced much further apart until it lifts.
UNREACHABLE_TTL = 900.0
_status_cache: "dict[str, tuple[float, dict]]" = {}
_status_lock = threading.Lock()


def _probe(username: str) -> dict:
    """The real state of a session, telling "expired" apart from "unknown".

    A failed request does not prove a session has expired: the network may be
    down, or Instagram may be limiting the account. Confusing the two showed
    "session expired" to a perfectly connected user.
    """
    target = paths.sessions_dir() / f"{username}.session"
    if not username or not target.exists():
        return {"exists": False, "valid": False, "account": None,
                "reachable": True, "error": None}
    try:
        loader = load(username)
    except (SessionError, OSError) as exc:
        return {"exists": True, "valid": False, "account": None,
                "reachable": False, "error": str(exc)[:200]}

    # test_login() returns None in two opposite cases: the session really is
    # signed out, or the question could not be asked at all (no network, or
    # "feedback_required", Instagram's answer to an account it is limiting).
    # It only logs an error in the second case, and that is the sole difference
    # between them. Confusing the two showed "session expired" to a perfectly
    # connected user whose downloads were going through.
    log = loader.context.error_log
    before = len(log)
    try:
        actual = loader.test_login()
    except (InstaloaderException, OSError) as exc:
        return {"exists": True, "valid": False, "account": None,
                "reachable": False, "error": str(exc)[:200]}

    if actual:
        return {"exists": True, "valid": True, "account": actual,
                "reachable": True, "error": None}
    if len(log) > before:
        return {"exists": True, "valid": False, "account": None,
                "reachable": False, "error": str(log[-1])[:200]}
    return {"exists": True, "valid": False, "account": None,
            "reachable": True, "error": None}


def status(username: str, *, force: bool = False, recover: bool = True) -> dict:
    """Session state, checked at most once every STATUS_TTL seconds.

    When the stored session really has expired, the browser is checked for a
    newer one: signing in again at instagram.com should be enough, without
    having to come back and press a button.
    """
    if not username:
        return {"exists": False, "valid": False, "account": None,
                "reachable": True, "error": None}

    now = time.monotonic()
    with _status_lock:
        cached = _status_cache.get(username)
        if cached and not force:
            age = now - cached[0]
            ttl = STATUS_TTL if cached[1].get("reachable", True) else UNREACHABLE_TTL
            if age < ttl:
                return cached[1]

    result = _probe(username)

    if recover and result["exists"] and result["reachable"] and not result["valid"]:
        try:
            account, _ = open_auto(username)
            result = _probe(account)
            result["recovered"] = account
        except SessionError:
            pass   # nothing new in the browser: the session stays expired

    with _status_lock:
        _status_cache[username] = (time.monotonic(), result)
    return result


def connect(username: str = "") -> dict:
    """Check the stored session, and failing that take one from the browser.

    Called only when the user presses "Connect the account": the interface no
    longer checks anything on its own. Two requests at most, at a moment the
    user chose, rather than a background poll nobody asked for -- which is what
    ended up getting the account limited.
    """
    checked = _probe(username) if username else None
    if checked and checked["valid"]:
        invalidate(username)
        return {**checked, "checked": True, "usable": True,
                "message": i18n.t("connected_to", account=checked["account"])}

    # Instagram would not answer. That says nothing about the stored session --
    # the endpoint being throttled is the very one we ask. Blocking the backup on
    # an unanswerable question strands a user whose session works perfectly; the
    # backup itself settles the matter in seconds.
    if checked and checked["exists"] and not checked["reachable"]:
        invalidate(username)
        return {**checked, "checked": True, "usable": True,
                "message": i18n.t("unverified_usable")}

    try:
        account, _ = open_auto(username)
    except SessionError as exc:
        return {"exists": bool(checked and checked["exists"]), "valid": False,
                "usable": False, "account": None, "reachable": True,
                "checked": True, "error": str(exc),
                "message": str(exc).splitlines()[0]}

    result = _probe(account)
    invalidate(account)
    # A session just taken from the browser is usable even when Instagram would
    # not confirm it: the cookies are fresh by construction.
    return {**result, "checked": True, "usable": True,
            "message": (i18n.t("connected_to", account=account) if result["valid"]
                        else i18n.t("unverified_usable"))}


def invalidate(username: str | None = None) -> None:
    """Force the next read to check again -- after any action on the session."""
    with _status_lock:
        if username:
            _status_cache.pop(username, None)
        else:
            _status_cache.clear()


def forget(username: str) -> bool:
    target = paths.sessions_dir() / f"{username}.session"
    if target.exists():
        target.unlink()
        invalidate(username)
        return True
    return False


def list_sessions() -> list[str]:
    directory = paths.sessions_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.session"))
