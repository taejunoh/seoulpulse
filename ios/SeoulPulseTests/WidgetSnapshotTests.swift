import SwiftUI
import XCTest
@testable import SeoulPulse

/// (f) Renders every widget family from the bundled sample with `ImageRenderer` at scale 3, asserts the
/// pixel size, writes PNGs to `$SP_SNAPSHOT_DIR` (default `ios/build/snapshots`) and attaches them.
@MainActor
final class WidgetSnapshotTests: XCTestCase {
    /// 8 minutes after the sample's generated_at, so footers read "8m ago" deterministically.
    private func renderTime(_ snapshot: TrendsSnapshot) -> Date { snapshot.generatedAt.addingTimeInterval(8 * 60) }

    func testRenderAllFourFamilies() throws {
        let snapshot = try TestSupport.sampleSnapshot()
        // Main set: rendered as a live (network) widget. "-sample" variants show the sample footer state.
        for family in WidgetPreviewFamily.allCases {
            try render(family, snapshot: snapshot, origin: .network, suffix: "")
            try render(family, snapshot: snapshot, origin: .sample, suffix: "-sample")
        }
    }

    func testRenderOfflineAndStaleStates() throws {
        let snapshot = try TestSupport.sampleSnapshot()
        try render(.small, snapshot: snapshot, origin: .cache, suffix: "-offline")
        try render(.medium, snapshot: snapshot, origin: .network, suffix: "-stale",
                   now: snapshot.generatedAt.addingTimeInterval(5 * 3600))
    }

    func testRenderEmptyState() throws {
        var snapshot = try TestSupport.sampleSnapshot()
        snapshot.items = []
        for family in WidgetPreviewFamily.allCases {
            try render(family, snapshot: snapshot, origin: .network, suffix: "-empty")
        }
    }

    private func render(_ family: WidgetPreviewFamily, snapshot: TrendsSnapshot, origin: TrendOrigin,
                        suffix: String, now: Date? = nil) throws {
        let view = WidgetPreview(family: family, snapshot: snapshot, origin: origin, now: now ?? renderTime(snapshot))
            .environment(\.widgetClockStyle, .fixed)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 3
        let image = try XCTUnwrap(renderer.uiImage, "\(family) rendered no image")
        let cg = try XCTUnwrap(image.cgImage)
        XCTAssertEqual(cg.width, Int(family.size.width * 3), "\(family) width")
        XCTAssertEqual(cg.height, Int(family.size.height * 3), "\(family) height")

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
