"""The normalised record of an item, and the catalogue's three outputs.

One record per item is written to metadata/. The catalogue is rebuilt entirely
from those records: nothing has to be downloaded again to change the
presentation, and the records stay readable without this program.
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

from igarchive import i18n, paths

SCHEMA_VERSION = 2

CSV_COLUMNS = [
    "shortcode", "url", "kind", "author", "posted_at_utc", "saved_at", "saved_rank",
    "collection", "caption", "hashtags", "mentions", "location", "media_count",
    "video_duration_s", "likes", "comments", "is_sponsored", "directory", "bytes_total",
]

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

COLLECTIONS_DIR = "collections"
UNSORTED_NAME = "No collection"   # nom de dossier, volontairement stable


@dataclass
class Record:
    """Everything kept about an item, independent of Instagram's API."""

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
# reading and writing records
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
    """Every record, ordered with the most recently saved first."""
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
    """Carry the official export's dates onto records already written."""
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
# outputs
# ---------------------------------------------------------------------------

def safe_folder_name(name: str, limit: int = 80) -> str:
    """A collection name usable as a folder name on macOS and Linux."""
    cleaned = re.sub(r"[/\\:\x00-\x1f]+", "-", name).strip(" .")
    return cleaned[:limit] or "Sans nom"


def record_collections(record: dict) -> list[str]:
    names = list(record.get("collections") or [])
    if not names and record.get("collection"):
        names = [record["collection"]]
    return names or [UNSORTED_NAME]


def build_collection_links(archive: Path, records: list[dict]) -> dict[str, Any]:
    """Rebuild the collection tree, as it appears in the app.

    These are symbolic links, not copies: a reel filed under three collections
    takes the space of one. The links are relative, so the archive stays movable
    from one disk to another.
    """
    root = archive / COLLECTIONS_DIR

    # Cleanup removes ONLY symbolic links, never a real file. A collection
    # deleted in the app therefore disappears from here without any media being
    # touched.
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
            # From collections/<name>/<link>, the media sits two levels up.
            target = Path("..") / ".." / directory
            try:
                folder.mkdir(parents=True, exist_ok=True)
                if not link.is_symlink() and not link.exists():
                    link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                # Some filesystems (exFAT on an external disk) have no links.
                # The catalogue stays usable without them.
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
    # utf-8-sig: the BOM is what makes Excel and Numbers open this correctly.
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
/* A reading register: plates with their captions, separated by whitespace
   rather than by boxes. Same palette and typography as the settings page, so
   the two read as one object. */
:root {
  --paper:#f7f8fa; --ink:#16181d; --ink-2:#565d6b; --ink-3:#8a909c;
  --rule:#e2e6ec; --rule-2:#cdd3dc; --field:#fff; --mount:#eceef2;
  --signal:#0e5a55; --signal-soft:#e6f0ef;
  --sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,"JetBrains Mono","DejaVu Sans Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme=light]) {
  --paper:#15171b; --ink:#e8eaee; --ink-2:#a2a9b5; --ink-3:#727984;
  --rule:#2a2e36; --rule-2:#3a3f49; --field:#121418; --mount:#1f232a;
  --signal:#5cbbaf; --signal-soft:#17312f;
} }

* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink);
  font:15px/1.6 var(--sans); font-variant-numeric:tabular-nums; }
.wrap { max-width:76rem; margin:0 auto; padding:0 1.5rem; }

/* ---- header: the register's identification block ---- */
header { position:sticky; top:0; z-index:5; background:var(--paper);
  border-bottom:1px solid var(--rule); padding:1.5rem 0 1.1rem; }
h1 { font-size:1.25rem; font-weight:600; letter-spacing:-.02em; margin:0; }
.ledger { display:flex; gap:1.75rem; flex-wrap:wrap; margin:.6rem 0 0;
  font-size:.8125rem; color:var(--ink-3); }
.ledger b { font-family:var(--mono); font-weight:500; color:var(--ink);
  margin-right:.35rem; }

.controls { display:flex; gap:.5rem; flex-wrap:wrap; margin-top:1.1rem;
  align-items:center; }
input[type=search], select { font:14px/1.45 var(--sans); color:var(--ink);
  background:var(--field); border:1px solid var(--rule-2); border-radius:3px;
  padding:.4rem .6rem; }
