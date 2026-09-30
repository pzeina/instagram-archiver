"""Interface de configuration, servie localement par la bibliotheque standard.

Choix de conception : plutot qu'une bibliotheque graphique (Tk, Qt, GTK), qui
impose un paquet systeme different sur chaque distribution et n'est pas toujours
presente dans les installations Python de macOS, l'interface est une page servie
sur 127.0.0.1 et ouverte dans le navigateur deja installe. Rien a compiler,
rendu identique sur macOS et Linux, aucune dependance supplementaire.

Securite : le serveur n'ecoute que sur l'interface locale, et chaque appel a
l'API doit porter un jeton tire au hasard au demarrage. Sans ce jeton, une page
web ouverte par ailleurs dans le meme navigateur ne peut pas piloter l'outil.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import threading
from dataclasses import asdict
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from igarchive import __version__, catalog, config as config_module, dyi, fetch, paths, session
from igarchive.config import Config
from igarchive.jobs import JobRunner

ASSETS = Path(__file__).parent / "assets"
MAX_BODY = 1 << 20          # 1 Mio : large pour un collage de cookies, borne contre l'abus
MAX_UPLOAD = 1 << 30        # 1 Gio : un export Instagram complet tient largement dedans
UPLOAD_CHUNK = 1 << 20


class AppState:
    """Ce que le serveur partage entre toutes les requetes."""

    def __init__(self) -> None:
        self.config: Config = config_module.load()
        self.runner = JobRunner()
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()

    # -- vue d'ensemble --------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "version": __version__,
            "platform": "macOS" if paths.is_macos() else "Linux",
            "config": asdict(cfg),
            "config_file": str(paths.config_file()),
            "browsers": list(session.SUPPORTED_BROWSERS),
            "sessions": session.list_sessions(),
            "session": session.status(cfg.username) if cfg.username else
                       {"exists": False, "valid": False, "account": None},
            "summary": {**fetch.summary(cfg), **_collection_summary(cfg)},
            "problems": cfg.problems(),
            "job": self.runner.state(),
        }


def _collection_summary(cfg: Config) -> dict[str, Any]:
    """Nombre de contenus par collection, lu sans rien reconstruire."""
    counts: dict[str, int] = {}
    for record in catalog.load_records(cfg.metadata_dir):
        for name in catalog.record_collections(record):
            counts[name] = counts.get(name, 0) + 1
    real = {k: v for k, v in counts.items() if k != catalog.UNSORTED_NAME}
    return {"collections": dict(sorted(real.items(), key=lambda kv: (-kv[1], kv[0]))),
            "uncategorised": counts.get(catalog.UNSORTED_NAME, 0)}


# ---------------------------------------------------------------------------
# actions de l'API
# ---------------------------------------------------------------------------

def action_save_config(state: AppState, body: dict) -> dict:
    current = asdict(state.config)
    known = set(current)
    incoming = {k: v for k, v in body.items() if k in known}

    # Les nombres arrivent en texte depuis un formulaire : on convertit sans
    # laisser une saisie vide effacer un reglage valide.
    for key in ("sleep_min", "sleep_max"):
        if key in incoming:
            try:
                incoming[key] = float(incoming[key])
            except (TypeError, ValueError):
                del incoming[key]
    for key in ("stop_after_known", "limit_per_run", "webui_port"):
        if key in incoming:
            try:
                incoming[key] = int(incoming[key])
            except (TypeError, ValueError):
                del incoming[key]
    for key in ("download_videos", "download_comments", "download_thumbnails"):
        if key in incoming:
            incoming[key] = bool(incoming[key])
    if "archive_dir" in incoming:
        incoming["archive_dir"] = str(Path(str(incoming["archive_dir"])).expanduser())

    merged = {**current, **incoming}
    new_config = Config(**merged)
    new_config.save()
    state.config = new_config
    return {"ok": True, "config": asdict(new_config), "problems": new_config.problems()}


def action_session_browser(state: AppState, body: dict) -> dict:
    browser = str(body.get("browser") or state.config.browser)
    username = str(body.get("username") or state.config.username)
    account, path = session.open_from_browser(browser, username)
    state.config.username = account
    state.config.browser = browser
    state.config.save()
    return {"ok": True, "account": account, "file": str(path),
            "message": f"Session ouverte pour « {account} »."}


def action_session_cookie(state: AppState, body: dict) -> dict:
    blob = str(body.get("cookie") or "")
    username = str(body.get("username") or state.config.username)
    account, path = session.open_from_blob(blob, username)
    state.config.username = account
    state.config.save()
    return {"ok": True, "account": account, "file": str(path),
            "message": f"Session ouverte pour « {account} »."}


def action_session_password(state: AppState, body: dict) -> dict:
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username or not password:
        raise session.SessionError("Identifiant et mot de passe sont tous deux requis.")
    account, path = session.open_interactive(username, password)
    state.config.username = account
    state.config.save()
    return {"ok": True, "account": account, "file": str(path),
            "message": f"Session ouverte pour « {account} »."}


def action_session_forget(state: AppState, body: dict) -> dict:
    username = str(body.get("username") or state.config.username)
    removed = session.forget(username)
    return {"ok": True, "removed": removed,
            "message": "Session oubliee." if removed else "Aucune session a oublier."}


def action_dyi(state: AppState, body: dict) -> dict:
    raw = str(body.get("path") or "").strip().strip("'\"")
    if not raw:
        raise ValueError("Indique le chemin du fichier .zip recu d'Instagram.")
    return ingest_export(state, Path(raw).expanduser())


def ingest_exports(state: AppState, sources: list[Path]) -> dict:
    """Lit un ou plusieurs exports et fusionne dates et collections.

    Reconstruit le catalogue dans la foulee : les fiches deja ecrites gagnent
    leur date et leur collection sans qu'aucun media soit retelecharge.
    """
    parsed = dyi.parse_all(sources)
    merged, added = dyi.merge_into(fetch.read_saved_dates(state.config), parsed)
    fetch.write_saved_dates(state.config, merged)

    cfg = state.config
    cfg.ensure_dirs()
    built = catalog.build(cfg.archive, cfg.metadata_dir, merged)
    names = sorted(built.get("collections", {}))
    detail = f" {len(names)} collections : {', '.join(names)}." if names else ""
    return {"ok": True, "total": len(merged), "added": added,
            "collections": built.get("collections", {}),
            "dated": built["dated"],
            "message": (f"{len(merged)} contenus dates, dont {added} nouveaux.{detail} "
                        f"Catalogue mis a jour : {built['dated']} fiches datees.")}


def ingest_export(state: AppState, source: Path) -> dict:
    return ingest_exports(state, [source])


def action_dyi_auto(state: AppState, body: dict) -> dict:
    """Cherche l'export dans les telechargements et sur le bureau."""
    sources = dyi.find_exports()
    if not sources:
        raise ValueError(
            "Aucun export Instagram trouve dans les telechargements ni sur le bureau.\n"
            "Demande-le sur accountscenter.instagram.com (format JSON, « Elements "
            "enregistres »), puis reessaie — ou depose le fichier ci-dessus."
        )
    result = ingest_exports(state, sources)
    result["sources"] = [str(s) for s in sources]
    result["message"] = (f"Lu depuis {', '.join(s.name for s in sources)}. "
                         + result["message"])
    return result


