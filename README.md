# SeoulPulse

Korea-first K-pop trends for US fans: an iOS widget plus a small app. A keyless Python pipeline watches YouTube
channel feeds, Wikipedia pageviews and Yonhap/Soompi/Koreaboo headlines, scores artist momentum (flagging when
Korea moves before English-language coverage), and writes one static file, `public/trends.json`. The iOS app
and WidgetKit extension read that file. No server, no accounts, no API keys required.

**Live feed:** <https://taejunoh.com/seoulpulse/trends.json> (refreshed every 30 minutes). The iOS app defaults to
this URL; for development, point Settings at a local server such as `http://localhost:8787/trends.json`.

**Site:** <https://taejunoh.com/seoulpulse/> · [Privacy Policy](https://taejunoh.com/seoulpulse/privacy/) ·
[Support](https://taejunoh.com/seoulpulse/support/) · [Issues](https://github.com/taejunoh/seoulpulse/issues)

## Architecture

```
 YouTube channel RSS ─┐
 Wikipedia pageviews ─┤                              ┌─> iOS app (Home, Detail, Settings)
 Yonhap RSS ──────────┼─> backend pipeline ─> SQLite │
 Soompi RSS ──────────┤   (fetch, score, rank)  │    │
 Koreaboo RSS ────────┘                         v    │
                                     public/trends.json ──(HTTPS)──┴─> WidgetKit widget
                                     (static host: GitHub Pages)
```

Each source is fetched independently; one failing source never blocks a run, and its status is published in
`trends.json` under `sources`. If every source fails, the previous `trends.json` is kept.

## Quickstart

Backend (Python 3.12+, [uv](https://docs.astral.sh/uv/)):

```sh
cd backend
uv sync
export SP_CONTACT="https://your-site.example/seoulpulse"   # or an email; see "Environment variables"
uv run seoulpulse dev          # serves http://127.0.0.1:8787/trends.json and re-runs every 30 min
                               # add --host 0.0.0.0 to reach it from a physical iPhone on your LAN (also on `serve`)
uv run pytest                  # optional: all tests, offline
```

iOS (iOS 18+, Xcode, [xcodegen](https://github.com/yonaskolb/XcodeGen)):

```sh
cd ios
xcodegen generate
open SeoulPulse.xcodeproj      # run the SeoulPulse scheme on a simulator
```

Or build from the command line, as in `ios/README.md`. The app reads the live feed
`https://taejunoh.com/seoulpulse/trends.json` by default; to develop against a local backend, set
`http://localhost:8787/trends.json` in Settings. If the feed is unreachable, the app falls back to its cache, then to bundled sample data, and labels
which one it is showing. To serve the sample payload without running the pipeline:
`uv run seoulpulse serve --seed-example`.

## Repo layout

| Path | What |
|---|---|
| `backend/` | Python package `seoulpulse` (fetchers, SQLite store, scoring, publish, CLI), seed rosters in `backend/data/`, tests |
| `ios/` | xcodegen spec `project.yml`, app, widget extension, shared code, unit and snapshot tests |
| `site/` | committed static pages (landing, `privacy/`, `support/`); `pipeline.yml` copies them into `public/` on every deploy |
| `public/` | output directory; `trends.json` is written here (gitignored except `.gitkeep`) |
| `docs/` | `SPEC.md`, the JSON contract `trends.schema.json`, a valid `trends.example.json`, `DEPLOY.md` |
| `.github/workflows/` | `pipeline.yml` (cron run and GitHub Pages deploy), `backend-ci.yml` (tests on push and PR), `social-draft.yml` (daily draft issue) |

## Data sources and legal stance

| Source | What we read |
|---|---|
| YouTube | channel RSS feeds (about 90 channels); optional Data API for view counts |
| Wikipedia | daily pageviews (en and ko) from the Wikimedia REST API |
| Yonhap, Soompi, Koreaboo | RSS headlines |

We store and show only the headline text, the link, the source name, the publish time, and metrics we compute
ourselves. Article bodies, RSS `description` and `content:encoded` are discarded at parse time and never stored,
translated, summarized, or sent to an LLM. Korean headlines are shown untranslated. This is a fan-made project
and is not affiliated with any artist or label. Details: `docs/SPEC.md` section 0.

## Environment variables

All optional; read in `backend/seoulpulse/config.py`. Relative paths resolve against `backend/`.

| Variable | Default | Notes |
|---|---|---|
| `SP_CONTACT` | `https://github.com/taejunoh/seoulpulse` | Contact URL or email placed in the User-Agent. The default identifies this project's repo; forks and other deployments should set their own. Wikimedia throttles anonymous User-Agents to about 10 requests/min instead of 200, which slows the Wikipedia backfill and can leave its status at `error`/`degraded` for several runs. |
| `YOUTUBE_API_KEY` | unset | Tracks view counts after videos leave the 15-item RSS window. |
| `SP_WHY_LLM` | `0` | `1` enables LLM-written "why" lines for the top 10. Also needs `ANTHROPIC_API_KEY` and `uv sync --extra llm`. In GitHub Actions, set the `SP_WHY_LLM` variable to `1` and add the `ANTHROPIC_API_KEY` secret (see `docs/DEPLOY.md`). The LLM never sees headlines. |
| `ANTHROPIC_API_KEY` | unset | Used only when `SP_WHY_LLM=1`. |
| `SP_LLM_MODEL` | `claude-haiku-5-5` | Model for the "why" lines. |
| `SP_DB_PATH` | `var/seoulpulse.db` | SQLite state (WAL). |
| `SP_PUBLIC_DIR` | `../public` | Where `trends.json` is written. |
| `SP_INTERVAL_SEC` | `1800` | Loop interval and `next_update_hint` (minimum 60). |
| `SP_TOP_N` | `20` | Items published (1 to 20). |
| `SP_RANK_COMPARE_HOURS` | `6` | Age of the rank-change baseline; `0` compares with the previous run. |

## Stage-0 validation (social drafts)

Before investing in distribution, test whether the rankings are worth sharing. After a run has produced
`public/trends.json`:

```sh
cd backend
uv run seoulpulse social                 # X and Threads drafts for the top 5
uv run seoulpulse social --top 3 --format x
```

The command only prints text. Posting is manual; the project never posts anything. See `docs/SPEC.md` section 9.

**Daily draft issue.** `.github/workflows/social-draft.yml` runs at 13:00 UTC (9am US Eastern), downloads the live
feed, runs `seoulpulse social`, and opens a GitHub issue titled `Social draft · YYYY-MM-DD` (label `social-draft`)
with the X and Threads drafts in copyable code blocks. It closes the previous open draft first and adds a warning
while the feed is still calibrating (`cold_start`). Copy the text and post it yourself; nothing is posted
automatically. Trigger it on demand from the Actions tab (`workflow_dispatch`).

## More

- `docs/SPEC.md`: product and engineering spec (scoring, storage, JSON contract, iOS design)
- `docs/DEPLOY.md`: going live with GitHub Pages, TestFlight, App Store Connect URLs, alternative hosting
- `backend/README.md`: CLI reference, scheduling, run state
- `ios/README.md`: build, test, data flow, deep links
