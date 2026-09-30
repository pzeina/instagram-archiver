"""Emplacements de fichiers, resolus de la meme facon sur macOS et Linux.

On suit la convention XDG sur les deux systemes plutot que
"~/Library/Application Support" sur macOS : un seul chemin a documenter,
un seul a sauvegarder, et le meme dans la documentation quel que soit l'OS.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = "igarchive"


def config_dir() -> Path:
    """Repertoire de configuration (respecte XDG_CONFIG_HOME s'il est defini)."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / APP


def config_file() -> Path:
    return config_dir() / "config.json"


def sessions_dir() -> Path:
    """Jetons de session : equivalents a un mot de passe, donc hors de l'archive."""
    return config_dir() / "sessions"


def default_archive_dir() -> Path:
    return Path.home() / "InstagramArchive"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def firefox_profile_roots() -> list[Path]:
    """Tous les emplacements ou Firefox range ses profils, selon l'OS et le paquet."""
    home = Path.home()
    if is_macos():
        candidates = [home / "Library/Application Support/Firefox/Profiles"]
    else:
        candidates = [
            home / ".mozilla/firefox",
            home / "snap/firefox/common/.mozilla/firefox",          # Ubuntu snap
            home / ".var/app/org.mozilla.firefox/.mozilla/firefox",  # Flatpak
        ]
    return [p for p in candidates if p.is_dir()]


def open_in_browser(target: str) -> bool:
    """Ouvre une URL ou un fichier dans l'application par defaut. Vrai si lance."""
    if is_macos():
        opener = ["open"]
    else:
        exe = shutil.which("xdg-open") or shutil.which("gio")
        if not exe:
            return False
        opener = [exe, "open"] if exe.endswith("gio") else [exe]
    try:
        subprocess.Popen([*opener, target],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def human_bytes(size: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(size) < 1024 or unit == "To":
            return f"{size:.1f} {unit}" if unit != "o" else f"{size:.0f} o"
        size /= 1024
    return f"{size:.1f} To"