def action_catalog(state: AppState, body: dict) -> dict:
    cfg = state.config
    cfg.ensure_dirs()
    result = catalog.build(cfg.archive, cfg.metadata_dir, fetch.read_saved_dates(cfg))
    return {"ok": True, **result,
            "message": f"Catalogue reconstruit : {result['count']} contenus."}


def action_fetch_start(state: AppState, body: dict) -> dict:
    cfg = state.config
    problems = cfg.problems()
    if problems:
        raise ValueError(" ".join(problems))
    dry_run = bool(body.get("dry_run"))
    loader = session.load(cfg.username)
    runner = state.runner

    def work(cancel: threading.Event, report: Callable) -> None:
        last = {"done": -1, "failed": -1, "skipped": -1}

        def on_progress(progress: fetch.Progress) -> None:
            report(progress)
            if progress.current and progress.done != last["done"]:
                runner.log(f"archive  {progress.current}")
            elif progress.failed != last["failed"] and progress.failed:
                runner.log(f"echec    {progress.current or ''}".rstrip())
            last.update(done=progress.done, failed=progress.failed,
                        skipped=progress.skipped)

        result = fetch.run(cfg, loader, on_progress=on_progress,
                           cancel=cancel, dry_run=dry_run)
        if result.error:
            runner.log(result.error)
        if result.message:
            runner.log(result.message)
        if not dry_run and result.done:
            built = catalog.build(cfg.archive, cfg.metadata_dir,
                                  fetch.read_saved_dates(cfg))
            runner.log(f"Catalogue reconstruit : {built['count']} contenus.")

    label = "Simulation" if dry_run else "Archivage"
    if not runner.start(work, label=label):
        raise ValueError("Une tache est deja en cours.")
    return {"ok": True, "message": f"{label} demarre."}


def action_fetch_cancel(state: AppState, body: dict) -> dict:
    stopped = state.runner.cancel()
    return {"ok": True, "message": "Arret demande." if stopped else "Aucune tache en cours."}


