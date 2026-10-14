# Going live

Step-by-step for publishing SeoulPulse. Nothing here has been run for you: no repo exists yet, no secrets are
set, and the app has never been signed. The default path is GitHub Actions + GitHub Pages (free, no server).

## Current deployment

- Repo: `taejunoh/seoulpulse` (public).
- Hosting: GitHub Pages via Actions, serving <https://taejunoh.com/seoulpulse/trends.json>.
- Repository variable `SP_CONTACT` = `https://github.com/taejunoh/seoulpulse`.
- Schedule: cron every 30 minutes (`.github/workflows/pipeline.yml`).
- The iOS app's built-in default feed URL is the Pages URL above.

## 1. Push to GitHub

```sh
gh repo create seoulpulse --public --source=. --push     # or --private, see below
```

**Public vs private.** `pipeline.yml` runs on `*/30 * * * *`, i.e. 48 runs a day. Each run has two jobs
(`run` and `deploy-pages`), and each job is billed in whole minutes, so expect roughly 100 or more minutes a day,
well over the 2,000 free minutes/month of a private repo. Public repos get unlimited Actions minutes. GitHub Pages
on a private repo also needs a paid plan. Pick one:

- public repo (simplest; the data is public anyway), or
- private repo on a paid plan, or a longer cron interval (edit the `cron:` line in `.github/workflows/pipeline.yml`
  and set `SP_INTERVAL_SEC` to match so `next_update_hint` stays honest), or
- skip Actions and host elsewhere (section 5).

Two GitHub behaviors to know: scheduled workflows are best-effort and can run 5 to 15 minutes late, and on public
repos GitHub disables the schedule after 60 days without repo activity (re-enable it in the Actions tab).

## 2. Configure the repository

Settings, then Secrets and variables, then Actions:

| Kind | Name | Required | Used for |
|---|---|---|---|
| Variable (`vars`) | `SP_CONTACT` | in practice, yes | Contact URL or email in the User-Agent. Without it Wikimedia limits the pipeline to about 10 requests/min. Use your repo or site URL if you do not want to publish an email. |
| Secret | `YOUTUBE_API_KEY` | no | Tracks view counts past the 15-item RSS window. |

```sh
gh variable set SP_CONTACT --body "https://github.com/<you>/seoulpulse"
gh secret set YOUTUBE_API_KEY            # optional; prompts for the value
```

`SP_CONTACT` is a repository **variable**, not a secret: the workflow reads `${{ vars.SP_CONTACT }}`.

Enable Pages: Settings, then Pages, then Source = **GitHub Actions** (the workflow deploys with
`actions/deploy-pages`; do not choose "Deploy from a branch").

### What `pipeline.yml` does

1. Restores `backend/var` (the SQLite history used for velocity, rank change and baselines) and `public/` from the
   Actions cache (`actions/cache/restore`, key `sp-state-<run_id>`, falling back to the newest `sp-state-*`).
   Restoring `public/` is what lets the pipeline keep the last good `trends.json` when every source fails.
2. `uv sync --frozen`, then `uv run seoulpulse run` with `SP_CONTACT` and `YOUTUBE_API_KEY` in the environment.
3. Saves the same two paths back to the cache (`if: always()`), then uploads `public/` as the Pages artifact.
4. A second job, `deploy-pages`, publishes the artifact to the `github-pages` environment.

First deploy: Actions tab, then **pipeline**, then **Run workflow** (`workflow_dispatch`). When it finishes, the
feed is at:

```
https://<owner>.github.io/seoulpulse/trends.json
```

(Project sites live under `/<repo-name>/`; adjust if you rename the repo or use a custom domain.)

Caveats:

- The first run is a cold start: `cold_start: true`, ranked from lifetime view counts. Real velocity appears once
  two runs at least 25 minutes apart exist.
- GitHub evicts caches unused for 7 days (and old ones when the 10 GB repo limit is hit). If that happens the
  history resets and the next run is a cold start again.
- LLM "why" lines are optional and off by default. To enable them, set the repository variable `SP_WHY_LLM` to `1`
  and add `ANTHROPIC_API_KEY` as a secret. The workflow then installs with `uv sync --frozen --extra llm` and passes
  both through to the "Run pipeline" step. Optionally set the variable `SP_LLM_MODEL` (default `claude-haiku-5-5`).
- `backend-ci.yml` runs `uv run pytest -q` on pushes and PRs touching `backend/` or `docs/`; it needs no config.

## 3. Point the iOS app at the published URL

The feed URL resolves as: Settings override, then Info.plist `SPTrendsURL`, then the built-in default `https://taejunoh.com/seoulpulse/trends.json`.

