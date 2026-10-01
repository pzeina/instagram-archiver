"""The settings interface, served locally by the standard library.

A design choice: rather than a GUI toolkit (Tk, Qt, GTK), which needs a
different system package on every distribution and is missing from many macOS
Python installs, the interface is a page served on 127.0.0.1 and opened in the
browser already present. Nothing to compile, the same rendering on macOS and
Linux, no extra dependency.

Security: the server listens on the loopback interface only, and every API call
must carry a token drawn at random on startup. Without that token, another web
page open in the same browser cannot drive the program.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import urllib.error
import urllib.request
import threading
import time
from dataclasses import asdict
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from igarchive import __version__, catalog, config as config_module, dyi, fetch, i18n, paths, session
from igarchive.config import Config
from igarchive.jobs import JobRunner

ASSETS = Path(__file__).parent / "assets"
MAX_BODY = 1 << 20          # 1 Mio : large pour un collage de cookies, borne contre l'abus
MAX_UPLOAD = 1 << 30        # 1 Gio : un export Instagram complet tient largement dedans
UPLOAD_CHUNK = 1 << 20


class AppState:
    """What the server shares across every request."""

    def __init__(self) -> None:
        self.config: Config = config_module.load()
        i18n.set_language(self.config.language)
        self.runner = JobRunner()
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        # Result of the last "Connect the account". While it is None the
        # interface knows nothing and claims nothing: it never queries Instagram
        # on its own.
        self.checked: dict[str, Any] | None = None

    # -- vue d'ensemble --------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        cfg = self.config
        state = self.checked or {"checked": False, "valid": False,
                                 "account": None, "exists": bool(cfg.username)}
        return {
            "version": __version__,
            "platform": "macOS" if paths.is_macos() else "Linux",
            "config": asdict(cfg),
            "config_file": str(paths.config_file()),
            "browsers": list(session.SUPPORTED_BROWSERS),
            "sessions": session.list_sessions(),
            "session": state,
            "summary": {**fetch.summary(cfg), **_collection_summary(cfg)},
            "problems": cfg.problems(),
            "problems_detail": cfg.problems_detail(),
            "job": self.runner.state(),
        }


# Re-reading every record on each page refresh costs one disk access per item,
# every second and a half. A few seconds of lag on a counter bothers nobody.
_SUMMARY_TTL = 4.0
_summary_cache: "dict[str, tuple[float, dict]]" = {}


def _collection_summary(cfg: Config) -> dict[str, Any]:
    """Item count per collection, read without rebuilding anything."""
    key = str(cfg.metadata_dir)
    now = time.monotonic()
    cached = _summary_cache.get(key)
    if cached and now - cached[0] < _SUMMARY_TTL:
        return cached[1]
    counts: dict[str, int] = {}
    for record in catalog.load_records(cfg.metadata_dir):
        for name in catalog.record_collections(record):
            counts[name] = counts.get(name, 0) + 1
    real = {k: v for k, v in counts.items() if k != catalog.UNSORTED_NAME}
    result = {"collections": dict(sorted(real.items(), key=lambda kv: (-kv[1], kv[0]))),
              "uncategorised": counts.get(catalog.UNSORTED_NAME, 0)}
    _summary_cache[key] = (now, result)
    return result


# ---------------------------------------------------------------------------
# API actions
# ---------------------------------------------------------------------------

def action_save_config(state: AppState, body: dict) -> dict:
    current = asdict(state.config)
    known = set(current)
    incoming = {k: v for k, v in body.items() if k in known}

    # Numbers arrive as text from a form: convert them without letting an empty
    # field wipe out a valid setting.
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
    i18n.set_language(new_config.language)
    return {"ok": True, "config": asdict(new_config), "problems": new_config.problems()}


def action_session_browser(state: AppState, body: dict) -> dict:
    browser = str(body.get("browser") or state.config.browser)
    username = str(body.get("username") or state.config.username)
    account, path = session.open_from_browser(browser, username)
    state.config.username = account
    state.config.browser = browser
    state.config.save()
    return {"ok": True, "account": account, "file": str(path),
            "message": i18n.t("session_opened", account=account)}


def action_session_connect(state: AppState, body: dict) -> dict:
    """The one place where Instagram is asked about the session.

    With ``add``, no account is assumed: whichever one the browser is signed
    into is taken. That is what adding a second account means in practice --
    the user signs in elsewhere, then tells us to look again.
    """
    wanted = "" if body.get("add") else str(body.get("username") or state.config.username)
    result = session.connect(wanted)
    state.checked = result
    if result.get("account") and result["account"] != state.config.username:
        state.config.username = result["account"]
        state.config.save()
    return {"ok": True, **result}


def action_session_use(state: AppState, body: dict) -> dict:
    """Switch to a session already stored."""
    username = str(body.get("username") or "").strip()
    if username not in session.list_sessions():
        raise ValueError(i18n.t("no_saved_session", account=username))
    state.config.username = username
    state.config.save()
    state.checked = None          # un autre compte demande une nouvelle verification
    session.invalidate(username)
    return {"ok": True, "account": username,
            "message": i18n.t("active_account", state.config.language, account=username)}


def action_session_cookie(state: AppState, body: dict) -> dict:
    blob = str(body.get("cookie") or "")
    username = str(body.get("username") or state.config.username)
    account, path = session.open_from_blob(blob, username)
    state.config.username = account
    state.config.save()
    return {"ok": True, "account": account, "file": str(path),
            "message": i18n.t("session_opened", account=account)}


def action_session_password(state: AppState, body: dict) -> dict:
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not username or not password:
        raise session.SessionError("Identifiant et mot de passe sont tous deux requis.")
    account, path = session.open_interactive(username, password)
    state.config.username = account
    state.config.save()
    return {"ok": True, "account": account, "file": str(path),
            "message": i18n.t("session_opened", account=account)}


def action_session_forget(state: AppState, body: dict) -> dict:
    username = str(body.get("username") or state.config.username)
    removed = session.forget(username)
    state.checked = None
    return {"ok": True, "removed": removed,
            "message": i18n.t("session_forgotten" if removed else "no_session_to_forget",
                              state.config.language)}


def action_dyi(state: AppState, body: dict) -> dict:
    raw = str(body.get("path") or "").strip().strip("'\"")
    if not raw:
        raise ValueError(i18n.t("give_export_path", state.config.language))
    return ingest_export(state, Path(raw).expanduser())


def ingest_exports(state: AppState, sources: list[Path]) -> dict:
    """Read one or more exports and merge dates and collections.

    Rebuilds the catalogue straight away: records already written gain their
    date and their collection with no media downloaded again.
    """
    parsed = dyi.parse_all(sources)
    merged, added = dyi.merge_into(fetch.read_saved_dates(state.config), parsed)
    fetch.write_saved_dates(state.config, merged)

    cfg = state.config
    cfg.ensure_dirs()
    built = catalog.build(cfg.archive, cfg.metadata_dir, merged, cfg.language)
    names = sorted(built.get("collections", {}))
    lang = cfg.language
    detail = (i18n.t("collections_found", lang, count=len(names), names=", ".join(names))
              if names else "")
    return {"ok": True, "total": len(merged), "added": added,
            "collections": built.get("collections", {}),
            "dated": built["dated"],
            "message": (i18n.t("dated_items", lang, total=len(merged), added=added)
                        + detail + " "
                        + i18n.t("catalogue_updated", lang, dated=built["dated"]))}


def ingest_export(state: AppState, source: Path) -> dict:
    return ingest_exports(state, [source])


def action_dyi_auto(state: AppState, body: dict) -> dict:
    """Look for the export in the downloads folder and on the desktop."""
    sources = dyi.find_exports()
    if not sources:
        raise ValueError(i18n.t("no_export_found"))
    result = ingest_exports(state, sources)
    result["sources"] = [str(s) for s in sources]
    result["message"] = (f"Lu depuis {', '.join(s.name for s in sources)}. "
                         + result["message"])
    return result


def action_catalog(state: AppState, body: dict) -> dict:
    cfg = state.config
    cfg.ensure_dirs()
    result = catalog.build(cfg.archive, cfg.metadata_dir,
                          fetch.read_saved_dates(cfg), cfg.language)
    return {"ok": True, **result,
            "message": f"Catalogue reconstruit : {result['count']} contenus."}


def action_fetch_start(state: AppState, body: dict) -> dict:
    cfg = state.config
    # « utilisable » couvre la session confirmee et celle qu'Instagram n'a pas
    # voulu confirmer : seule une deconnexion averee doit bloquer la sauvegarde.
    if not (state.checked and state.checked.get("usable")):
        raise ValueError(i18n.t("connect_first", cfg.language))
    problems = [p["text"] for p in cfg.problems_detail() if p["step"] != 1]
    if problems:
        raise ValueError(" ".join(problems))
    dry_run = bool(body.get("dry_run"))
    loader = session.load(cfg.username)
    runner = state.runner

    def work(cancel: threading.Event, report: Callable) -> None:
        # The official export is the only source of save dates and collections.
        # It is looked for in the archive folder alone, which the user chose:
        # sweeping "Downloads" would raise a macOS permission prompt in the
        # middle of a backup, with no visible link to what was just asked for.
        try:
            found = dyi.find_exports([cfg.archive], search_home=False)
            if found:
                result = ingest_exports(state, found)
                runner.log(i18n.t("export_read", cfg.language, message=result["message"]))
        except (dyi.ExportError, OSError, ValueError) as exc:
            runner.log(i18n.t("export_skipped", cfg.language, error=exc))

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
        session.invalidate(cfg.username)
        if result.error and "session" in result.error.lower():
            state.checked = None
        if not dry_run and result.done:
            built = catalog.build(cfg.archive, cfg.metadata_dir,
                                  fetch.read_saved_dates(cfg), cfg.language)
            runner.log(i18n.t("catalogue_rebuilt", cfg.language, count=built["count"]))

    label = i18n.t("dry_run" if dry_run else "backup", cfg.language)
    if not runner.start(work, label=label):
        raise ValueError(i18n.t("already_running", cfg.language))
    return {"ok": True, "message": i18n.t("started", cfg.language, label=label)}


def action_fetch_cancel(state: AppState, body: dict) -> dict:
    stopped = state.runner.cancel()
    return {"ok": True,
            "message": i18n.t("stop_requested" if stopped else "nothing_running",
                              state.config.language)}


def action_open(state: AppState, body: dict) -> dict:
    what = str(body.get("what") or "archive")
    cfg = state.config
    target = {"archive": cfg.archive,
              "html": cfg.archive / "index.html",
              "csv": cfg.archive / "catalog.csv"}.get(what, cfg.archive)
    if not target.exists():
        raise ValueError(i18n.t("nothing_to_open", cfg.language, path=target))
    if not paths.open_in_browser(str(target)):
        raise ValueError(i18n.t("cannot_open", cfg.language, path=target))
    return {"ok": True, "message": i18n.t("opened", cfg.language, path=target)}


ACTIONS: dict[str, Callable[[AppState, dict], dict]] = {
    "/api/config": action_save_config,
    "/api/session/browser": action_session_browser,
    "/api/session/connect": action_session_connect,
    "/api/session/use": action_session_use,
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
# server
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
        """Silence: the useful log is the interface's, not HTTP's."""

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
        """Receive the export .zip in chunks, without holding it in memory."""
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
            # Expected errors: the message is written for the user.
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 -- ne jamais tuer le serveur
            self._json(500, {"ok": False,
                             "error": f"Erreur inattendue : {type(exc).__name__}: {exc}"})


def already_running(port: int) -> bool:
    """True if the port is held by another igarchive instance.

    The port is queried rather than the process list: that is portable, and the
    Server header is enough to recognise the program.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            return str(response.headers.get("Server", "")).startswith("igarchive/")
    except (urllib.error.URLError, OSError, ValueError):
        return False


def serve(port: int | None = None, *, open_browser: bool = True) -> None:
    """Start the interface and block until Ctrl-C."""
    state = AppState()
    port = port or state.config.webui_port
    handler = partial(Handler, state=state)

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as exc:
        # By far the most common case is a window already open. Refusing to
        # start would be absurd; showing the running one is the useful answer.
        if already_running(port):
            url = f"http://127.0.0.1:{port}/"
            print(i18n.t("already_open", url=url))
            print(i18n.t("stop_it_there"))
            if open_browser:
                paths.open_in_browser(url)
            return
        raise SystemExit(i18n.t("port_busy", port=port, error=exc,
                                next=port + 1)) from exc

    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"igarchive {__version__} — {url}")
    print(i18n.t("stop_it_there"))
    if open_browser and not paths.open_in_browser(url):
        print(f"Open this address in your browser: {url}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArret de l'interface.")
    finally:
        httpd.server_close()
