import SwiftUI
import XCTest
@testable import SeoulPulse

/// (f) Renders every widget family from the bundled sample with `ImageRenderer` at scale 3, asserts the
/// pixel size, writes PNGs to `$SP_SNAPSHOT_DIR` (default `ios/build/snapshots`) and attaches them.
/// The main set uses the SPEC sizes with WidgetKit's default 16 pt content margins; the `-device` set
/// uses the margins and sizes measured on the real iPhone 17 Pro Max Home Screen (19.4 pt margins).
@MainActor
final class WidgetSnapshotTests: XCTestCase {
    /// 8 minutes after the sample's generated_at, so footers read "8m ago" deterministically.
    private func renderTime(_ snapshot: TrendsSnapshot) -> Date { snapshot.generatedAt.addingTimeInterval(8 * 60) }

    func testRenderAllFourFamilies() throws {
        let snapshot = try TestSupport.sampleSnapshot()
        for family in WidgetPreviewFamily.allCases {
            try render(family, snapshot: snapshot, origin: .network, suffix: "", exactSpecSize: true)
            try render(family, snapshot: snapshot, origin: .sample, suffix: "-sample", exactSpecSize: true)
        }
    }

    func testRenderWithMeasuredDeviceMargins() throws {
        let snapshot = try TestSupport.sampleSnapshot()
        let longNames = try TestSupport.longNamesSnapshot()
        for family in [WidgetPreviewFamily.small, .medium, .large] {
            try render(family, snapshot: snapshot, origin: .network, suffix: "-device", metrics: family.deviceMetrics)
            try render(family, snapshot: longNames, origin: .cache, suffix: "-device-longnames-offline",
                       metrics: family.deviceMetrics, now: longNames.generatedAt.addingTimeInterval(2 * 3600))
        }
    }

    func testRenderOfflineAndStaleStates() throws {
        let snapshot = try TestSupport.sampleSnapshot()
        try render(.small, snapshot: snapshot, origin: .cache, suffix: "-offline")
        try render(.medium, snapshot: snapshot, origin: .network, suffix: "-stale",
                   now: snapshot.generatedAt.addingTimeInterval(5 * 3600))
        try render(.large, snapshot: snapshot, origin: .cache, suffix: "-offline",
                   now: snapshot.generatedAt.addingTimeInterval(2 * 3600))
    }

    func testRenderEmptyState() throws {
        var snapshot = try TestSupport.sampleSnapshot()
        snapshot.items = []
        for family in WidgetPreviewFamily.allCases {
            try render(family, snapshot: snapshot, origin: .network, suffix: "-empty")
        }
    }

    private func render(_ family: WidgetPreviewFamily, snapshot: TrendsSnapshot, origin: TrendOrigin,
                        suffix: String, metrics: WidgetMetrics? = nil, now: Date? = nil,
                        exactSpecSize: Bool = false) throws {
        let m = metrics ?? family.specMetrics
        let view = WidgetPreview(family: family, snapshot: snapshot, origin: origin,
                                 now: now ?? renderTime(snapshot), metrics: m)
            .environment(\.widgetClockStyle, .fixed)
            .environment(\.widgetKitTextEmulation, true)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 3
        let image = try XCTUnwrap(renderer.uiImage, "\(family) rendered no image")
        let cg = try XCTUnwrap(image.cgImage)
        if exactSpecSize {
            XCTAssertEqual(cg.width, Int(family.size.width * 3), "\(family) width")
            XCTAssertEqual(cg.height, Int(family.size.height * 3), "\(family) height")
        } else {
            XCTAssertEqual(Double(cg.width), m.size.width * 3, accuracy: 1.5, "\(family)\(suffix) width")
            XCTAssertEqual(Double(cg.height), m.size.height * 3, accuracy: 1.5, "\(family)\(suffix) height")
        }

        let name = "widget-\(family.rawValue)\(suffix).png"
        let dir = TestSupport.snapshotDirectory
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let png = try XCTUnwrap(image.pngData())
        try png.write(to: dir.appendingPathComponent(name), options: .atomic)

        let attachment = XCTAttachment(image: image)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
