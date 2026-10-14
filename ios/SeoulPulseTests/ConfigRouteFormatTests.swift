import XCTest
@testable import SeoulPulse

/// (c) AppConfig resolution order.
final class AppConfigTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!
    private let plist: [String: Any] = ["SPTrendsURL": "http://plist.example:8787/trends.json"]

    override func setUp() {
        suiteName = "seoulpulse.tests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
    }

    func testOverrideWinsOverInfoPlist() {
        defaults.set("https://override.example/trends.json", forKey: AppConfig.overrideKey)
        let r = AppConfig.resolveTrendsURL(defaults: defaults, info: plist)
        XCTAssertEqual(r.url.absoluteString, "https://override.example/trends.json")
        XCTAssertEqual(r.source, .override)
    }

    func testInvalidOverridesFallBackToInfoPlist() {
        for bad in ["", "   ", "ftp://host/trends.json", "not a url", "http://", "localhost:8787/trends.json",
                    "file:///tmp/trends.json", "//host/path"] {
            defaults.set(bad, forKey: AppConfig.overrideKey)
            let r = AppConfig.resolveTrendsURL(defaults: defaults, info: plist)
            XCTAssertEqual(r.source, .infoPlist, "override \(bad.debugDescription) should be ignored")
            XCTAssertEqual(r.url.absoluteString, "http://plist.example:8787/trends.json")
        }
    }

    func testInfoPlistWithoutOverride() {
        let r = AppConfig.resolveTrendsURL(defaults: defaults, info: plist)
        XCTAssertEqual(r.source, .infoPlist)
    }

    func testBuiltInWhenInfoPlistMissingOrInvalid() {
        for info in [nil, [:], ["SPTrendsURL": "nope"], ["SPTrendsURL": 42]] as [[String: Any]?] {
            let r = AppConfig.resolveTrendsURL(defaults: defaults, info: info)
            XCTAssertEqual(r.source, .builtIn)
            XCTAssertEqual(r.url.absoluteString, "https://taejunoh.com/seoulpulse/trends.json")
        }
        XCTAssertEqual(AppConfig.resolveTrendsURL(defaults: nil, info: nil).source, .builtIn)
    }

    func testAppInfoPlistShipsDefaultURL() {
        XCTAssertEqual(Bundle.main.object(forInfoDictionaryKey: "SPTrendsURL") as? String,
                       "https://taejunoh.com/seoulpulse/trends.json")
        XCTAssertEqual(Bundle.main.object(forInfoDictionaryKey: "SPAppGroupID") as? String, "group.com.taejunoh.seoulpulse")
    }

    func testSetOverrideValidatesTrimsAndClears() {
        XCTAssertFalse(AppConfig.setOverride("gopher://x", defaults: defaults))
        XCTAssertNil(defaults.string(forKey: AppConfig.overrideKey))
        XCTAssertTrue(AppConfig.setOverride("  http://192.168.0.10:8787/trends.json \n", defaults: defaults))
        XCTAssertEqual(defaults.string(forKey: AppConfig.overrideKey), "http://192.168.0.10:8787/trends.json")
        XCTAssertTrue(AppConfig.setOverride("", defaults: defaults))
        XCTAssertNil(defaults.string(forKey: AppConfig.overrideKey))
    }

    func testValidatedURL() {
        XCTAssertNotNil(AppConfig.validatedURL("HTTPS://Example.com/trends.json"))
        XCTAssertNotNil(AppConfig.validatedURL("http://127.0.0.1:8787/trends.json"))
        XCTAssertNotNil(AppConfig.validatedURL("https://user.github.io/seoulpulse/trends.json"))
        XCTAssertNil(AppConfig.validatedURL(nil))
        XCTAssertNil(AppConfig.validatedURL("mailto:a@b.c"))
    }
}

/// (d) Deep-link parsing.
final class RouteTests: XCTestCase {
    private func parse(_ s: String) -> [Route] { Route.parse(URL(string: s)!) }

    func testItemLinks() {
        XCTAssertEqual(parse("seoulpulse://item/aespa"), [.item("aespa")])
        XCTAssertEqual(parse("seoulpulse://item/kiss-of-life"), [.item("kiss-of-life")])
        XCTAssertEqual(parse("SeoulPulse://ITEM/aespa"), [.item("aespa")])
        XCTAssertEqual(parse("seoulpulse://item/aespa?src=widget"), [.item("aespa")])
        XCTAssertEqual(parse("seoulpulse://item/aespa/extra"), [.item("aespa")])
    }

    func testScreens() {
        XCTAssertEqual(parse("seoulpulse://settings"), [.settings])
        XCTAssertEqual(parse("seoulpulse://widget-gallery"), [.widgetGallery])
    }

