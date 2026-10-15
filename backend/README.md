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

`--sources` takes any of `youtube,wikipedia,yonhap,soompi,koreaboo` (plus `naver` when it is configured, see
below). Excluded sources show as `disabled`.
Exit codes for `run`: `0` means ok or partial, `1` means a config error or crash, `2` means every source failed
(an existing `trends.json` is then kept).

## Environment

| Variable | Default | Notes |
|---|---|---|
| `SP_CONTACT` | `https://github.com/taejunoh/seoulpulse` | Contact URL or email for the User-Agent `SeoulPulse/0.1 (+…)`. The default identifies this project's repo; forks and other deployments should set their own. Without a URL or email, Wikimedia treats requests as unidentified and limits them to about 10/min instead of 200/min. Wikipedia backfill then spreads over several runs, and its status reads `error`/`degraded` until it catches up. |
| `SP_DB_PATH` | `var/seoulpulse.db` | SQLite (WAL). Relative paths resolve against `backend/`. |
| `SP_PUBLIC_DIR` | `../public` | where `trends.json` is written |
| `SP_INTERVAL_SEC` | `1800` | loop interval and `next_update_hint` |
| `SP_TOP_N` | `20` | items published (max 20) |
| `SP_RANK_COMPARE_HOURS` | `6` | rank-change basis age; `0` compares with the previous run |
| `YOUTUBE_API_KEY` | unset | optional YouTube Data API v3 pass (see below): finds uploads beyond RSS's latest 15, exact view counts, keeps tracking videos after they leave the feed |
| `SP_YOUTUBE_DAILY_UNITS` | `9000` | daily unit budget for that key (0–10000; the free quota is 10,000/day per Google Cloud project). Lower it if the project is shared |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | unset | optional Naver DataLab "Korean search spike" signal. Both must be set; either alone does nothing |
| `SP_NAVER_ENDPOINT` | `auto` | `auto` tries NAVER API HUB, then the legacy developers.naver.com endpoint; `hub` or `developers` pins one |
| `SP_WHY_LLM` / `ANTHROPIC_API_KEY` / `SP_LLM_MODEL` | `0` / unset / `claude-haiku-5-5` | optional LLM "why" lines for the top 10. Install the extra with `uv sync --extra llm`. The LLM never sees headlines. |

## Optional keys

Every key is optional and off when absent; a keyless run produces exactly the same `trends.json` as before. Keys
are sent only as request headers and never appear in logs, errors, the database or `trends.json`.

### YouTube Data API (`YOUTUBE_API_KEY`)

What it adds (spec §3.3): each channel's uploads playlist is read about every 6 hours (50 uploads instead of
RSS's 15), every tracked video gets an exact view count each run (replacing the coarse, cached RSS count), videos
stay tracked after they leave the feed, and Shorts that RSS never showed are filtered by duration and title.

Quota: 1 unit per call; at most 200 units per run and `SP_YOUTUBE_DAILY_UNITS` (9,000) per Pacific-time day,
tracked in the database. A normal day uses about 1,500–3,000 units. When the budget runs out the run falls back
to RSS values, and the YouTube source `detail` says so (`API 41 units (2310/9000 today)`, `budget cap`,
`quota exhausted`, `key rejected`).

To get a key:
1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project (one dedicated to SeoulPulse
   keeps the 10,000-unit quota to itself).
2. APIs & Services → Library → **YouTube Data API v3** → Enable.
3. APIs & Services → Credentials → Create credentials → **API key**.
4. Edit the key: API restrictions → Restrict key → select only **YouTube Data API v3**. Leave application
   restrictions at "None" for GitHub Actions (runner IPs change), or restrict by IP if you run it on your own host.
5. Set it as the `YOUTUBE_API_KEY` environment variable (or GitHub secret).

### Naver DataLab search trend (`NAVER_CLIENT_ID` + `NAVER_CLIENT_SECRET`)

What it adds (spec §3.7, §5.3): the daily Naver search interest of each act's Korean keywords, turned into a
z-score against its own previous 14 days. It adds up to 10 points to the score, can set `korea_first` (a Korean
search spike while English Wikipedia is quiet), can produce the why line `Korean searches 2.3x normal`, and adds a
6th `sources[]` entry, "Naver DataLab". Keywords default to each artist's Hangul aliases; an optional
`search: [...]` list in `data/artists.yaml` overrides them for ambiguous names.

Where to get credentials (status as of 2026-10-14):
- **New users: NAVER API HUB on Naver Cloud Platform.** developers.naver.com stopped accepting new
  applications for the Search Trend API on 2026-07-31. Sign in to the [NCP console](https://console.ncloud.com/),
  apply for **NAVER API HUB** (Services → Application Service → NAVER API HUB), register an *Application* that
  includes **검색어 트렌드 (Search Trend)**, and copy that Application's Client ID and Client Secret (Application →
  API 관리 → 인증 정보). An NCP account IAM access key does not work. The HUB allows 50,000 calls/month and is
  free for now; Naver has said metering may start later. SeoulPulse makes about 17 calls a day.
- **Existing developers.naver.com keys** (applied for by 2026-07-30, with the DataLab search trend API enabled)
  keep working until 2027-06-30 with `SP_NAVER_ENDPOINT=auto` or `developers`. The two kinds of credentials are
  not interchangeable.

Limits we enforce: at most 20 calls per run and 300 per KST day. Data is daily, so an act is re-requested only
when yesterday's (KST) value is not in yet, at most every 2 hours.

Data handling: Naver's ratio series is only held in memory. The database keeps one row per act with our derived
values (day, z-score, ratio to its 14-day mean) for at most 7 days, and these values are never sent to the
optional LLM. This follows the strictest reading of Naver's September 2026 API terms (no AI input, limited
storage), which were written for the Search API and may not even apply to DataLab.

Run state lives in `backend/var/` (gitignored). Delete it to start cold. The first run ranks from lifetime
view counts and shows `cold_start: true`. Runs at least 25 minutes apart produce real velocity pairs, after
which `cold_start` turns `false`.

## Scheduling

- **cron** (every 30 minutes):
  `*/30 * * * * cd /path/to/repo/backend && SP_CONTACT=https://… /path/to/uv run seoulpulse run >> var/cron.log 2>&1`.
  Overlapping runs are safe: a second run finds the `var/run.lock` flock held and exits 0.
- **GitHub Actions**: `.github/workflows/ticker.yml` (one long job, re-dispatching itself every ~5h30m) dispatches `pipeline.yml` every 30 minutes, because the `schedule` trigger is unreliable here; `pipeline.yml` also keeps a backstop cron `7,22,37,52 * * * *`. It
  restores `backend/var` (and `public/`) from the Actions cache, runs the pipeline and deploys `public/` to
  GitHub Pages. Set the repo variable `SP_CONTACT`, optionally the secrets `YOUTUBE_API_KEY`,
  `NAVER_CLIENT_ID` and `NAVER_CLIENT_SECRET` (and the variables `SP_YOUTUBE_DAILY_UNITS`, `SP_NAVER_ENDPOINT`),
  and set Pages → Source to "GitHub Actions". `.github/workflows/backend-ci.yml` runs the tests on push and PR.

## Data rules

Only the headline text, link, source, publish time and our own metrics are stored. RSS `description` and
`content:encoded` are never read. Korean headlines are shown untranslated. YouTube API data is kept at most 30
days without a refresh. Naver data: only derived values, at most 7 days, never sent to an LLM.
