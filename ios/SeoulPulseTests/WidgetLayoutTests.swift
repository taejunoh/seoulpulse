import SwiftUI
import XCTest
@testable import SeoulPulse

/// Geometry checks on the widget views, laid out inside the real content margins:
/// every tagged element (wordmark, subtitle, status, rows, empty message) stays inside the content box,
/// no two of them overlap, and in the medium/large headers the status ends at the trailing content edge.
///
/// Why this exists: on the real Home Screen the large widget's header escaped the margins and its pieces
/// overlapped, while ImageRenderer snapshots looked fine. WidgetKit lays out live date text as if it
/// were as wide as the space offered, and the header measured the status's ideal width. The status line
/// is now built so that its layout doesn't depend on that (it always takes the remaining width), which
/// makes these in-process checks representative of WidgetKit.
@MainActor
final class WidgetLayoutTests: XCTestCase {
    private struct Scenario {
        let name: String
        let origin: TrendOrigin
        let age: TimeInterval
    }

    private let scenarios = [
        Scenario(name: "live", origin: .network, age: 8 * 60),                 // "8m ago"
        Scenario(name: "live-2h", origin: .network, age: 2 * 3600 + 50 * 60),  // "2h ago"
        Scenario(name: "offline", origin: .cache, age: 2 * 3600),             // "Offline · 2h ago"
        Scenario(name: "stale", origin: .network, age: 26 * 3600),            // "Stale · 1d ago"
        Scenario(name: "sample", origin: .sample, age: 0),                    // "Sample data"
    ]

    func testElementsStayInsideMarginsAndNeverOverlap() throws {
        let payloads = [("sample", try TestSupport.sampleSnapshot()), ("longnames", try TestSupport.longNamesSnapshot())]
        var checked = 0
        for (payloadName, snapshot) in payloads {
            for family in WidgetPreviewFamily.allCases {
                for (metricsName, metrics) in [("spec", family.specMetrics), ("device", family.deviceMetrics)] {
                    for scenario in scenarios {
                        let context = "\(family.rawValue)/\(metricsName)/\(scenario.name)/\(payloadName)"
                        let frames = try layout(family, metrics: metrics, snapshot: snapshot, scenario: scenario)
                        assertSound(frames, family: family, metrics: metrics, context: context)
                        checked += 1
                    }
                }
            }
        }
        XCTAssertEqual(checked, 2 * 4 * 2 * scenarios.count)
    }

    func testEmptyStateLayout() throws {
        var snapshot = try TestSupport.sampleSnapshot()
        snapshot.items = []
        for family in [WidgetPreviewFamily.small, .medium, .large] {
            for metrics in [family.specMetrics, family.deviceMetrics] {
                let frames = try layout(family, metrics: metrics, snapshot: snapshot, scenario: scenarios[0])
                XCTAssertNotNil(frames["empty"], "\(family) empty message missing")
                assertSound(frames, family: family, metrics: metrics, context: "\(family.rawValue)/empty")
            }
        }
    }

    /// The large subtitle only appears when the status is short, so the header never has to squeeze.
    func testLargeSubtitleRule() throws {
        let snapshot = try TestSupport.sampleSnapshot()
        let m = WidgetPreviewFamily.large.deviceMetrics
        XCTAssertNotNil(try layout(.large, metrics: m, snapshot: snapshot, scenario: scenarios[0])["subtitle"])
        XCTAssertNil(try layout(.large, metrics: m, snapshot: snapshot, scenario: scenarios[2])["subtitle"])
        XCTAssertNil(try layout(.large, metrics: m, snapshot: snapshot, scenario: scenarios[3])["subtitle"])
    }