    func testEverythingElseGoesHome() {
        XCTAssertEqual(parse("seoulpulse://item"), [])
        XCTAssertEqual(parse("seoulpulse://item/"), [])
        XCTAssertEqual(parse("seoulpulse://"), [])
        XCTAssertEqual(parse("seoulpulse://unknown/thing"), [])
        XCTAssertEqual(parse("https://example.com/item/aespa"), [])
        XCTAssertEqual(parse("otherapp://item/aespa"), [])
    }

    func testRoundTrip() {
        for route in [Route.item("lim-young-woong"), .settings, .widgetGallery] {
            XCTAssertEqual(Route.parse(route.url), [route])
        }
        XCTAssertEqual(Route.itemURL("aespa").absoluteString, "seoulpulse://item/aespa")
    }
}

/// (e) `compact()` table and the other formatters.
final class FormatTests: XCTestCase {
    func testCompactSpecTable() {
        XCTAssertEqual(Format.compact(999), "999")
        XCTAssertEqual(Format.compact(7584), "7.6K")
        XCTAssertEqual(Format.compact(126_667), "127K")
        XCTAssertEqual(Format.compact(1_250_000), "1.2M")
    }

    func testCompactEdges() {
        XCTAssertEqual(Format.compact(0), "0")
        XCTAssertEqual(Format.compact(1000), "1K")
        XCTAssertEqual(Format.compact(1050), "1.1K")
        XCTAssertEqual(Format.compact(9_999), "10K")
        XCTAssertEqual(Format.compact(10_000), "10K")
        XCTAssertEqual(Format.compact(20_000), "20K")
        XCTAssertEqual(Format.compact(999_499), "999K")
        XCTAssertEqual(Format.compact(999_600), "1M")
        XCTAssertEqual(Format.compact(1_000_000), "1M")
        XCTAssertEqual(Format.compact(212_000_000), "212M")
        XCTAssertEqual(Format.compact(-7584), "-7.6K")
    }

    func testRatio() {
        XCTAssertEqual(Format.ratio(3.25), "3.25x")
        XCTAssertEqual(Format.ratio(1.7), "1.7x")
        XCTAssertEqual(Format.ratio(3.0), "3.0x")
        XCTAssertEqual(Format.ratio(0.96), "0.96x")
        XCTAssertEqual(Format.ratio(4), "4.0x")
    }

    func testSignal() {
        XCTAssertEqual(Format.signal(0.814), "0.81")
        XCTAssertEqual(Format.signal(-0.03), "-0.03")
        XCTAssertEqual(Format.signal(-0.0001), "0.00")
    }

    func testRelative() {
        let now = TestSupport.date("2026-10-14T09:00:00Z")
        XCTAssertEqual(Format.relative(now.addingTimeInterval(-30), now: now), "just now")
        XCTAssertEqual(Format.relative(now.addingTimeInterval(-8 * 60), now: now), "8m ago")
        XCTAssertEqual(Format.relative(now.addingTimeInterval(-3 * 3600 - 59), now: now), "3h ago")
        XCTAssertEqual(Format.relative(now.addingTimeInterval(-50 * 3600), now: now), "2d ago")
        XCTAssertEqual(Format.relative(now.addingTimeInterval(600), now: now), "just now", "future reads as just now")
    }

    func testKST() {
        XCTAssertEqual(Format.kstTime(TestSupport.date("2026-10-14T09:00:00Z")), "18:00 KST")
        XCTAssertEqual(Format.kstDateTime(TestSupport.date("2026-10-14T09:00:00Z")), "Oct 14, 18:00 KST")
    }

    func testStaleness() throws {
        let snap = try TestSupport.sampleSnapshot()
        XCTAssertFalse(snap.isStale(now: snap.generatedAt.addingTimeInterval(3 * 3600)))
        XCTAssertTrue(snap.isStale(now: snap.generatedAt.addingTimeInterval(3 * 3600 + 1)))
        XCTAssertFalse(snap.isOverADayOld(now: snap.generatedAt.addingTimeInterval(23 * 3600)))
        XCTAssertTrue(snap.isOverADayOld(now: snap.generatedAt.addingTimeInterval(25 * 3600)))
    }

    func testRankChangeAccessibilityLabels() {
        XCTAssertEqual(RankChangeBadge.accessibilityLabel(change: .up, amount: 2), "Up 2 places")
        XCTAssertEqual(RankChangeBadge.accessibilityLabel(change: .down, amount: 1), "Down 1 place")
        XCTAssertEqual(RankChangeBadge.accessibilityLabel(change: .same, amount: 0), "No change")
        XCTAssertEqual(RankChangeBadge.accessibilityLabel(change: .new, amount: 0), "New entry")
        XCTAssertEqual(RankChangeBadge.token(change: .up, amount: 2), "▲2")
        XCTAssertEqual(RankChangeBadge.token(change: .down, amount: 3), "▼3")
    }
}
