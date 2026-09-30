"""Ouverture et conservation d'une session Instagram.

Quatre voies, de la plus simple a la plus universelle :

1. cookies de Firefox        -- lecture directe du profil, aucune dependance
2. cookies d'un autre navigateur -- via browser_cookie3 (paquet optionnel)
3. collage du cookie sessionid   -- fonctionne partout, y compris sans navigateur local
4. identifiant / mot de passe    -- gere par instaloader, rien n'est conserve

Aucun mot de passe n'est jamais ecrit sur le disque. Seul le jeton de session
l'est, en droits 600, et il est traite comme un equivalent de mot de passe.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

from instaloader import Instaloader
from instaloader.exceptions import InstaloaderException

from igarchive import paths

# Navigateurs delegues a browser_cookie3 (dechiffrement Trousseau macOS /
# portefeuille GNOME-KDE sous Linux).
DELEGATED_BROWSERS = ("chrome", "chromium", "brave", "edge", "opera", "safari")
SUPPORTED_BROWSERS = ("firefox", *DELEGATED_BROWSERS)


class SessionError(RuntimeError):
    """Echec d'ouverture de session, avec un message destine a l'utilisateur."""


# ---------------------------------------------------------------------------
# lecture des cookies
# ---------------------------------------------------------------------------

def firefox_cookies() -> dict[str, str]:
    """Cookies instagram.com du profil Firefox le plus recemment utilise."""
    roots = paths.firefox_profile_roots()
    if not roots:
        raise SessionError(
            "Aucun profil Firefox trouve. Installe Firefox et connecte-toi a "
            "instagram.com, ou choisis une autre methode."
        )
    databases = [db for root in roots for db in root.glob("*/cookies.sqlite")]
    if not databases:
        raise SessionError("Profil Firefox trouve, mais aucun fichier cookies.sqlite.")
    database = max(databases, key=lambda p: p.stat().st_mtime)

    # Firefox garde le fichier verrouille pendant qu'il tourne : on lit une copie.
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
    """Cookies d'un navigateur dont la base est chiffree, via browser_cookie3."""
    try:
        import browser_cookie3
    except ImportError as exc:
        raise SessionError(
            f"Lire les cookies de {browser} demande le paquet browser_cookie3 :\n"
            f"    pip install browser-cookie3\n"
            f"Sinon, utilise Firefox ou colle ton cookie sessionid."
        ) from exc
    loader = getattr(browser_cookie3, browser, None)
    if loader is None:
        raise SessionError(f"Navigateur non reconnu : {browser}")
    try:
        jar = loader(domain_name="instagram.com")
    except Exception as exc:  # browser_cookie3 leve des exceptions tres variees
        raise SessionError(f"Lecture des cookies de {browser} impossible : {exc}") from exc
    return {cookie.name: cookie.value for cookie in jar}


def browser_cookies(browser: str) -> dict[str, str]:
    browser = browser.lower().strip()
    if browser == "firefox":
        return firefox_cookies()
    if browser in DELEGATED_BROWSERS:
        return delegated_cookies(browser)
    raise SessionError(
        f"Navigateur non pris en charge : {browser}. "
        f"Choix possibles : {', '.join(SUPPORTED_BROWSERS)}."
    )


def parse_cookie_blob(blob: str) -> dict[str, str]:
    """Accepte soit la valeur brute du sessionid, soit un en-tete Cookie entier.

    Coller l'en-tete complet depuis les outils de developpement est le moyen le
    plus fiable quand la lecture automatique echoue (Linux durci, navigateur
    exotique, machine distante).
    """
    blob = blob.strip().strip(";")
    if not blob:
        raise SessionError("Aucun cookie fourni.")
    if "=" not in blob:
        return {"sessionid": blob}          # valeur seule du sessionid
    cookies: dict[str, str] = {}
    for part in blob.split(";"):
        if "=" in part:
            name, _, value = part.partition("=")
            cookies[name.strip()] = value.strip()
    return cookies


# ---------------------------------------------------------------------------
# ouverture, verification, conservation
# ---------------------------------------------------------------------------

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
    return target


def _verify(loader: Instaloader, expected: str) -> str:
    """Interroge Instagram pour savoir quel compte la session ouvre reellement."""
    try:
        actual = loader.test_login()
    except InstaloaderException as exc:
        raise SessionError(f"Instagram a refuse la session : {exc}") from exc
    if not actual:
        raise SessionError(
            "Session invalide : Instagram ne reconnait aucun compte connecte.\n"
            "Connecte-toi a instagram.com dans ton navigateur, puis recommence."
        )
    if expected and actual.lower() != expected.lower():
        # On fait confiance a Instagram plutot qu'a ce qui a ete saisi.
        return actual
    return actual


def open_from_cookies(cookies: dict[str, str], username: str = "") -> tuple[str, Path]:
    """Ouvre une session a partir de cookies deja obtenus. Rend (compte, fichier)."""
    if "sessionid" not in cookies:
        raise SessionError(
            "Aucun cookie « sessionid » pour instagram.com.\n"
            "Connecte-toi a instagram.com dans ce navigateur, puis recommence."
        )
    loader = Instaloader(quiet=True)
    loader.load_session(username or "unknown", cookies)
    actual = _verify(loader, username)
    loader.context.username = actual
    return actual, _persist(loader, actual)


def open_from_browser(browser: str, username: str = "") -> tuple[str, Path]:
    return open_from_cookies(browser_cookies(browser), username)


def open_from_blob(blob: str, username: str = "") -> tuple[str, Path]:
    return open_from_cookies(parse_cookie_blob(blob), username)


def open_interactive(username: str, password: str | None = None) -> tuple[str, Path]:
    """Connexion par identifiant. Sans mot de passe, instaloader le demande au terminal.

    Le mot de passe n'est ni conserve ni journalise ; seul le jeton resultant l'est.
    """
    loader = Instaloader(quiet=True)
    try:
        if password:
            loader.login(username, password)
        else:
            loader.interactive_login(username)
    except InstaloaderException as exc:
        raise SessionError(f"Connexion refusee : {exc}") from exc
    actual = _verify(loader, username)
    return actual, _persist(loader, actual)


def load(username: str) -> Instaloader:
    """Recharge une session deja ouverte. Leve SessionError si elle manque."""
    target = paths.sessions_dir() / f"{username}.session"
    if not target.exists():
        raise SessionError(f"Aucune session enregistree pour « {username} ».")
    loader = Instaloader(quiet=True)
    try:
        loader.load_session_from_file(username, str(target))
    except (OSError, InstaloaderException) as exc:
        raise SessionError(f"Session illisible ({exc}). Ouvre-la a nouveau.") from exc
    return loader


def status(username: str) -> dict:
    """Etat de la session, pour l'interface : presente ? valide ? quel compte ?"""
    target = paths.sessions_dir() / f"{username}.session" if username else None
    if not username or not target or not target.exists():
        return {"exists": False, "valid": False, "account": None}
    try:
        loader = load(username)
        actual = loader.test_login()
    except (SessionError, InstaloaderException, OSError) as exc:
        return {"exists": True, "valid": False, "account": None, "error": str(exc)[:200]}
    return {"exists": True, "valid": bool(actual), "account": actual}


def forget(username: str) -> bool:
    target = paths.sessions_dir() / f"{username}.session"
    if target.exists():
        target.unlink()
        return True
    return False


def list_sessions() -> list[str]:
    directory = paths.sessions_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.session"))
