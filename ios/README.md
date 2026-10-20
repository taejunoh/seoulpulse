# Seoul Rising iOS (app + widget)

SwiftUI app and WidgetKit extension for `trends.json` (contract: `../docs/trends.schema.json`, spec: `../docs/SPEC.md` §8). iOS 18+, Swift 5 language mode, dark-only.

The app shows the name **Seoul Rising** (`CFBundleDisplayName` of the app and the widget extension, the `Wordmark`
view, the Lock Screen empty state and the About text). Targets, folders, the Swift module, bundle IDs, the App Group
and the `seoulpulse://` scheme keep the old SeoulPulse name on purpose; `seoulrising://` is also registered and
parsed as an alias, but links the app builds still use `seoulpulse://`.

## Build, test, run

```sh
cd ios
xcodegen generate
xcodebuild -project SeoulPulse.xcodeproj -scheme SeoulPulse \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro Max' -derivedDataPath build/DD build
# tests (56, incl. widget snapshots + widget layout checks). -collect-test-diagnostics never avoids a 10-minute
# post-run stall on failure with Xcode 27 + the iOS 26.5 simulator.
xcodebuild ... -collect-test-diagnostics never test

xcrun simctl install booted build/DD/Build/Products/Debug-iphonesimulator/SeoulPulse.app
xcrun simctl launch booted com.taejunoh.seoulpulse
xcrun simctl openurl booted seoulpulse://item/aespa     # also: seoulpulse://settings, seoulpulse://widget-gallery
```

Bundle IDs are `com.taejunoh.seoulpulse` (app) and `com.taejunoh.seoulpulse.widget`; App Group `group.com.taejunoh.seoulpulse`; team `3BMF4LM6TM`, automatic signing. The build signs ad hoc for the simulator (`CODE_SIGN_IDENTITY[sdk=iphonesimulator*] = "-"`, no signing needed). The App Group entitlement is honored there, so the app and widget share one cache.

## TestFlight

Prerequisite: Xcode signed in to the team account. The App Store Connect app record exists (Apple ID 6822836898, bundle ID `com.taejunoh.seoulpulse`; created as "SeoulPulse", App Name "Seoul Rising" since the 2026-10-19 rename), and the internal group "Internal" gets every processed build automatically.

```sh
ios/scripts/testflight.sh   # xcodegen, Release archive to build/SeoulPulse.xcarchive, export + upload via ExportOptions.plist
```

Build numbers are automatic: the script passes `CURRENT_PROJECT_VERSION=$(date -u +%Y%m%d%H%M)` (UTC timestamp) to `xcodebuild archive`, for the app and widget alike, so there is nothing to bump in `project.yml`. Override with `BUILD_NUMBER=202610141800 ios/scripts/testflight.sh` (integers separated by periods, higher than any previous build). The marketing version is `MARKETING_VERSION` in `project.yml`. The export step retries once after 10 s, because cloud-managed distribution signing sometimes fails the first attempt with "no identity found".

See `../docs/DEPLOY.md` section 4. Both targets carry a `PrivacyInfo.xcprivacy` (no tracking, no collected data, UserDefaults `1C8F.1`).

## Data flow

`TrendStore.load()` tries the network, then the App Group cache (`trends-cache.json`), then the bundled `Shared/Resources/sample-trends.json` (a byte-identical copy of `docs/trends.example.json`; a test enforces this). It never throws. Every screen labels its origin: **Live**, **Offline** (cache) or **Sample data**.

The feed URL resolves in this order: Settings override (App Group defaults key `trendsURLOverride`) → Info.plist `SPTrendsURL` → built-in default `https://taejunoh.com/seoulpulse/trends.json` (the Info.plist value is the same). ATS allows local networking (`NSAllowsLocalNetworking` in `project.yml`). On a physical iPhone, start the backend with `uv run seoulpulse dev --host 0.0.0.0` (the default `127.0.0.1` is not reachable from the phone), then set `http://<Mac LAN IP>:8787/trends.json` in Settings. To develop against a local backend on the simulator, set `http://localhost:8787/trends.json` in Settings.

## Layout

| Path | What |
|---|---|
| `project.yml` | xcodegen spec (app, widget extension, unit tests) |
| `Shared/Models` | Codable model; tolerant of unknown keys/enums and nulls |
| `Shared/Data` | `AppConfig`, `TrendStore` (+ widget refresh policy), `Route` (deep links) |
| `Shared/Design` | `Theme` tokens (§8.6), `Format` (`compact()`, relative time, KST) |
| `Shared/WidgetViews` | Pure widget views plus `WidgetPreview` (exact family size and chrome), used by the widget, the in-app gallery and the snapshot tests |
| `SeoulPulse/` | App: Home, Detail, Settings (incl. source status), Widget gallery |
| `SeoulPulseWidget/` | `StaticConfiguration` provider and entry view |
| `SeoulPulseTests/` | Decoding/tolerance, AppConfig order, deep links, `compact()`, TrendStore fallback chain (stubbed URLProtocol), refresh policy, widget snapshots, widget layout geometry |

Widget snapshot PNGs go to `$SP_SNAPSHOT_DIR`, or `ios/build/snapshots/` if unset. Each family has a live render, a `-sample` render and an `-empty` render, plus `-offline` and `-stale` state variants.

### Widget layout gotchas (learned on the real Home Screen)

- **WidgetKit lays out live date text greedily.** Time text (`.relative`, `.timer`, iOS 18 `.reference`) takes all the width it is offered and reports an unbounded ideal width. A `Spacer` next to it collapses, and `fixedSize`/`ViewThatFits` around it overflow the margins. ImageRenderer doesn't behave like this. So `WidgetStatusLine` is a single `Text` that always takes the remaining width and aligns itself with `multilineTextAlignment`.
- **The harness emulates this.** The `widgetKitTextEmulation` environment flag reproduces it in tests, and `WidgetLayoutTests` asserts every element stays inside the content margins, nothing overlaps, and header status text is right-aligned. A lint test bans `ViewThatFits` and `AnyLayout` in widget views, and `fixedSize` on the status line.
- **Real metrics differ from the spec's.** On the iPhone 17 Pro Max (iOS 26.5) Home Screen, content margins are 19.4 pt on every side. Content boxes are 138×138 (small), 339×138 (medium) and 339×355 (large). These were measured via `\.widgetContentMargins` and live in `WidgetPreviewFamily.deviceMetrics`. The `-device` snapshots and the layout tests use both these and the spec sizes with 16 pt margins.
- **The tests render "8m ago"; the Home Screen says "59 minutes ago".** The large header's subtitle is therefore gated on width (`LargeTrendsView.subtitleMinContentWidth`, 316 pt of content), and `testSubtitleThresholdCoversLiveStatus` measures the real wordmark, subtitle and a live-length status against it. Layout tests also run at 6.1-inch (338 pt large) and iPhone SE (small and large) sizes; the SE medium widget is too short for a header plus three rows and is not covered.
- **WidgetKit truncates where ImageRenderer shrinks.** Long names hit `minimumScaleFactor` in snapshots but truncate on the Home Screen. Small and medium rows therefore only reserve the KR-first dot column when a visible row needs it.