def action_open(state: AppState, body: dict) -> dict:
    what = str(body.get("what") or "archive")
    cfg = state.config
    target = {"archive": cfg.archive,
              "html": cfg.archive / "index.html",
              "csv": cfg.archive / "catalog.csv"}.get(what, cfg.archive)
    if not target.exists():
        raise ValueError(f"Rien a ouvrir : {target} n'existe pas encore.")
    if not paths.open_in_browser(str(target)):
        raise ValueError(f"Impossible d'ouvrir automatiquement. Chemin : {target}")
    return {"ok": True, "message": f"Ouvert : {target}"}


ACTIONS: dict[str, Callable[[AppState, dict], dict]] = {
    "/api/config": action_save_config,
    "/api/session/browser": action_session_browser,
    "/api/session/cookie": action_session_cookie,
    "/api/session/password": action_session_password,
    "/api/session/forget": action_session_forget,
    "/api/dyi": action_dyi,
    "/api/dyi/auto": action_dyi_auto,
    "/api/catalog": action_catalog,
    "/api/fetch/start": action_fetch_start,
    "/api/fetch/cancel": action_fetch_cancel,
    "/api/open": action_open,
}


# ---------------------------------------------------------------------------
# serveur
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = f"igarchive/{__version__}"

    def __init__(self, *args, state: AppState, **kwargs) -> None:
        self.state = state
        super().__init__(*args, **kwargs)

    # -- utilitaires -----------------------------------------------------

    def _send(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _authorised(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-IG-Token", ""), self.state.token
        )

    def log_message(self, fmt: str, *args) -> None:
        """Silence : le journal utile est celui de l'interface, pas celui d'HTTP."""

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 -- nom impose par http.server
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            page = (ASSETS / "app.html").read_text(encoding="utf-8")
            page = page.replace("__TOKEN__", self.state.token)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if route == "/api/state":
            if not self._authorised():
                self._json(403, {"ok": False, "error": "Jeton invalide."})
                return
            self._json(200, self.state.snapshot())
            return
        self._json(404, {"ok": False, "error": "Route inconnue."})

    def _handle_upload(self) -> None:
        """Recoit le .zip de l'export, par morceaux, sans le charger en memoire."""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json(400, {"ok": False, "error": "Fichier vide."})
            return
        if length > MAX_UPLOAD:
            self._json(413, {"ok": False, "error": "Fichier trop volumineux (plus de 1 Go)."})
            return

        tmp = Path(tempfile.gettempdir()) / f"igarchive-export-{secrets.token_hex(8)}.zip"
        try:
            remaining = length
            with tmp.open("wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
            with self.state.lock:
                result = ingest_export(self.state, tmp)
            self._json(200, result)
        except (dyi.ExportError, ValueError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False,
                             "error": f"Erreur inattendue : {type(exc).__name__}: {exc}"})
        finally:
            tmp.unlink(missing_ok=True)

    def do_POST(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]
        if route == "/api/dyi/upload":
            if not self._authorised():
                self._json(403, {"ok": False, "error": "Jeton invalide."})
                return
            self._handle_upload()
            return
        action = ACTIONS.get(route)
        if action is None:
            self._json(404, {"ok": False, "error": "Route inconnue."})
            return
        if not self._authorised():
            self._json(403, {"ok": False, "error": "Jeton invalide."})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._json(413, {"ok": False, "error": "Requete trop volumineuse."})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "Corps de requete illisible."})
            return

        try:
            with self.state.lock:
                result = action(self.state, body if isinstance(body, dict) else {})
            self._json(200, result)
        except (session.SessionError, dyi.ExportError, ValueError) as exc:
            # Erreurs attendues : le message est ecrit pour l'utilisateur.
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 -- ne jamais tuer le serveur
            self._json(500, {"ok": False,
                             "error": f"Erreur inattendue : {type(exc).__name__}: {exc}"})


def serve(port: int | None = None, *, open_browser: bool = True) -> None:
    """Demarre l'interface et bloque jusqu'a Ctrl-C."""
    state = AppState()
    port = port or state.config.webui_port
    handler = partial(Handler, state=state)

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise SystemExit(
            f"Impossible d'ecouter sur le port {port} : {exc}\n"
            f"Un autre programme l'utilise sans doute. Essaie :  igarchive ui --port {port + 1}"
        ) from exc

    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"igarchive {__version__} — interface sur {url}")
    print("Ctrl-C pour arreter.")
    if open_browser and not paths.open_in_browser(url):
        print("Ouvre cette adresse manuellement dans ton navigateur.")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArret de l'interface.")
    finally:
        httpd.server_close()
