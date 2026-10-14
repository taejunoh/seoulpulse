# SeoulPulse backend

A keyless Python pipeline that collects YouTube channel RSS, Wikipedia pageviews and Yonhap/Soompi/Koreaboo
headlines. It scores Korea-first K-pop momentum and writes one static file, `../public/trends.json`.
The contract is `docs/SPEC.md` together with `docs/trends.schema.json`.

## Setup

```sh
cd backend
uv sync                      # Python ≥3.12 (.python-version pins 3.14)
export SP_CONTACT="https://your-site.example/seoulpulse"   # strongly recommended, see below
```

## Commands (run in `backend/`)

| Command | What it does |
|---|---|
| `uv run seoulpulse run [--now ISO] [--sources a,b] [--no-publish]` | one pipeline run → `../public/trends.json` |
| `uv run seoulpulse run --loop [--interval 1800]` | run forever; a failed run never stops the loop |
| `uv run seoulpulse serve [--port 8787] [--seed-example]` | serve `../public/` on `127.0.0.1` with `Cache-Control: no-cache` |
| `uv run seoulpulse dev` | `serve` in a thread + `run --loop` |
| `uv run seoulpulse social [--input PATH] [--top 5] [--format x\|threads\|both]` | print X/Threads drafts (we never post) |
| `uv run seoulpulse resolve-channels [--force]` | resolve `@handles` → `data/channels.lock.json` (title-checked) |
| `uv run seoulpulse validate-seed [--online]` | roster checks; `--online` also verifies channel ids and canonical wiki titles |
| `uv run pytest` | all tests, offline (real sockets are blocked) |

`--sources` takes any of `youtube,wikipedia,yonhap,soompi,koreaboo`. Excluded sources show as `disabled`.
Exit codes for `run`: `0` means ok or partial, `1` means a config error or crash, `2` means every source failed
(an existing `trends.json` is then kept).

## Environment

| Variable | Default | Notes |
|---|---|---|
| `SP_CONTACT` | generic project string | Contact URL or email for the User-Agent `SeoulPulse/0.1 (+…)`. **Set it.** Without a URL or email, Wikimedia treats requests as unidentified and limits them to about 10/min instead of 200/min. Wikipedia backfill then spreads over several runs, and its status reads `error`/`degraded` until it catches up. |
| `SP_DB_PATH` | `var/seoulpulse.db` | SQLite (WAL). Relative paths resolve against `backend/`. |
| `SP_PUBLIC_DIR` | `../public` | where `trends.json` is written |
| `SP_INTERVAL_SEC` | `1800` | loop interval and `next_update_hint` |
| `SP_TOP_N` | `20` | items published (max 20) |
| `SP_RANK_COMPARE_HOURS` | `6` | rank-change basis age; `0` compares with the previous run |
| `YOUTUBE_API_KEY` | unset | optional: tracks view counts after videos leave the 15-item RSS window |
| `SP_WHY_LLM` / `ANTHROPIC_API_KEY` / `SP_LLM_MODEL` | `0` / unset / `claude-haiku-5-5` | optional LLM "why" lines for the top 10. Install the extra with `uv sync --extra llm`. The LLM never sees headlines. |

Run state lives in `backend/var/` (gitignored). Delete it to start cold. The first run ranks from lifetime
view counts and shows `cold_start: true`. Runs at least 25 minutes apart produce real velocity pairs, after
which `cold_start` turns `false`.

## Scheduling

- **cron** (every 30 minutes):
  `*/30 * * * * cd /path/to/repo/backend && SP_CONTACT=https://… /path/to/uv run seoulpulse run >> var/cron.log 2>&1`.
  Overlapping runs are safe: a second run finds the `var/run.lock` flock held and exits 0.
- **GitHub Actions**: `.github/workflows/pipeline.yml` runs on `*/30 * * * *` and on `workflow_dispatch`. It
  restores `backend/var` (and `public/`) from the Actions cache, runs the pipeline and deploys `public/` to
  GitHub Pages. Set the repo variable `SP_CONTACT`, optionally the secret `YOUTUBE_API_KEY`, and set Pages →
  Source to "GitHub Actions". `.github/workflows/backend-ci.yml` runs the tests on push and PR.

## Data rules

Only the headline text, link, source, publish time and our own metrics are stored. RSS `description` and
`content:encoded` are never read. Korean headlines are shown untranslated.
