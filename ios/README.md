# SeoulPulse iOS (app + widget)

SwiftUI app and WidgetKit extension for `trends.json` (contract: `../docs/trends.schema.json`, spec: `../docs/SPEC.md` §8). iOS 18+, Swift 5 language mode, dark-only.

## Build, test, run

```sh
cd ios
xcodegen generate
xcodebuild -project SeoulPulse.xcodeproj -scheme SeoulPulse \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro Max' -derivedDataPath build/DD build
# tests (52, incl. widget snapshots + widget layout checks). -collect-test-diagnostics never avoids a 10-minute
# post-run stall on failure with Xcode 27 + the iOS 26.5 simulator.
xcodebuild ... -collect-test-diagnostics never test

xcrun simctl install booted build/DD/Build/Products/Debug-iphonesimulator/SeoulPulse.app
xcrun simctl launch booted com.seoulpulse.app
xcrun simctl openurl booted seoulpulse://item/aespa     # also: seoulpulse://settings, seoulpulse://widget-gallery
```

The build signs ad hoc for the simulator (`CODE_SIGN_IDENTITY[sdk=iphonesimulator*] = "-"`, no team). The App Group entitlement is honored there, so the app and widget share one cache.

## Data flow

`TrendStore.load()` tries the network, then the App Group cache (`trends-cache.json`), then the bundled `Shared/Resources/sample-trends.json` (a byte-identical copy of `docs/trends.example.json`; a test enforces this). It never throws. Every screen labels its origin: **Live**, **Offline** (cache) or **Sample data**.

The feed URL resolves in this order: Settings override (App Group defaults key `trendsURLOverride`) → Info.plist `SPTrendsURL` → `http://localhost:8787/trends.json`. ATS allows local networking (`NSAllowsLocalNetworking` in `project.yml`). On a physical iPhone, start the backend with `uv run seoulpulse dev --host 0.0.0.0` (the default `127.0.0.1` is not reachable from the phone), then set `http://<Mac LAN IP>:8787/trends.json` in Settings.

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
- **WidgetKit truncates where ImageRenderer shrinks.** Long names hit `minimumScaleFactor` in snapshots but truncate on the Home Screen. Small and medium rows therefore only reserve the KR-first dot column when a visible row needs it.
