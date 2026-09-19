# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Telegram bot (aiogram 3, long polling) that casts a Vedic horary chart (prashna kundali) for the
moment a question arrives and sends it to Groq for interpretation. Russian-language domain: code,
comments, identifiers in astro modules, and all user-facing strings are Russian. Keep new strings
Russian too.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # fill TELEGRAM_TOKEN, GROQ_API_KEY; set DB_PATH=./data/prashna.sqlite3
python run.py                 # entry point; validate() exits if required env vars are missing
```

Astro-only check (no Telegram, no Groq):

```bash
python3 -c "
from datetime import datetime, timezone
from app.astro.chart import build_chart
from app.astro.prashna import render_chart_text, judgment_factors
c = build_chart(datetime.now(timezone.utc), 56.5, 84.97, 'Asia/Tomsk', 'Томск', 10)
print(render_chart_text(c)); print(); print('\n'.join(judgment_factors(c)))
"
```

Checks live in `.github/workflows/deploy.yml` → job `check`, which runs on every PR into `main`
and again on push to `main` before the deploy job: `ruff check .`, `ruff format --check .`,
`python -m compileall -q app run.py`, `pytest -q`. A failure blocks deploy. Run the same locally:

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check . && pytest -q
```

Tests live in `tests/`, not colocated (the package is `app/`, tests are a separate tree).
`tests/conftest.py` points `app.db` at a fresh SQLite file under `tmp_path` for every test —
`DB_PATH` in env won't work, since `app/config.py` reads env at import time.
`tests/test_chart.py` pins reference positions for a fixed moment: a diff there means the
calculation changed, not that the data drifted.

## Architecture

Data flows one way: **Telegram → geo → chart → factors → LLM → SQLite → Telegram.**

- `app/bot.py` — all aiogram handlers registered in one `register(dp)` function; `run()` wires
  logging, DB init, and polling. The catch-all `F.text & ~F.text.startswith("/")` handler is the
  prashna path: rate-limit check → `detect_house` → `build_chart` (in `asyncio.to_thread`, since
  pyswisseph is blocking) → send short chart → `llm.interpret` → persist → send answer in
  3800-char chunks. Chart is sent to the user *before* the LLM call, so an LLM outage still
  leaves the user with a chart.
- `app/astro/chart.py` — pure computation via Swiss Ephemeris. Sidereal zodiac, whole-sign houses
  (`swe.houses_ex(..., b"W", ...)`), `FLG_MOSEPH` so **no ephemeris data files are needed**.
  Produces one `PrashnaChart` holding planets, vargas D1/D2/D3/D4/D7/D9/D10/D12, aspects, arudhas,
  Vimshottari dasha, panchanga, house lords.
- `app/astro/prashna.py` — interpretation *inputs*: `detect_house` (keyword scoring over
  `C.HOUSE_KEYWORDS`, weighted by matched-word length, falls back to house 1),
  `judgment_factors` (classical yes/no factors as Russian sentences), and the three renderers
  (`render_chart_text` for LLM + archive, `render_short` for the Telegram summary).
- `app/astro/constants.py` — every astrological table (signs, lords, nakshatras, dignities,
  combustion orbs, special aspects, Vimshottari years, house meanings/keywords, panchanga names).
  Tuning bhava detection or dignity rules means editing this file, not the logic.
- `app/llm.py` — Groq via the OpenAI-compatible `/chat/completions` endpoint over raw `httpx`
  (no SDK). Retries 3×, honors `retry-after` on 429, optional `LLM_PROXY`.
- `app/db.py` — plain `sqlite3` with a `@contextmanager conn()` that commits on exit and sets
  WAL. `SCHEMA` is idempotent DDL describing the *current* shape; `MIGRATIONS` is the catch-up
  path for databases that already exist, tracked in `PRAGMA user_version` (entry `i` bumps it to
  `i + 1`). `init()` runs `SCHEMA`, stamps a brand-new file with `len(MIGRATIONS)` — on an empty
  DB the steps are already contained in `SCHEMA`, so replaying them would duplicate columns —
  then calls `migrate()` for the rest. A pending migration triggers `deploy/backup.sh` first and
  aborts startup if that fails: the change is one-way, and a code rollback would meet a schema
  that moved on. Add steps, never edit a released one (servers that applied it won't re-run it),
  and keep them backward compatible so the previous version can still read the DB.
- `app/geo.py` — Nominatim geocoding, results cached in the `geocache` table, throttled to ≤1
  req/sec by a module-level `asyncio.Lock` + `_last_call`. `timezonefinder` derives the tz offline.

**Core invariant: the LLM computes nothing.** Every position, varga, arudha, dasha, and panchanga
value is produced by code; Groq only interprets the finished chart. The system prompt in
`app/llm.py` states this explicitly — don't move calculation into the prompt.

Config is a single frozen `Settings` dataclass in `app/config.py`, read from env/`.env` at import.
Because defaults are evaluated at class-definition time, tests or scripts must set env vars before
importing `app.config`.

## Deploy

`main` is the deploy branch: push (or merged PR) → `check` job → rsync to
`/opt/prashna-bot/incoming` → `sudo deploy/apply.sh` on the VPS, which backs up the current code,
swaps it in, reinstalls deps, restarts the systemd unit, and **auto-rolls back** if
`systemctl is-active` fails. `deploy/install.sh` is first-time VPS setup; `deploy/update.sh` is
the manual equivalent of `apply.sh`. `.env` and `data/` live only on the server and are excluded
from rsync.

The DB is backed up by `prashna-backup.timer` (daily ~03:30, `Persistent=true`), which runs
`deploy/backup.sh`: `sqlite3 .backup` for a consistent snapshot under WAL, `PRAGMA integrity_check`
on the snapshot, `gzip -9`, then prune archives older than 14 days. All three deploy scripts
install the unit files and `systemctl enable --now` the timer, so a new unit reaches the VPS with
the next deploy. The archive is then pushed off-site with `sendDocument` to a private Telegram
channel (`BACKUP_CHAT_ID` in `.env`, same bot token, no extra dependency). Without that variable
the script still succeeds and just leaves the archive on the VPS; a failed upload exits non-zero
so the unit lands in `failed`. Bot API caps documents at 50 MB — past that the script fails loudly
rather than silently skipping, and the fallback is `rclone` to S3 (ROADMAP §4.4).

`.gitattributes` forces LF (`* text=auto eol=lf`, `*.sh text eol=lf`) — the shell scripts break on
the VPS with CRLF, so never commit CRLF line endings.
