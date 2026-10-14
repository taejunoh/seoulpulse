# SeoulPulse iOS (app + widget)

SwiftUI app and WidgetKit extension for `trends.json` (contract: `../docs/trends.schema.json`, spec: `../docs/SPEC.md` §8). iOS 18+, Swift 5 language mode, dark-only.

## Build, test, run

```sh
cd ios
xcodegen generate
xcodebuild -project SeoulPulse.xcodeproj -scheme SeoulPulse \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro Max' -derivedDataPath build/DD build
# tests (47, incl. widget snapshots). -collect-test-diagnostics never avoids a 10-minute
# post-run stall on failure with Xcode 27 + the iOS 26.5 simulator.
xcodebuild ... -collect-test-diagnostics never test

xcrun simctl install booted build/DD/Build/Products/Debug-iphonesimulator/SeoulPulse.app
xcrun simctl launch booted com.seoulpulse.app
xcrun simctl openurl booted seoulpulse://item/aespa     # also: seoulpulse://settings, seoulpulse://widget-gallery
```

The build signs ad hoc for the simulator (`CODE_SIGN_IDENTITY[sdk=iphonesimulator*] = "-"`, no team). The App Group entitlement is honored there, so the app and widget share one cache.

## Data flow

`TrendStore.load()` tries the network, then the App Group cache (`trends-cache.json`), then the bundled `Shared/Resources/sample-trends.json` (a byte-identical copy of `docs/trends.example.json`; a test enforces this). It never throws. Every screen labels its origin: **Live**, **Offline** (cache) or **Sample data**.

The feed URL resolves in this order: Settings override (App Group defaults key `trendsURLOverride`) → Info.plist `SPTrendsURL` → `http://localhost:8787/trends.json`. ATS allows local networking. On a device, set your Mac's LAN IP in Settings.

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
| `SeoulPulseTests/` | Decoding/tolerance, AppConfig order, deep links, `compact()`, TrendStore fallback chain (stubbed URLProtocol), refresh policy, widget snapshots |

Widget snapshot PNGs go to `$SP_SNAPSHOT_DIR`, or `ios/build/snapshots/` if unset. Each family has a live render, a `-sample` render and an `-empty` render, plus `-offline` and `-stale` state variants.