input[type=search] { flex:1 1 15rem; min-width:0; }
button { font:13.5px/1 var(--sans); color:var(--ink); background:var(--field);
  border:1px solid var(--rule-2); border-radius:3px; padding:.45rem .8rem;
  cursor:pointer; white-space:nowrap; }
button:hover { border-color:var(--signal); color:var(--signal); }
button.on { background:var(--signal); border-color:var(--signal); color:var(--paper); }
:focus-visible { outline:2px solid var(--signal); outline-offset:1px; }

.banner { margin:1.1rem 0 0; padding-left:.75rem; border-left:2px solid var(--rule-2);
  font-size:.8125rem; color:var(--ink-2); max-width:52rem; }
.banner b { font-weight:600; color:var(--ink); }

/* ---- plates ---- */
.grid { display:grid; gap:2.25rem 1.75rem; padding:2rem 0 5rem;
  grid-template-columns:repeat(auto-fill,minmax(15rem,1fr)); }
.card { display:flex; flex-direction:column; min-width:0; }

/* 4/5 is the shape of most items; "contain" shows the whole frame rather than
   cropping part of it away, which is what an archive owes its contents. */
.media { position:relative; aspect-ratio:4/5; background:var(--mount);
  overflow:hidden; border-radius:2px; box-shadow:inset 0 0 0 1px rgba(0,0,0,.05); }
.media video, .media img { position:absolute; inset:0; width:100%; height:100%;
  object-fit:contain; display:block; }
.nomedia { position:absolute; inset:0; display:flex; align-items:center;
  justify-content:center; color:var(--ink-3); font-size:.75rem; }

.head { display:flex; justify-content:space-between; align-items:baseline;
  gap:.5rem; margin-top:.7rem; }
