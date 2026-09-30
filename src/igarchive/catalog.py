"""Fiche normalisee d'un contenu, et les trois sorties du catalogue.

Une fiche par contenu est ecrite dans metadata/. Le catalogue se reconstruit
entierement a partir de ces fiches : rien n'oblige a retelecharger pour changer
la presentation, et les fiches restent lisibles sans cet outil.
"""

from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from igarchive import paths

SCHEMA_VERSION = 2

CSV_COLUMNS = [
    "shortcode", "url", "kind", "author", "posted_at_utc", "saved_at", "saved_rank",
    "collection", "caption", "hashtags", "mentions", "location", "media_count",
    "video_duration_s", "likes", "comments", "is_sponsored", "directory", "bytes_total",
]

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

COLLECTIONS_DIR = "collections"
UNSORTED_NAME = "Sans collection"


@dataclass
class Record:
    """Tout ce qu'on retient d'un contenu, independamment de l'API d'Instagram."""

    schema_version: int = SCHEMA_VERSION
    shortcode: str = ""
    url: str = ""
    kind: str = ""                   # reel | video | image | carousel
    typename: str = ""
    author: str | None = None
    author_id: int | None = None
    posted_at_utc: str | None = None
    posted_at_local: str | None = None
    saved_at: str | None = None      # de l'export officiel uniquement
    saved_timestamp: int | None = None
    saved_rank: int | None = None    # 0 = enregistre le plus recemment
    collection: str | None = None
    collections: list[str] = field(default_factory=list)
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    location: str | None = None
    media_count: int = 1
    is_video: bool = False
    video_duration_s: float | None = None
    video_views: int | None = None
    likes: int | None = None
    comments: int | None = None
    is_sponsored: bool = False
    directory: str = ""
    files: list[str] = field(default_factory=list)
    bytes_total: int = 0
    fetched_at: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Record":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# lecture / ecriture des fiches
# ---------------------------------------------------------------------------

def write_record(metadata_dir: Path, record: Record) -> Path:
    metadata_dir.mkdir(parents=True, exist_ok=True)
    target = metadata_dir / f"{record.shortcode}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(target)
    return target


