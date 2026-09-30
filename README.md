<h1 align="center">igarchive</h1>

<p align="center">
  <b>Back up your Instagram saved library to your own disk.</b><br>
  The videos, the captions, the accounts — and the date you saved each item.
</p>

<p align="center">
  <img alt="MIT licence" src="https://img.shields.io/badge/licence-MIT-blue">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-3776ab">
  <img alt="macOS and Linux" src="https://img.shields.io/badge/macOS%20%7C%20Linux-lightgrey">
  <img alt="One dependency" src="https://img.shields.io/badge/dependencies-1-brightgreen">
  <img alt="106 tests" src="https://img.shields.io/badge/tests-106-success">
</p>

<p align="center">
  <img src="docs/interface.png" alt="igarchive: the account, the folder, one button" width="600">
</p>

---

## Why

Instagram lets you save posts and reels, but gives you **no way to get them back**.
An account closes, a post disappears, and your library quietly empties.

`igarchive` copies it to your machine once, then just keeps it up to date.

- 🎬 **The video itself** — real `.mp4` files, not just links
- 📅 **The save date** — which no other tool recovers (see below)
- 🗂️ **Your collections** — rebuilt as folders, like in the app
- 🔎 **Readable offline** — a web page, a CSV, JSON
- 🔁 **Resumable** — a crash, a timeout or a block never costs you work already done
- 🔒 **Entirely local** — no third-party service, no AI, no telemetry
- 🧩 **One dependency** — [instaloader](https://instaloader.github.io/); everything else is the Python standard library

Available in **English** and **French**, switchable from the interface.

---

## Install

```bash
git clone https://github.com/pzeina/instagram-archiver.git
cd instagram-archiver
./install.sh
```

Then open the interface:

```bash
igarchive
```

> [!TIP]
> **Request your Instagram export now.** It takes a few hours to 48 hours to arrive, and it
> is the only source of save dates and collections.
> [accountscenter.instagram.com](https://accountscenter.instagram.com/info_and_permissions/dyi/)
> → *Some of your information* → **Saved items** → **JSON** format → all time.
> The backup does not have to wait for it.

---

## The interface

Two settings and one button.

| | |
|---|---|
| **Account** | The accounts you have already connected, with a *connected* indicator. Press **Connect the account** to check the session — that is the only moment Instagram is contacted about it. |
| **Folder** | Where the archive is written, and what it already holds: item count, size, date of the last backup. |
| **Back up** | Starts the backup. While it runs: counters, the item in progress, a stop button. |

Everything else is decided for you, because nobody needs to tune it. The gear icon opens
advanced settings — request pacing, per-pass caps, what gets downloaded — for the rare case
where you do.

A **Details and messages** panel holds the log, the errors and the cookie sign-in, without
putting them in your way.

The official export is read automatically at the start of each backup, from your backup
folder. Drop the `.zip` there and there is nothing else to do.

<details>
<summary><b>If session detection fails</b></summary>

<br>

Open *Details and messages* and paste the `sessionid` cookie from instagram.com. This works
**everywhere**, including on a machine with no browser:

1. Open your browser's developer tools on instagram.com
2. Application → Cookies → `instagram.com` → `sessionid`
3. Copy the value, paste it into the field

Chrome, Safari, Edge and Brave encrypt their cookies; reading them automatically needs one
extra package:

```bash
./install.sh --browsers
```

</details>

---

## What you get

<p align="center">
  <img src="docs/catalogue.png" alt="The catalogue: one plate per item, search and filters" width="700">
</p>

A page you can open in any browser with no connection: videos play directly, and the search
covers accounts, captions and hashtags at once.

```
~/InstagramArchive/
├── index.html          the page above
├── catalog.csv         one row per item — opens straight in Excel or Numbers
├── catalog.json        the same data, complete
├── media/2026/2026-09-22_someone_Dq1/
│   ├── ….mp4           the video
│   ├── ….jpg           thumbnail, and every image of a carousel
│   ├── ….txt           the caption
│   └── ….json          Instagram's raw answer, for verification
├── collections/        your collections, as folders
├── metadata/           one normalised record per item
└── state/              the resume ledger
```

Each record holds: id, link, kind (`reel` / `video` / `image` / `carousel`), account, post
date, **save date**, collection, caption, hashtags, mentions, place, duration, likes,
comments, files and size.

---

## Your collections

`collections/` mirrors your Instagram library. Each entry is a **link**, not a copy: a reel
filed under three collections takes the space of one, and since the links are relative the
archive stays movable from one disk to another.

The tree is rebuilt on every catalogue build, so a collection you delete in the app
disappears here too — without touching any media, because the cleanup only ever removes
links. Items in no collection are grouped under *No collection*.

> Collections come from the official export, like the dates. Before it is imported,
> everything sits under *No collection*.

---

## The save date, and the detour it needs

No single source has everything:

| Source | Gives | Does not give |
|---|---|---|
| **Instagram's official export** | the exact save date, the collections | the videos — it contains *your* posts, not the ones you saved |
| **Your connected account** | the video, the caption, the account, the post date | the save date — only the order |

`igarchive` reads both and merges them by item id. The export can arrive later: records
already written are enriched in place, with nothing re-downloaded.

Until it is imported, the catalogue shows the save **rank** (“3rd in the list”) instead of a
date, and says so at the top of the page.

---

## Command line

Everything the interface does is available in a terminal — so from a scheduled job, or a
machine with no screen.

```bash
igarchive session --user YOUR_HANDLE       # open a session
igarchive dyi                              # find and read the official export
igarchive fetch --dry-run --limit 20       # preview, downloads nothing
igarchive fetch                            # back up
igarchive catalog --open                   # rebuild and open
igarchive status                           # where things stand
igarchive config --set language=fr         # switch language
```

**Later updates** — stops after 20 already-known items, so about a minute:

```bash
igarchive fetch --stop-after-known 20 && igarchive catalog
```

<details>
<summary><b>Scheduling (launchd, systemd, cron)</b></summary>

<br>

**macOS — launchd**

```bash
cat > ~/Library/LaunchAgents/com.perso.igarchive.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.perso.igarchive</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>-lc</string>
    <string>igarchive fetch --stop-after-known 20 &amp;&amp; igarchive catalog</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Day</key><integer>1</integer><key>Hour</key><integer>9</integer></dict>
  <key>StandardOutPath</key><string>/tmp/igarchive.log</string>
  <key>StandardErrorPath</key><string>/tmp/igarchive.err</string>
</dict></plist>
PLIST
launchctl load ~/Library/LaunchAgents/com.perso.igarchive.plist
```

**Linux — systemd**

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/igarchive.service <<'UNIT'
[Unit]
Description=Back up saved Instagram content
[Service]
Type=oneshot
ExecStart=/bin/sh -lc 'igarchive fetch --stop-after-known 20 && igarchive catalog'
UNIT
cat > ~/.config/systemd/user/igarchive.timer <<'UNIT'
[Unit]
Description=Monthly Instagram backup
[Timer]
OnCalendar=monthly
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload && systemctl --user enable --now igarchive.timer
```

`Persistent=true` catches up if the machine was off at the scheduled time.

**Linux — cron**

```cron
0 9 1 * * igarchive fetch --stop-after-known 20 && igarchive catalog
```

</details>

---

## Before you start

> [!IMPORTANT]
> **Do not shorten the pauses.** Instagram rate limits accounts that fire requests back to
> back. The default (3 to 8 seconds) means roughly **one hour per 500 items**. On a large
> library, spread it over several days with `--limit 300`. On a rate-limit error the program
> stops **by itself** and resumes on the next pass.

- **From a home connection**, not a VPN or a rented server: hosting ranges are blocked far faster.
- **What is already gone is gone.** A deleted post, or one whose author went private, is
  recorded as `unavailable` and never retried forever. That is the argument for starting
  early: the official export will always give you the link and the date, never the video.
- **Status.** Retrieving this content automatically is against Instagram's terms of use, even
  for your own library. The concrete risk is a temporary rate limit, which the pauses above
  make unlikely.

---

## Privacy

- **Nothing leaves your machine** apart from the requests to Instagram.
- **No password is ever written to disk**, whichever sign-in method you use.
- The session token lives in `~/.config/igarchive/sessions/` with mode `600`. It is **worth a
  password**. It sits deliberately *outside* the archive, so it does not travel with it onto
  an external disk or a cloud folder.
- The interface listens on `127.0.0.1` only, and every API call needs a token drawn at
  startup: another page open in the same browser cannot drive it.
- The interface never queries Instagram on its own. It checks the session when you press
  **Connect the account**, and at no other time.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| “No sessionid cookie” | You are not signed in to instagram.com in that browser. |
| “…needs browser_cookie3” | `./install.sh --browsers`, or use Firefox, or paste the cookie. |
| Session will not confirm | Instagram is rate limiting the account. Wait a few minutes and press *Connect the account* again. |
| Rate-limit error mid-backup | Normal on a large library. Wait an hour or two: resuming is automatic. |
| Many `unavailable` items | Deleted posts, or accounts gone private. Not recoverable. |
| Port 8765 already in use | Another program has it: `igarchive ui --port 8766`. A second `igarchive` simply opens the running window. |
| Nothing opens | Headless machine: `igarchive ui --no-open`, then open the printed address. |

---

<details>
<summary><b>Development</b></summary>

<br>

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Tests use the standard library only and **never contact Instagram**. They cover the official
export parser (broken encoding, localised fields, collection merging), configuration,
the catalogue (escaping, ordering, CSV, both languages), path resolution on both systems —
checked by forcing the platform — and the interface server, actually started on an ephemeral
port.

| File | Role |
|---|---|
| `paths.py` | locations, per-system differences |
| `config.py` | persisted settings and their validation |
| `i18n.py` | user-facing strings, English and French |
| `session.py` | sign-in — four methods |
| `dyi.py` | official export parser |
| `fetch.py` | downloading, resume ledger |
| `catalog.py` | normalised record, CSV / JSON / HTML output |
| `jobs.py` | background execution |
| `webui.py` | local interface server |
| `cli.py` | command line |

`fetch.py` knows nothing of terminals or interfaces: it reports progress through a callback,
which is what lets both share the same code.

The interface is served over `127.0.0.1` by the standard library rather than built with a GUI
toolkit: Tk is missing from many macOS Python installs and needs a different system package on
every Linux distribution.

Source comments are in French; the user-facing project is English-first.

</details>

---

<p align="center">
  <sub>MIT — see <a href="LICENSE">LICENSE</a>. Independent project, not affiliated with Meta or Instagram.</sub>
</p>
