"""Configuration persistante, en JSON pour ne dependre d'aucune bibliotheque."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from igarchive import paths

# Bornes de securite : en dessous, Instagram limite le compte tres vite.
MIN_SLEEP = 1.0
RECOMMENDED_SLEEP_MIN = 3.0
RECOMMENDED_SLEEP_MAX = 8.0


@dataclass
class Config:
    """Reglages de l'utilisateur. Tous modifiables depuis l'interface web."""

    username: str = ""
    archive_dir: str = field(default_factory=lambda: str(paths.default_archive_dir()))
    browser: str = "firefox"
    sleep_min: float = RECOMMENDED_SLEEP_MIN
    sleep_max: float = RECOMMENDED_SLEEP_MAX
    download_videos: bool = True
    download_comments: bool = False
    download_thumbnails: bool = True
    stop_after_known: int = 0        # 0 = parcourir toute la bibliotheque
    limit_per_run: int = 0           # 0 = pas de plafond
    webui_port: int = 8765
    language: str = "fr"

    # -- chemins derives -------------------------------------------------

    @property
    def archive(self) -> Path:
        return Path(self.archive_dir).expanduser()

    @property
    def media_dir(self) -> Path:
        return self.archive / "media"

    @property
    def metadata_dir(self) -> Path:
        return self.archive / "metadata"

    @property
    def state_dir(self) -> Path:
        return self.archive / "state"

    @property
    def ledger_file(self) -> Path:
        return self.state_dir / "ledger.json"

    @property
    def saved_dates_file(self) -> Path:
        return self.state_dir / "saved_dates.json"

    def session_file(self, username: str | None = None) -> Path:
        return paths.sessions_dir() / f"{username or self.username}.session"

    def ensure_dirs(self) -> None:
        for directory in (self.media_dir, self.metadata_dir, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # -- validation ------------------------------------------------------

    def problems(self) -> list[str]:
        """Liste lisible de ce qui empeche un archivage de demarrer."""
        issues: list[str] = []
        if not self.username.strip():
            issues.append("Aucun compte Instagram renseigne.")
        elif not self.session_file().exists():
            issues.append(f"Aucune session ouverte pour « {self.username} ».")
        if self.sleep_min < MIN_SLEEP:
            issues.append(f"La pause minimale ne peut pas descendre sous {MIN_SLEEP} s.")
        if self.sleep_max < self.sleep_min:
            issues.append("La pause maximale est inferieure a la pause minimale.")
        parent = self.archive.parent
        if not parent.exists():
            issues.append(f"Le dossier parent de destination n'existe pas : {parent}")
        return issues

    # -- persistance -----------------------------------------------------

    def save(self, path: Path | None = None) -> Path:
        target = path or paths.config_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(target)
        return target


def load(path: Path | None = None) -> Config:
    """Lit la configuration ; rend les valeurs par defaut si le fichier manque.

    Les cles inconnues sont ignorees, pour qu'un fichier ecrit par une version
    ulterieure n'empeche pas une version anterieure de demarrer.
    """
    target = path or paths.config_file()
    if not target.exists():
        return Config()
    try:
        raw: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return Config()
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in raw.items() if k in known})