def load_records(metadata_dir: Path) -> list[dict]:
    """Toutes les fiches, triees : enregistre le plus recemment d'abord."""
    records: list[dict] = []
    if not metadata_dir.is_dir():
        return records
    for path in sorted(metadata_dir.glob("*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
    records.sort(key=lambda r: (
        r.get("saved_timestamp") is None,        # les dates connues d'abord
        -(r.get("saved_timestamp") or 0),
        r.get("saved_rank") or 0,
    ))
    return records


def apply_saved_dates(metadata_dir: Path, saved_dates: dict[str, dict]) -> int:
    """Reporte les dates de l'export officiel sur les fiches deja ecrites."""
    patched = 0
    for path in sorted(metadata_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        saved = saved_dates.get(raw.get("shortcode", ""))
        if not saved:
            continue
        changed = False
        for key in ("saved_at", "saved_timestamp", "collection", "collections"):
            value = saved.get(key)
            if value and raw.get(key) != value:
                raw[key] = value
                changed = True
        if changed:
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            patched += 1
    return patched


# ---------------------------------------------------------------------------
# sorties
# ---------------------------------------------------------------------------

def safe_folder_name(name: str, limit: int = 80) -> str:
    """Nom de collection utilisable comme nom de dossier sur macOS et Linux."""
    cleaned = re.sub(r"[/\\:\x00-\x1f]+", "-", name).strip(" .")
    return cleaned[:limit] or "Sans nom"


def record_collections(record: dict) -> list[str]:
    names = list(record.get("collections") or [])
    if not names and record.get("collection"):
        names = [record["collection"]]
    return names or [UNSORTED_NAME]


def build_collection_links(archive: Path, records: list[dict]) -> dict[str, Any]:
    """Recree l'arborescence des collections, comme dans l'application.

    Ce sont des liens symboliques, pas des copies : un reel range dans trois
    collections n'occupe la place qu'une fois. Les liens sont relatifs, donc
    l'archive reste deplacable d'un disque a l'autre.
    """
    root = archive / COLLECTIONS_DIR

    # Nettoyage : on ne supprime QUE des liens symboliques, jamais un fichier
    # reel. Une collection supprimee dans l'application disparait ainsi d'ici
    # sans qu'aucun media ne soit touche.
    removed = 0
    if root.exists():
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_symlink():
                path.unlink()
                removed += 1
        for path in sorted(root.glob("*"), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()

    counts: dict[str, int] = {}
    for record in records:
        directory = record.get("directory")
        if not directory:
            continue
        for name in record_collections(record):
            folder = root / safe_folder_name(name)
            link = folder / Path(directory).name
            # Depuis collections/<nom>/<lien>, le media est deux crans au-dessus.
            target = Path("..") / ".." / directory
            try:
                folder.mkdir(parents=True, exist_ok=True)
                if not link.is_symlink() and not link.exists():
                    link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                # Certains systemes de fichiers (exFAT d'un disque externe) ne
                # gerent pas les liens. Le catalogue reste utilisable sans eux.
                return {"supported": False, "error": str(exc), "collections": counts,
                        "removed": removed}
            counts[name] = counts.get(name, 0) + 1

    return {"supported": True, "error": None, "collections": counts, "removed": removed}


def write_json_catalog(archive: Path, records: list[dict]) -> Path:
    target = archive / "catalog.json"
    target.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def write_csv_catalog(archive: Path, records: list[dict]) -> Path:
    target = archive / "catalog.csv"
    # utf-8-sig : le BOM fait ouvrir le fichier correctement par Excel et Numbers.
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["hashtags"] = " ".join(record.get("hashtags") or [])
            row["mentions"] = " ".join(record.get("mentions") or [])
            row["caption"] = (record.get("caption") or "").replace("\n", " ").strip()
            writer.writerow(row)
    return target


PAGE_CSS = """
:root { --bg:#fbfbfa; --fg:#1c1c1a; --muted:#6b6b66; --line:#e3e3df; --card:#fff;
        --accent:#2b5fd9; --shadow:0 1px 2px rgba(0,0,0,.05); }
@media (prefers-color-scheme: dark) { :root:not([data-theme=light]) {
  --bg:#16161a; --fg:#ececea; --muted:#9a9a95; --line:#2c2c32; --card:#1e1e24;
  --accent:#7fa5ff; --accent-soft:#232a44; --shadow:0 1px 2px rgba(0,0,0,.3); } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
header { position:sticky; top:0; z-index:5; background:var(--bg);
  border-bottom:1px solid var(--line); padding:16px 0; }
.wrap { max-width:1240px; margin:0 auto; padding:0 16px; }
h1 { font-size:19px; margin:0 0 4px; }
.meta { color:var(--muted); font-size:13px; }
.controls { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
input[type=search], select { padding:8px 12px; border:1px solid var(--line);
  border-radius:8px; background:var(--card); color:var(--fg); font-size:14px; }
input[type=search] { flex:1 1 240px; min-width:0; }
button { padding:8px 12px; border:1px solid var(--line); border-radius:8px;
  background:var(--card); color:var(--fg); cursor:pointer; font-size:13px; }
button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.grid { display:grid; gap:16px; padding:20px 0 60px;
  grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
  overflow:hidden; display:flex; flex-direction:column; box-shadow:var(--shadow); }
/* position:absolute a l'interieur : une video portrait ne peut pas etirer la carte,
   ce que aspect-ratio seul ne garantit pas sur un conteneur flex. */
.media { position:relative; background:#000; aspect-ratio:1/1; overflow:hidden; }
.media video, .media img { position:absolute; inset:0; width:100%; height:100%;
  object-fit:contain; display:block; }
.nomedia { position:absolute; inset:0; display:flex; align-items:center;
  justify-content:center; color:#777; font-size:13px; }
.body { padding:12px 14px 14px; }
.head { display:flex; justify-content:space-between; align-items:center; gap:8px; }
.author { color:var(--accent); font-weight:600; text-decoration:none;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.badge { font-size:11px; color:var(--muted); border:1px solid var(--line);
  border-radius:99px; padding:1px 8px; white-space:nowrap; }
.dates { display:flex; gap:12px; flex-wrap:wrap; font-size:12px; color:var(--muted);
  margin:6px 0 8px; }
.caption { margin:0; font-size:13.5px; white-space:pre-wrap; overflow-wrap:anywhere;
  max-height:8.2em; overflow:auto; }
.tags { margin-top:8px; display:flex; gap:6px; flex-wrap:wrap; }
.tags span { font-size:11.5px; color:var(--muted); }
.approx { font-style:italic; opacity:.85; border-bottom:1px dotted currentColor; cursor:help; }
.colls { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:8px; }
.colls:empty { display:none; }
.coll { font-size:11.5px; padding:1px 8px; border-radius:99px;
  background:var(--accent-soft, rgba(43,95,217,.1)); color:var(--accent); }
.banner { margin:14px 0 0; padding:10px 14px; border-radius:9px; font-size:13.5px;
  border:1px solid var(--line); background:var(--card); color:var(--muted); }
.banner b { color:var(--fg); }
.empty { padding:60px 0; text-align:center; color:var(--muted); }
@media (max-width:520px) { .grid { grid-template-columns:1fr; } }
"""

PAGE_JS = """
var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
var q = document.getElementById('q');
var coll = document.getElementById('coll');
var count = document.getElementById('count');
var kind = '';
function apply() {
  var term = q.value.trim().toLowerCase();
  var wanted = coll.value;
  var shown = 0;
  cards.forEach(function (c) {
    var inColl = !wanted
      || (wanted === '__none__'
            ? !c.dataset.collections
            : c.dataset.collections.indexOf('|' + wanted + '|') !== -1);
    var ok = (!kind || c.dataset.kind === kind) && inColl
          && (!term || c.dataset.search.indexOf(term) !== -1);
    c.style.display = ok ? '' : 'none';
    if (ok) shown++;
  });
  count.textContent = shown;
}
q.addEventListener('input', apply);
coll.addEventListener('change', apply);
document.querySelectorAll('button[data-filter]').forEach(function (b) {
  b.addEventListener('click', function () {
    document.querySelectorAll('button[data-filter]').forEach(function (o) {
      o.classList.remove('on');
    });
    b.classList.add('on');
    kind = b.dataset.filter;
    apply();
  });
});
"""


def _card(record: dict) -> str:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    files = record.get("files") or []
    video = next((f for f in files if f.lower().endswith(".mp4")), None)
    poster = next((f for f in files if f.lower().endswith(IMAGE_SUFFIXES)), None)

    if video:
        attrs = f' poster="{esc(poster)}"' if poster else ""
        preview = (f'<video controls preload="none" playsinline{attrs} '
                   f'src="{esc(video)}"></video>')
    elif poster:
        preview = f'<img loading="lazy" src="{esc(poster)}" alt="">'
    else:
        preview = '<div class="nomedia">aucun media</div>'

    caption = record.get("caption") or ""
    shown_caption = caption[:600]
    ellipsis = "&hellip;" if len(caption) > 600 else ""

    # La date d'enregistrement ne vient que de l'export officiel. Tant qu'il n'a
    # pas ete importe, on n'a que l'ordre : autant le dire plutot que d'afficher
    # un « rang 3 » que rien n'explique.
    saved_at = (record.get("saved_at") or "")[:10]
    if saved_at:
        saved_html = (f'<span title="date d\'enregistrement">enregistr&eacute; le '
                      f'{esc(saved_at)}</span>')
    else:
        rank = record.get("saved_rank")
        position = "1er" if rank == 0 else f"{(rank or 0) + 1}e"
        saved_html = ('<span class="approx" title="Date exacte disponible apres import '
                      f'de l\'export officiel Instagram">enregistr&eacute; : {position} '
                      'de la liste</span>')

    collections = [c for c in (record.get("collections") or []) if c]
    if not collections and record.get("collection"):
        collections = [record["collection"]]
    collection_html = "".join(
        f'<span class="coll">{esc(c)}</span>' for c in collections[:3])
    collection_data = "|" + "|".join(collections) + "|" if collections else ""
    haystack = " ".join([
        record.get("author") or "", caption,
        " ".join(record.get("hashtags") or []),
        record.get("collection") or "", record.get("kind") or "",
    ]).lower()
    tags = " ".join(f"<span>#{esc(t)}</span>" for t in (record.get("hashtags") or [])[:8])

    return f"""
      <article class="card" data-kind="{esc(record.get('kind'))}"
               data-collections="{esc(collection_data)}"
               data-search="{esc(haystack)}">
        <div class="media">{preview}</div>
        <div class="body">
          <div class="head">
            <a class="author" href="{esc(record.get('url'))}" target="_blank"
               rel="noopener">@{esc(record.get('author'))}</a>
            <span class="badge">{esc(record.get('kind'))}</span>
          </div>
          <div class="dates">
            <span title="date de publication">publi&eacute; {esc((record.get('posted_at_utc') or '')[:10])}</span>
            {saved_html}
          </div>
          <div class="colls">{collection_html}</div>
          <p class="caption">{esc(shown_caption)}{ellipsis}</p>
          <div class="tags">{tags}</div>
        </div>
      </article>"""


def write_html_catalog(archive: Path, records: list[dict]) -> Path:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    total = paths.human_bytes(sum(r.get("bytes_total", 0) for r in records))
    dated = sum(1 for r in records if r.get("saved_at"))
    kinds = sorted({r.get("kind") or "?" for r in records})
    collections = sorted({name for r in records for name in (r.get("collections") or [])
                          if name} | {r["collection"] for r in records if r.get("collection")})
    undated = len(records) - dated

    filters = "".join(
        f'<button data-filter="{esc(k)}">{esc(k)}</button>' for k in kinds
    )
    options = "".join(
        f'<option value="{esc(c)}">{esc(c)}</option>' for c in collections
    )
    if undated or not collections:
        options += '<option value="__none__">sans collection</option>'

    banner = ""
    if undated:
        banner = (
            f'<p class="banner"><b>{undated} contenu(s) sans date d\'enregistrement.</b> '
            "Instagram n'expose pas cette date&nbsp;; seul son export officiel la contient. "
            "Une fois l'export import&eacute;, cette page affichera "
            "« enregistr&eacute; le&nbsp;&hellip; » au lieu de l'ordre, et les collections "
            "appara&icirc;tront ici.</p>")
    cards = "".join(_card(r) for r in records)
    body = cards or '<p class="empty">Aucun contenu archive pour le moment.</p>'

    target = archive / "index.html"
    target.write_text(f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Archive Instagram &mdash; contenus enregistr&eacute;s</title>
<style>{PAGE_CSS}</style></head><body>
<header><div class="wrap">
  <h1>Archive Instagram &mdash; contenus enregistr&eacute;s</h1>
  <div class="meta"><span id="count">{len(records)}</span> sur {len(records)} contenus
    &middot; {total} &middot; {dated} avec date d'enregistrement exacte
    &middot; g&eacute;n&eacute;r&eacute; le {datetime.now():%Y-%m-%d %H:%M}</div>
  <div class="controls">
    <input type="search" id="q" placeholder="Rechercher : auteur, l&eacute;gende, hashtag&hellip;">
    <select id="coll"><option value="">toutes les collections</option>{options}</select>
    <button data-filter="" class="on">tout</button>{filters}
  </div>
  {banner}
</div></header>
<main class="wrap"><div class="grid" id="grid">{body}</div></main>
<script>{PAGE_JS}</script></body></html>
""", encoding="utf-8")
    return target


def build(archive: Path, metadata_dir: Path,
          saved_dates: dict[str, dict] | None = None) -> dict[str, Any]:
    """Reconstruit les trois sorties. Rend un resume pour l'appelant."""
    archive.mkdir(parents=True, exist_ok=True)
    patched = apply_saved_dates(metadata_dir, saved_dates) if saved_dates else 0
    records = load_records(metadata_dir)
    links = build_collection_links(archive, records)
    return {
        "count": len(records),
        "collections": links["collections"],
        "links_supported": links["supported"],
        "links_error": links["error"],
        "patched": patched,
        "dated": sum(1 for r in records if r.get("saved_at")),
        "bytes": sum(r.get("bytes_total", 0) for r in records),
        "json": str(write_json_catalog(archive, records)),
        "csv": str(write_csv_catalog(archive, records)),
        "html": str(write_html_catalog(archive, records)),
    }
