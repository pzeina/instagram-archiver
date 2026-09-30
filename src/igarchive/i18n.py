"""User-facing strings, in English and French.

Only strings the program itself composes live here. Text coming from
instaloader or from the operating system is passed through untouched: it is
already in English, and mistranslating a technical message helps nobody.

The interface keeps its own copy of the strings it renders; this module covers
what the server sends back and what the generated catalogue contains.
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "fr")

STRINGS: dict[str, dict[str, str]] = {
    # -- session --------------------------------------------------------
    "connected_to": {
        "en": "Connected to {account}.",
        "fr": "Connecté au compte {account}.",
    },
    "throttled": {
        "en": "Instagram did not answer — the account is rate limited for now. "
              "Try again in a few minutes.",
        "fr": "Instagram n'a pas répondu — le compte est momentanément limité. "
              "Réessayez dans quelques minutes.",
    },
    "unconfirmed": {
        "en": "Session opened, but Instagram did not confirm it. "
              "Try again in a few minutes.",
        "fr": "Session ouverte mais Instagram ne l'a pas confirmée. "
              "Réessayez dans quelques minutes.",
    },
    "no_browser_session": {
        "en": "No Instagram session found in your browsers.\n"
              "Sign in at instagram.com, then try again.",
        "fr": "Aucune session Instagram trouvée dans vos navigateurs.\n"
              "Connectez-vous sur instagram.com, puis réessayez.",
    },
    "no_sessionid": {
        "en": "No « sessionid » cookie for instagram.com.\n"
              "Sign in at instagram.com in this browser, then try again.",
        "fr": "Aucun cookie « sessionid » pour instagram.com.\n"
              "Connectez-vous à instagram.com dans ce navigateur, puis réessayez.",
    },
    "session_forgotten": {"en": "Session forgotten.", "fr": "Session oubliée."},
    "no_session_to_forget": {
        "en": "No session to forget.", "fr": "Aucune session à oublier."},
    "active_account": {
        "en": "Active account: {account}.", "fr": "Compte actif : {account}."},
    "connect_first": {
        "en": "Connect the account before starting the backup.",
        "fr": "Connectez le compte avant de lancer la sauvegarde.",
    },

    # -- archiving ------------------------------------------------------
    "started": {"en": "{label} started.", "fr": "{label} démarré."},
    "backup": {"en": "Backup", "fr": "Archivage"},
    "dry_run": {"en": "Dry run", "fr": "Simulation"},
    "already_running": {
        "en": "A task is already running.", "fr": "Une tâche est déjà en cours."},
    "stop_requested": {"en": "Stop requested.", "fr": "Arrêt demandé."},
    "nothing_running": {
        "en": "No task is running.", "fr": "Aucune tâche en cours."},
    "rate_limited": {
        "en": "Instagram is rate limiting requests. The backup stopped to protect "
              "the account: start it again in one or two hours and it will resume "
              "where it left off.",
        "fr": "Instagram limite les requêtes. L'archivage s'est arrêté pour protéger "
              "le compte : relancez dans une à deux heures, il reprendra où il en est.",
    },
    "session_expired_midrun": {
        "en": "The session expired. Connect the account again.",
        "fr": "La session a expiré. Connectez le compte à nouveau.",
    },
    "refused": {
        "en": "Instagram refused the request: {error}",
        "fr": "Instagram a refusé la requête : {error}",
    },
    "stopped_on_request": {
        "en": "Stopped on request.", "fr": "Arrêté à la demande."},
    "limit_reached": {
        "en": "Reached the limit of {limit} for this pass.",
        "fr": "Plafond de {limit} atteint pour cette passe.",
    },
    "known_streak": {
        "en": "{count} already-known items in a row: update finished.",
        "fr": "{count} contenus déjà connus d'affilée : mise à jour terminée.",
    },

    # -- export ---------------------------------------------------------
    "dated_items": {
        "en": "{total} items dated, {added} of them new.",
        "fr": "{total} contenus datés, dont {added} nouveaux.",
    },
    "collections_found": {
        "en": " {count} collections: {names}.",
        "fr": " {count} collections : {names}.",
    },
    "catalogue_updated": {
        "en": "Catalogue updated: {dated} dated records.",
        "fr": "Catalogue mis à jour : {dated} fiches datées.",
    },
    "catalogue_rebuilt": {
        "en": "Catalogue rebuilt: {count} items.",
        "fr": "Catalogue reconstruit : {count} contenus.",
    },
    "export_read": {
        "en": "Official export read: {message}",
        "fr": "Export officiel lu : {message}",
    },
    "export_skipped": {
        "en": "Official export skipped: {error}",
        "fr": "Export officiel ignoré : {error}",
    },
    "give_export_path": {
        "en": "Give the path of the .zip received from Instagram.",
        "fr": "Indiquez le chemin du .zip reçu d'Instagram.",
    },

    # -- files ----------------------------------------------------------
    "nothing_to_open": {
        "en": "Nothing to open: {path} does not exist yet.",
        "fr": "Rien à ouvrir : {path} n'existe pas encore.",
    },
    "cannot_open": {
        "en": "Could not open it automatically. Path: {path}",
        "fr": "Impossible d'ouvrir automatiquement. Chemin : {path}",
    },
    "opened": {"en": "Opened: {path}", "fr": "Ouvert : {path}"},

    # -- generated catalogue --------------------------------------------
    "cat_title": {"en": "Saved content", "fr": "Contenus enregistrés"},
    "cat_of": {"en": "of {total} items", "fr": "sur {total} contenus"},
    "cat_on_disk": {"en": "on disk", "fr": "sur le disque"},
    "cat_dates": {
        "en": "with a save date", "fr": "avec date d'enregistrement"},
    "cat_collections": {"en": "collections", "fr": "collections"},
    "cat_search": {
        "en": "Search an account, a caption, a hashtag",
        "fr": "Rechercher un auteur, une légende, un hashtag",
    },
    "cat_all_collections": {
        "en": "all collections", "fr": "toutes les collections"},
    "cat_no_collection": {"en": "no collection", "fr": "sans collection"},
    "cat_all": {"en": "all", "fr": "tout"},
    "cat_posted": {"en": "posted", "fr": "publié"},
    "cat_saved_on": {"en": "saved on", "fr": "enregistré le"},
    "cat_saved_rank": {"en": "saved", "fr": "enregistré"},
    "cat_rank_value": {"en": "{position} in the list", "fr": "{position} de la liste"},
    "cat_rank_hint": {
        "en": "Exact date available once Instagram's official export is imported",
        "fr": "Date exacte disponible après import de l'export officiel Instagram",
    },
    "cat_no_media": {"en": "no media", "fr": "aucun média"},
    "cat_empty": {
        "en": "Nothing archived yet.", "fr": "Aucun contenu archivé pour le moment."},
    "cat_banner": {
        "en": "<b>{count} item(s) without a save date.</b> Instagram does not expose "
              "that date; only its official export contains it. Once the export is "
              "imported, this page shows « saved on … » instead of the rank, and your "
              "collections appear here.",
        "fr": "<b>{count} contenu(s) sans date d'enregistrement.</b> Instagram n'expose "
              "pas cette date&nbsp;; seul son export officiel la contient. Une fois "
              "l'export importé, cette page affichera « enregistré le&nbsp;… » au lieu "
              "de l'ordre, et les collections apparaîtront ici.",
    },
}


_active = DEFAULT_LANGUAGE


def set_language(language: str | None) -> str:
    """Set the language used when a call does not name one.

    A single-user desktop program has exactly one interface language at a time;
    threading it through every call site would add noise and nothing else.
    """
    global _active
    _active = normalise(language)
    return _active


def active() -> str:
    return _active


def normalise(language: str | None) -> str:
    """Return a supported language, English by default."""
    code = (language or "").strip().lower()[:2]
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def t(key: str, language: str | None = None, **fields: object) -> str:
    """Translate a key. An unknown key shows itself rather than vanishing."""
    entry = STRINGS.get(key)
    if entry is None:
        return f"[{key}]"
    text = entry.get(normalise(language or _active), entry[DEFAULT_LANGUAGE])
    return text.format(**fields) if fields else text

STRINGS.update({
    "no_firefox_profile": {
        "en": "No Firefox profile found. Install Firefox and sign in to "
              "instagram.com, or use another method.",
        "fr": "Aucun profil Firefox trouvé. Installez Firefox et connectez-vous à "
              "instagram.com, ou utilisez une autre méthode.",
    },
    "firefox_no_cookies": {
        "en": "Firefox profile found, but no cookies.sqlite file.",
        "fr": "Profil Firefox trouvé, mais aucun fichier cookies.sqlite.",
    },
    "needs_browser_cookie3": {
        "en": "Reading {browser} cookies needs the browser_cookie3 package:\n"
              "    pip install browser-cookie3\n"
              "Otherwise use Firefox, or paste your sessionid cookie.",
        "fr": "Lire les cookies de {browser} demande le paquet browser_cookie3 :\n"
              "    pip install browser-cookie3\n"
              "Sinon, utilisez Firefox ou collez votre cookie sessionid.",
    },
    "unknown_browser": {
        "en": "Unknown browser: {browser}", "fr": "Navigateur inconnu : {browser}"},
    "unsupported_browser": {
        "en": "Browser not supported: {browser}. Choices: {choices}.",
        "fr": "Navigateur non pris en charge : {browser}. Choix : {choices}.",
    },
    "cookie_read_failed": {
        "en": "Could not read {browser} cookies: {error}",
        "fr": "Lecture des cookies de {browser} impossible : {error}",
    },
    "no_cookie_given": {"en": "No cookie given.", "fr": "Aucun cookie fourni."},
    "session_refused": {
        "en": "Instagram refused the session: {error}",
        "fr": "Instagram a refusé la session : {error}",
    },
    "session_invalid": {
        "en": "Invalid session: Instagram recognises no signed-in account.\n"
              "Sign in to instagram.com in your browser, then try again.",
        "fr": "Session invalide : Instagram ne reconnaît aucun compte connecté.\n"
              "Connectez-vous à instagram.com dans votre navigateur, puis recommencez.",
    },
    "no_saved_session": {
        "en": "No saved session for {account}.",
        "fr": "Aucune session enregistrée pour {account}.",
    },
    "session_unreadable": {
        "en": "Session unreadable ({error}). Open it again.",
        "fr": "Session illisible ({error}). Ouvrez-la à nouveau.",
    },
    "login_refused": {
        "en": "Sign-in refused: {error}", "fr": "Connexion refusée : {error}"},
    "credentials_required": {
        "en": "Both a username and a password are required.",
        "fr": "Identifiant et mot de passe sont tous deux requis.",
    },
})