    /// Guards the WidgetKit-specific failure: containers that measure ideal sizes, and `fixedSize` on the
    /// status line, break when the status holds live time text.
    func testWidgetViewsAvoidIdealSizeMeasurementOfStatus() throws {
        let dir = TestSupport.iosDirectory.appendingPathComponent("Shared/WidgetViews")
        guard let files = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) else {
            throw XCTSkip("Shared/WidgetViews not reachable from the test process")
        }
        let fixedAfterStatus = try NSRegularExpression(pattern: #"WidgetStatusLine\([^)]*\)\s*\.fixedSize"#)
        for file in files where file.pathExtension == "swift" {
            let source = try String(contentsOf: file, encoding: .utf8)
            for banned in ["ViewThatFits", "AnyLayout"] {
                XCTAssertFalse(source.contains(banned), "\(file.lastPathComponent) uses \(banned)")
            }
            let range = NSRange(source.startIndex..., in: source)
            XCTAssertNil(fixedAfterStatus.firstMatch(in: source, range: range),
                         "\(file.lastPathComponent) applies fixedSize to WidgetStatusLine")
        }
    }

    // MARK: Helpers

    private func layout(_ family: WidgetPreviewFamily, metrics: WidgetMetrics, snapshot: TrendsSnapshot,
                        scenario: Scenario) throws -> [String: CGRect] {
        let recorder = WidgetLayoutRecorder()
        let view = WidgetPreview(family: family, snapshot: snapshot, origin: scenario.origin,
                                 now: snapshot.generatedAt.addingTimeInterval(scenario.age), metrics: metrics)
            .environment(\.widgetClockStyle, .fixed)
            .environment(\.widgetKitTextEmulation, true)
            .environment(\.widgetLayoutRecorder, recorder)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 1
        _ = try XCTUnwrap(renderer.uiImage)
        return recorder.frames
    }

    private func assertSound(_ frames: [String: CGRect], family: WidgetPreviewFamily, metrics: WidgetMetrics,
                             context: String, file: StaticString = #filePath, line: UInt = #line) {
        let visible = frames.filter { $0.value.width > 0.5 && $0.value.height > 0.5 }
        XCTAssertFalse(visible.isEmpty, "\(context): nothing recorded", file: file, line: line)

        let box = metrics.contentRect.insetBy(dx: -0.5, dy: -0.5)
        for (id, f) in visible {
            XCTAssertTrue(box.contains(f), "\(context): \(id) \(f) escapes content box \(metrics.contentRect)",
                          file: file, line: line)
        }

        // "status-text" (the glyph box) lives inside "status"; it gets its own checks below.
        let items = visible.filter { $0.key != "status-text" }.sorted { $0.key < $1.key }
        for i in items.indices {
            for j in items.indices where j > i {
                let a = items[i].value.insetBy(dx: 0.5, dy: 0.5)
                let b = items[j].value.insetBy(dx: 0.5, dy: 0.5)
                XCTAssertFalse(a.intersects(b), "\(context): \(items[i].key) \(items[i].value) overlaps \(items[j].key) \(items[j].value)",
                               file: file, line: line)
            }
        }

        if let glyphs = visible["status-text"] {
            for id in ["wordmark", "subtitle"] {
                if let other = visible[id] {
                    XCTAssertFalse(glyphs.insetBy(dx: 0.5, dy: 0.5).intersects(other),
                                   "\(context): status text overlaps \(id)", file: file, line: line)
                }
            }
            if family == .medium || family == .large {
                XCTAssertEqual(glyphs.maxX, metrics.contentRect.maxX, accuracy: 1,
                               "\(context): status text is not right-aligned (\(glyphs))", file: file, line: line)
            } else if family == .small {
                XCTAssertEqual(glyphs.minX, metrics.contentRect.minX, accuracy: 1,
                               "\(context): footer status not left-aligned", file: file, line: line)
            }
        } else if visible["status"] != nil {
            XCTFail("\(context): status text not recorded", file: file, line: line)
        }

        if family == .medium || family == .large {
            if let status = visible["status"], let wordmark = visible["wordmark"] {
                XCTAssertEqual(status.maxX, metrics.contentRect.maxX, accuracy: 1,
                               "\(context): status should end at the trailing content edge", file: file, line: line)
                XCTAssertGreaterThanOrEqual(status.minX, wordmark.maxX, "\(context): status starts inside the wordmark",
                                            file: file, line: line)
                XCTAssertEqual(status.midY, wordmark.midY, accuracy: 3, "\(context): header pieces not on one row",
                               file: file, line: line)
            } else {
                XCTFail("\(context): header not recorded", file: file, line: line)
            }
        }
    }
}
