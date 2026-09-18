# AGENTS.md

Telegram bot (aiogram 3, long polling) that casts a Vedic horary chart (prashna kundali) for the
moment a question arrives and sends it to Groq for interpretation. Single small Python app — no
framework, no web server. Full context: `CLAUDE.md` (architecture, deploy) and `README.md`
(ops, env vars). If `CLAUDE.md` and this file conflict, `CLAUDE.md` wins — it is maintained.

## Language

The whole domain is Russian: code comments, identifiers in `app/astro/*`, and all user-facing
strings. Keep new strings Russian. User messages and LLM answers are rendered with Telegram HTML
parse mode (set globally in `run()`), so **any user-provided or DB text inserted into a reply must
go through `html.escape()`** — new handlers must do this too or the message fails to send.

## Setup & run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill TELEGRAM_TOKEN, GROQ_API_KEY; DB_PATH=./data/prashna.sqlite3
python run.py                 # entry point; validate() exits if required env vars are missing
```

- `requirements.txt` uses **lower bounds, not pins, on purpose**: on fresh Python (3.13+ incl. the
  3.15 alpha on this machine) old versions build from source and fail — new ones ship wheels. Don't
  pin exact versions. CI runs Python 3.12.
- `.env` and `data/` are gitignored; they exist only on the server.

## Verification — there is NO test suite and NO linter

The only automated gate is `.github/workflows/deploy.yml` → job `check`. **Run it locally before
pushing or a merge into `main` will fail and block deploy:**

```bash
python -m compileall -q app run.py
python3 -c "
from datetime import datetime, timezone
from app.astro.chart import build_chart
from app.astro.prashna import render_chart_text, judgment_factors, detect_house
house = detect_house('получу ли я оффер на новую работу')   # must be 10
c = build_chart(datetime.now(timezone.utc), 56.5, 84.97, 'Asia/Tomsk', 'Томск', house)
assert len(c.planets) == 9 and len(c.vargas) == 8
assert 'Лагна:' in render_chart_text(c) and len(judgment_factors(c)) > 8
"
```

Astro-only sanity check (no Telegram/Groq/network): the same `build_chart` +
`render_chart_text`/`judgment_factors` calls work offline.

## Architecture

One-way flow: **Telegram → geo → chart → factors → LLM → SQLite → Telegram.**

- `app/bot.py` — every handler registered in one `register(dp)` function. The prashna path is the
  catch-all `F.text & ~F.text.startswith("/")` handler. `build_chart` (pyswisseph) is CPU-blocking
  and **must be called via `asyncio.to_thread`**, never directly in an async handler.
- `app/astro/constants.py` — every astrological table. Tuning house/keyword detection or dignity
  rules means editing this file, not the logic in `chart.py`/`prashna.py`.
- `app/astro/chart.py` — pure computation, Swiss Ephemeris with `FLG_MOSEPH` (no ephemeris files).
- `app/astro/prashna.py` — `detect_house`, `judgment_factors`, the three renderers.
- `app/llm.py` — Groq over raw `httpx` (OpenAI-compatible endpoint, no SDK).
- `app/db.py` — plain `sqlite3` via `@contextmanager conn()` (commits on exit, WAL). Schema is
  idempotent DDL executed by `init()`; **no migrations** — schema changes must stay backward
  compatible or add a new table.
- `app/geo.py` — Nominatim, throttled to ≤1 req/sec by a module-level lock, cached in SQLite.
- `app/config.py` — frozen `Settings` dataclass, defaults evaluated **at import time**; scripts or
  tests must set env vars before importing `app.config`.

## Hard constraints

- **The LLM computes nothing.** Every position, varga, arudha, dasha, panchanga is produced by
  code; Groq only interprets the finished chart (the system prompt states this explicitly). Never
  move calculation into the prompt.
- The chart is sent to the user **before** the LLM call, so an LLM outage still leaves a chart.
  Preserve this ordering; answers are chunked at 3800 chars.
- Deploy shell scripts break with CRLF — `.gitattributes` forces LF (`* text=auto eol=lf`). Never
  commit CRLF line endings.

## Deploy / git workflow

- `main` is the deploy branch: **merging a PR into `main` auto-deploys** to the VPS (GitHub
  Actions → rsync to `/opt/prashna-bot/incoming` → `sudo deploy/apply.sh`, which backs up, swaps
  code, reinstalls deps, restarts systemd, and auto-rolls back if the service fails to start).
- Work on a feature branch → PR; only a merge to `main` triggers deploy. `.env` and `data/` never
  touch the repo (excluded from rsync) — don't add secrets or DB dumps.
- systemd unit caps memory at `MemoryMax=512M` — don't add heavy dependencies.
- See `deploy/GITHUB.md` for the CD setup/rollback details.
