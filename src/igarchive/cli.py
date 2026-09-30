"""Ligne de commande.

Elle appelle exactement les memes modules que l'interface web : tout ce qui est
faisable par l'interface l'est depuis un terminal, donc depuis une tache
planifiee ou une machine sans affichage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from igarchive import __version__, catalog, config as config_module, dyi, fetch, paths, session, webui
from igarchive.fetch import Progress


def _config(args: argparse.Namespace) -> config_module.Config:
    """Configuration enregistree, surchargee par les options de la commande."""
    cfg = config_module.load()
    for name in ("archive_dir", "sleep_min", "sleep_max", "limit_per_run",
                 "stop_after_known", "username"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(cfg, name, value)
    if getattr(args, "no_videos", False):
        cfg.download_videos = False
    if getattr(args, "comments", False):
        cfg.download_comments = True
    return cfg


# ---------------------------------------------------------------------------

def cmd_ui(args: argparse.Namespace) -> int:
    webui.serve(port=args.port, open_browser=not args.no_open)
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    cfg = config_module.load()
    username = args.user or cfg.username
    try:
        if args.cookie:
            blob = args.cookie if args.cookie != "-" else sys.stdin.read()
            account, path = session.open_from_blob(blob, username)
        elif args.password:
            account, path = session.open_interactive(username, None)
        else:
            account, path = session.open_from_browser(args.browser or cfg.browser, username)
    except session.SessionError as exc:
        print(f"Echec : {exc}", file=sys.stderr)
        return 1

    cfg.username = account
    if args.browser:
        cfg.browser = args.browser
    cfg.save()
    print(f"Session ouverte pour « {account} »")
    print(f"  jeton  : {path}  (droits 600, a traiter comme un mot de passe)")
    print(f"  reglage: {paths.config_file()}")
    return 0


def cmd_dyi(args: argparse.Namespace) -> int:
    cfg = _config(args)

    if args.export:
        sources = [Path(args.export).expanduser()]
    else:
        print("Recherche d'un export dans les telechargements et sur le bureau...")
        sources = dyi.find_exports()
        if not sources:
            print("Aucun export Instagram trouve.\n"
                  "Demande-le sur https://accountscenter.instagram.com/info_and_permissions/dyi/\n"
                  "(format JSON, en cochant « Elements enregistres »), puis relance cette\n"
                  "commande, ou indique le fichier avec --export.", file=sys.stderr)
            return 1
        for source in sources:
            print(f"  trouve : {source}")

    try:
        parsed = dyi.parse_all(sources)
    except dyi.ExportError as exc:
        print(f"Echec : {exc}", file=sys.stderr)
        return 1
    merged, added = dyi.merge_into(fetch.read_saved_dates(cfg), parsed)
    cfg.ensure_dirs()
    fetch.write_saved_dates(cfg, merged)
    noms = sorted({c for r in merged.values() for c in (r.get("collections") or [])})
    print(f"\n{len(merged)} contenus dates, dont {added} nouveaux.")
    if noms:
        print(f"{len(noms)} collections : {', '.join(noms)}")

    # Les fiches deja ecrites profitent immediatement des dates et des collections.
    built = catalog.build(cfg.archive, cfg.metadata_dir, merged)
    print(f"Catalogue mis a jour : {built['count']} contenus, "
          f"{built['dated']} avec date d'enregistrement exacte.")
    _print_collections(built)
    return 0


def _print_collections(built: dict) -> None:
    if built.get("links_supported") is False:
        print(f"  (dossiers de collections impossibles ici : {built.get('links_error')})")
        return
    for name, count in sorted(built.get("collections", {}).items(),
                              key=lambda kv: (-kv[1], kv[0])):
        print(f"    {count:4d}  {name}")


def cmd_fetch(args: argparse.Namespace) -> int:
    cfg = _config(args)
    problems = cfg.problems()
    if problems:
        for problem in problems:
            print(f"! {problem}", file=sys.stderr)
        print("\nOuvre une session :  igarchive session --user TON_PSEUDO", file=sys.stderr)
        return 1

    try:
        loader = session.load(cfg.username)
    except session.SessionError as exc:
        print(f"Echec : {exc}", file=sys.stderr)
        return 1

    state = {"done": -1, "failed": -1}

    def on_progress(progress: Progress) -> None:
        if progress.done != state["done"] and progress.current:
            print(f"  {progress.done:5d}  {progress.current}", flush=True)
        elif progress.failed != state["failed"] and progress.failed:
            print(f"  echec  {progress.current}", flush=True)
        state.update(done=progress.done, failed=progress.failed)

    verb = "Simulation" if args.dry_run else "Archivage"
    print(f"{verb} vers {cfg.archive}")
    result = fetch.run(cfg, loader, on_progress=on_progress, dry_run=args.dry_run)

    print(f"\n{result.done} traites, {result.failed} en echec, "
          f"{result.skipped} deja connus, {paths.human_bytes(result.bytes_total)}.")
    if result.message:
        print(result.message)
    if result.error:
        print(result.error, file=sys.stderr)
    if not args.dry_run and result.done:
        built = catalog.build(cfg.archive, cfg.metadata_dir, fetch.read_saved_dates(cfg))
        print(f"Catalogue : {built['count']} contenus -> {built['html']}")
    return 1 if result.error else 0


def cmd_catalog(args: argparse.Namespace) -> int:
    cfg = _config(args)
    cfg.ensure_dirs()
    built = catalog.build(cfg.archive, cfg.metadata_dir, fetch.read_saved_dates(cfg))
    print(f"{built['count']} contenus, {paths.human_bytes(built['bytes'])}, "
          f"{built['dated']} avec date d'enregistrement exacte.")
    for key in ("csv", "json", "html"):
        print(f"  {built[key]}")
    _print_collections(built)
    if args.open and not paths.open_in_browser(built["html"]):
        print("Ouverture automatique impossible ; ouvre le fichier a la main.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _config(args)
    summary = fetch.summary(cfg)
    state = session.status(cfg.username) if cfg.username else {}
    account = state.get("account")
    print(f"Compte         : {cfg.username or '(non defini)'}"
          f"{'' if not state else '  [session valide]' if state.get('valid') else '  [session invalide]'}")
    if account and account != cfg.username:
        print(f"                 (Instagram repond : {account})")
    print(f"Destination    : {summary['archive_dir']}")
    print(f"Archives       : {summary['archived']}")
    print(f"Indisponibles  : {summary['unavailable']}  (supprimes, ou comptes passes en prive)")
    print(f"En echec       : {summary['failed']}  (retentes a la prochaine passe)")
    print(f"Volume         : {paths.human_bytes(summary['bytes'])}")
    print(f"Dates connues  : {summary['known_dates']}  (depuis l'export officiel)")
    if summary["known_dates"]:
        print(f"Restant        : {summary['pending_from_export']}")
    for problem in cfg.problems():
        print(f"!  {problem}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = _config(args)
    if args.set:
        for pair in args.set:
            if "=" not in pair:
                print(f"Attendu cle=valeur, recu : {pair}", file=sys.stderr)
                return 1
            key, _, raw = pair.partition("=")
            key = key.strip()
            if not hasattr(cfg, key):
                print(f"Reglage inconnu : {key}", file=sys.stderr)
                return 1
            current = getattr(cfg, key)
            try:
                value: object = raw
                if isinstance(current, bool):
                    value = raw.strip().lower() in ("1", "true", "oui", "yes", "on")
                elif isinstance(current, int):
                    value = int(raw)
                elif isinstance(current, float):
                    value = float(raw)
            except ValueError:
                print(f"Valeur invalide pour {key} : {raw}", file=sys.stderr)
                return 1
            setattr(cfg, key, value)
        cfg.save()
        print(f"Enregistre dans {paths.config_file()}")

    width = max(len(f.name) for f in cfg.__dataclass_fields__.values())
    for name in cfg.__dataclass_fields__:
        print(f"  {name:<{width}}  {getattr(cfg, name)}")
    return 0


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="igarchive",
        description="Archivage local de vos contenus Instagram enregistres.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Sans argument, l'interface de configuration s'ouvre dans le navigateur.\n"
            "Enchainement habituel :  session -> dyi -> fetch -> catalog"
        ),
    )
    parser.add_argument("--version", action="version", version=f"igarchive {__version__}")
    sub = parser.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--archive-dir", dest="archive_dir", help="dossier de destination")
    common.add_argument("--user", dest="username", help="compte Instagram")

    p = sub.add_parser("ui", help="ouvrir l'interface de configuration (par defaut)")
    p.add_argument("--port", type=int, default=None, help="port d'ecoute local")
    p.add_argument("--no-open", action="store_true", help="ne pas ouvrir le navigateur")
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("session", parents=[common], help="ouvrir une session Instagram")
    p.add_argument("--browser", choices=session.SUPPORTED_BROWSERS,
                   help="reprendre les cookies de ce navigateur (defaut : firefox)")
    p.add_argument("--cookie", metavar="VALEUR",
                   help="coller un sessionid ou un en-tete Cookie ; « - » pour l'entree standard")
    p.add_argument("--password", action="store_true",
                   help="se connecter par mot de passe (demande au terminal)")
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("dyi", parents=[common],
                       help="lire l'export officiel (dates d'enregistrement)")
    p.add_argument("--export", help="chemin du .zip ou du dossier decompresse ; "
                                    "sans cette option, l'export est cherche automatiquement")
    p.set_defaults(func=cmd_dyi)

    p = sub.add_parser("fetch", parents=[common], help="telecharger les contenus enregistres")
    p.add_argument("--limit", dest="limit_per_run", type=int,
                   help="plafond de contenus pour cette passe")
    p.add_argument("--stop-after-known", dest="stop_after_known", type=int, metavar="N",
                   help="s'arreter apres N contenus deja connus d'affilee")
    p.add_argument("--sleep-min", dest="sleep_min", type=float, help="pause minimale (s)")
    p.add_argument("--sleep-max", dest="sleep_max", type=float, help="pause maximale (s)")
    p.add_argument("--no-videos", action="store_true", help="metadonnees et images seulement")
    p.add_argument("--comments", action="store_true", help="telecharger aussi les commentaires")
    p.add_argument("--dry-run", action="store_true", help="lister sans rien telecharger")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("catalog", parents=[common], help="reconstruire le catalogue")
    p.add_argument("--open", action="store_true", help="ouvrir le catalogue a la fin")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("status", parents=[common], help="etat de l'archive")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("config", parents=[common], help="afficher ou modifier les reglages")
    p.add_argument("--set", action="append", metavar="CLE=VALEUR",
                   help="modifier un reglage (repetable)")
    p.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # Sans sous-commande, l'interface graphique est le comportement attendu.
        return cmd_ui(argparse.Namespace(port=None, no_open=False))
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrompu.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