.author { color:var(--ink); font-weight:600; font-size:.875rem; text-decoration:none;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.author:hover { color:var(--signal); }
.kind { font-size:.75rem; color:var(--ink-3); white-space:nowrap; }

.dates { display:flex; flex-direction:column; gap:.05rem; margin-top:.3rem;
  font-size:.75rem; color:var(--ink-3); }
.dates span { display:flex; gap:.4rem; }
.dates i { font-style:normal; font-family:var(--mono); color:var(--ink-2);
  margin-left:auto; }
.approx i { font-family:var(--sans); font-style:italic; }

.colls { display:flex; gap:.3rem; flex-wrap:wrap; margin-top:.5rem; }
.colls:empty { display:none; }
.coll { font-size:.6875rem; padding:.05rem .4rem; border-radius:2px;
  background:var(--signal-soft); color:var(--signal); }

.caption { margin:.5rem 0 0; font-size:.8125rem; line-height:1.5; color:var(--ink-2);
  overflow-wrap:anywhere; display:-webkit-box; -webkit-line-clamp:4;
  -webkit-box-orient:vertical; overflow:hidden; }
.tags { margin-top:.4rem; font-size:.6875rem; color:var(--ink-3);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.empty { padding:5rem 0; text-align:center; color:var(--ink-3); font-size:.875rem; }
@media (max-width:30rem) { .grid { grid-template-columns:1fr; } }
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


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count <= 1 else (plural or singular + "s")


def _card(record: dict, lang: str) -> str:
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
        preview = f'<div class="nomedia">{esc(i18n.t("cat_no_media", lang))}</div>'

    caption = record.get("caption") or ""

    # The save date comes only from the official export. Until it is imported,
    # all we have is the order -- better to say so than to show a bare "rank 3"
    # that explains nothing.
    saved_at = (record.get("saved_at") or "")[:10]
    if saved_at:
        saved_html = (f'<span>{esc(i18n.t("cat_saved_on", lang))}'
                      f'<i>{esc(saved_at)}</i></span>')
    else:
        rank = (record.get("saved_rank") or 0) + 1
        position = (f"{rank}{'st' if rank == 1 else 'nd' if rank == 2 else 'rd' if rank == 3 else 'th'}"
                    if lang == "en" else ("1er" if rank == 1 else f"{rank}e"))
        saved_html = (f'<span class="approx" title="{esc(i18n.t("cat_rank_hint", lang))}">'
                      f'{esc(i18n.t("cat_saved_rank", lang))}'
                      f'<i>{esc(i18n.t("cat_rank_value", lang, position=position))}</i></span>')

    collections = [c for c in (record.get("collections") or []) if c]
    if not collections and record.get("collection"):
        collections = [record["collection"]]
    collection_html = "".join(
        f'<span class="coll">{esc(c)}</span>' for c in collections[:3])
    collection_data = "|" + "|".join(collections) + "|" if collections else ""

    haystack = " ".join([
        record.get("author") or "", caption,
        " ".join(record.get("hashtags") or []),
        " ".join(collections), record.get("kind") or "",
    ]).lower()
    tags = " ".join(f"#{esc(t)}" for t in (record.get("hashtags") or [])[:6])

    return f"""
      <article class="card" data-kind="{esc(record.get('kind'))}"
               data-collections="{esc(collection_data)}"
               data-search="{esc(haystack)}">
        <div class="media">{preview}</div>
        <div class="head">
          <a class="author" href="{esc(record.get('url'))}" target="_blank"
             rel="noopener">@{esc(record.get('author'))}</a>
          <span class="kind">{esc(record.get('kind'))}</span>
        </div>
        <div class="dates">
          <span>{esc(i18n.t("cat_posted", lang))}<i>{esc((record.get('posted_at_utc') or '')[:10])}</i></span>
          {saved_html}
        </div>
        <div class="colls">{collection_html}</div>
        <p class="caption">{esc(caption)}</p>
        <div class="tags">{tags}</div>
      </article>"""


def write_html_catalog(archive: Path, records: list[dict],
                       lang: str = i18n.DEFAULT_LANGUAGE) -> Path:
    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    total = paths.human_bytes(sum(r.get("bytes_total", 0) for r in records), lang)
    dated = sum(1 for r in records if r.get("saved_at"))
    kinds = sorted({r.get("kind") or "?" for r in records})
    collections = sorted({name for r in records for name in (r.get("collections") or [])
                          if name} | {r["collection"] for r in records if r.get("collection")})
    undated = len(records) - dated

    filters = "".join(
        f'<button data-filter="{esc(k)}">{esc(k)}</button>' for k in kinds
    )
    L = lambda key, **kw: esc(i18n.t(key, lang, **kw))  # noqa: E731
    options = "".join(
        f'<option value="{esc(c)}">{esc(c)}</option>' for c in collections
    )
    if undated or not collections:
        options += f'<option value="__none__">{L("cat_no_collection")}</option>'

    banner = ""
    if undated:
        banner = ('<p class="banner">'
                  + i18n.t("cat_banner", lang, count=undated) + "</p>")
    cards = "".join(_card(r, lang) for r in records)
    body = cards or f'<p class="empty">{L("cat_empty")}</p>'

    target = archive / "index.html"
    target.write_text(f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{L("cat_title")}</title>
<style>{PAGE_CSS}</style></head><body>
<header><div class="wrap">
  <h1>{L("cat_title")}</h1>
  <div class="ledger">
    <span><b id="count">{len(records)}</b>{L("cat_of", total=len(records))}</span>
    <span><b>{total}</b>{L("cat_on_disk")}</span>
    <span><b>{dated}</b>{L("cat_dates")}</span>
    <span><b>{len(collections)}</b>{L("cat_collections")}</span>
  </div>
  <div class="controls">
    <input type="search" id="q" placeholder="{L("cat_search")}">
    <select id="coll"><option value="">{L("cat_all_collections")}</option>{options}</select>
    <button data-filter="" class="on">{L("cat_all")}</button>{filters}
  </div>
  {banner}
</div></header>
<main class="wrap"><div class="grid" id="grid">{body}</div></main>
<script>{PAGE_JS}</script></body></html>
""", encoding="utf-8")
    return target


def build(archive: Path, metadata_dir: Path,
          saved_dates: dict[str, dict] | None = None,
          lang: str = i18n.DEFAULT_LANGUAGE) -> dict[str, Any]:
    """Rebuild the three outputs. Returns a summary for the caller."""
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
        "html": str(write_html_catalog(archive, records, lang)),
    }