- **Quick test, no rebuild:** in the app, open Settings and enter the Pages URL as the override. It is stored in the
  App Group defaults, so the widget uses it too (this requires the App Group to work, i.e. a real signed build).
- **Release build:** edit `ios/project.yml` and set `SPTrendsURL` to the Pages URL in **both** places, the
  `SeoulPulse` target and the `SeoulPulseWidget` target (the widget reads its own bundle's Info.plist). Then run
  `cd ios && xcodegen generate`; the generated `SeoulPulse.xcodeproj` and the Info.plist files are tracked in git, so commit
  the regenerated files.
- **ATS:** `NSAllowsLocalNetworking` only exempts local addresses; an `https://` Pages URL needs no exception.
  Remove the `NSAppTransportSecurity` block from both targets in `project.yml` for release to keep the plist
  clean, or leave it, since it is harmless. Do not ship an `http://` feed URL for a non-local host.

## 4. TestFlight (brief)

Requires a paid Apple Developer Program membership.

1. In the developer portal, register the App IDs `com.seoulpulse.app` and `com.seoulpulse.app.widget` (or your own
   unique IDs; bundle IDs are global, so the defaults may be taken) and an App Group (default
   `group.com.seoulpulse.app`), then add that group to both App IDs.
2. If you change any of those IDs, change them consistently in `ios/project.yml`: `bundleIdPrefix`, both
   `PRODUCT_BUNDLE_IDENTIFIER`s, the `com.apple.security.application-groups` entries for both targets, and
   `SPAppGroupID` in both Info.plist blocks. Then run `xcodegen generate`.
3. Set `DEVELOPMENT_TEAM` in `project.yml` (currently `""`) to your Team ID and regenerate. Simulator builds keep
   signing ad hoc via the `CODE_SIGN_IDENTITY[sdk=iphonesimulator*]` setting.
4. Create the app record in App Store Connect, bump `MARKETING_VERSION` / `CURRENT_PROJECT_VERSION` as needed,
   then in Xcode choose a generic iOS device, Product, Archive, and Distribute App, App Store Connect.
5. Add testers in TestFlight. App Review will want a privacy answer (the app collects no personal data) and
   fan-made / not-affiliated wording; see the legal reminders below.

## 5. Alternative hosting

`trends.json` is a plain static file, so anything that serves files over HTTPS works.

- **Cloudflare Pages / R2, Netlify, S3 + CDN, any static host:** run the pipeline on a box you control and upload
  `public/trends.json` after each run (for example with `wrangler`, `aws s3 cp`, or `rsync`). The upload is not part
  of this repo; add it to your scheduler. Serve the file with a short cache lifetime (the dev server sends
  `Cache-Control: no-cache`) and set `SP_PUBLIC_DIR` if you want it written somewhere other than `../public`.
- **Cron box:** keep state in `backend/var` (persistent disk) and run either
  `cd backend && SP_CONTACT=https://... uv run seoulpulse run --loop` under systemd/tmux/Docker, or a cron entry
  every 30 minutes as shown in `backend/README.md` (overlapping runs are safe because of the `var/run.lock` lock).
- Then set `SPTrendsURL` (section 3) to the new host. Skip the `pipeline.yml` workflow by disabling it in the
  Actions tab.

## 6. Operational checklist

- **Watch source health.** Fetch the live `trends.json` and check `sources[]`: `status` is `ok`, `degraded`,
  `error` or `disabled`; `last_success_at` and `detail` tell you why. A persistent `error` means a feed moved or
  rate-limited you. The app shows the same information in Settings.
- **Wikipedia degraded after a fresh start** usually means `SP_CONTACT` is missing or the backfill (29 days per
  article) is still spreading over runs.
- **Refresh the roster** (artist handles drift, channels get renamed). In `backend/`:
  `uv run seoulpulse resolve-channels` (add `--force` to redo every handle) to update
  `data/channels.lock.json`, then `uv run seoulpulse validate-seed --online` to verify channel ids and canonical
  Wikipedia titles. Commit `backend/data/` changes; the workflow uses the committed lock file.
- **Check Actions runs** for red runs, the 60-day schedule shutoff, and cache resets.
- **Legal reminders.** Keep to headline text, link, source name, publish time and our own metrics. Never store,
  translate, summarize or send article bodies / RSS descriptions to an LLM. Keep the "fan-made, not affiliated"
  wording. Yonhap's feed carries a reuse and AI-use notice (`docs/SPEC.md` section 12). Social posting stays manual
  (`uv run seoulpulse social` prints drafts only).
