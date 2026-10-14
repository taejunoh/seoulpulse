import SwiftUI

/// Outer size + the content margins WidgetKit applies around the widget's content.
struct WidgetMetrics: Equatable, Sendable {
    var size: CGSize
    var margins: EdgeInsets
    var cornerRadius: CGFloat

    /// The content box the family view is actually laid out in.
    var contentRect: CGRect {
        CGRect(x: margins.leading, y: margins.top,
               width: size.width - margins.leading - margins.trailing,
               height: size.height - margins.top - margins.bottom)
    }
}

/// The four supported widget families.
enum WidgetPreviewFamily: String, CaseIterable, Identifiable, Sendable {
    case small, medium, large, rectangular

    var id: String { rawValue }

    /// SPEC §10 (f) sizes, with WidgetKit's default 16 pt content margins for Home Screen families and
    /// none for accessory families (the system draws no container there).
    var specMetrics: WidgetMetrics {
        switch self {
        case .small: return WidgetMetrics(size: CGSize(width: 170, height: 170), margins: Self.margins(16), cornerRadius: 24)
        case .medium: return WidgetMetrics(size: CGSize(width: 364, height: 170), margins: Self.margins(16), cornerRadius: 24)
        case .large: return WidgetMetrics(size: CGSize(width: 364, height: 382), margins: Self.margins(16), cornerRadius: 24)
        case .rectangular: return WidgetMetrics(size: CGSize(width: 172, height: 76), margins: Self.margins(0), cornerRadius: 0)
        }
    }

    /// Measured on the real Home Screen of the iPhone 17 Pro Max simulator (iOS 26.5) by reading
    /// `\.widgetContentMargins` and the content size inside a placed widget: margins 19.4 pt on every
    /// side; content 138.0 x 137.9 (small), 339.3 x 137.9 (medium), 339.2 x 355.2 (large).
    var deviceMetrics: WidgetMetrics {
        let m = Self.margins(19.4)
        switch self {
        case .small: return WidgetMetrics(size: CGSize(width: 176.8, height: 176.7), margins: m, cornerRadius: 26)
        case .medium: return WidgetMetrics(size: CGSize(width: 378.1, height: 176.7), margins: m, cornerRadius: 26)
        case .large: return WidgetMetrics(size: CGSize(width: 378.0, height: 394.0), margins: m, cornerRadius: 26)
        case .rectangular: return specMetrics   // Lock Screen slot not measured; no margins either way.
        }
    }

    var size: CGSize { specMetrics.size }

    var title: String {
        switch self {
        case .small: return "Small · top 3"
        case .medium: return "Medium · top 6"
        case .large: return "Large · top 10"
        case .rectangular: return "Lock Screen · top 1"
        }
    }

    private static func margins(_ v: CGFloat) -> EdgeInsets {
        EdgeInsets(top: v, leading: v, bottom: v, trailing: v)
    }
}

/// The family's content view (no chrome): exactly what the widget extension renders.
struct WidgetFamilyContent: View {
    let family: WidgetPreviewFamily
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date

    var body: some View {
        switch family {
        case .small: SmallTrendsView(snapshot: snapshot, origin: origin, now: now)
        case .medium: MediumTrendsView(snapshot: snapshot, origin: origin, now: now)
        case .large: LargeTrendsView(snapshot: snapshot, origin: origin, now: now)
        case .rectangular: RectangularTrendView(snapshot: snapshot, origin: origin, now: now)
        }
    }
}

/// Content plus the chrome WidgetKit draws (ink container background, content margins, rounded clip),
/// at the given metrics. Used by the in-app gallery and the snapshot/layout tests.
struct WidgetPreview: View {
    let family: WidgetPreviewFamily
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date
    var metrics: WidgetMetrics? = nil

    var body: some View {
        let m = metrics ?? family.specMetrics
        let shape = RoundedRectangle(cornerRadius: m.cornerRadius, style: .continuous)
        WidgetFamilyContent(family: family, snapshot: snapshot, origin: origin, now: now)
            .padding(m.margins)
            .frame(width: m.size.width, height: m.size.height)
            .coordinateSpace(.named(WidgetLayoutRecorder.coordinateSpace))
            .background(Theme.ink)
            .clipShape(shape)
            .overlay(shape.strokeBorder(Theme.hairline, lineWidth: 0.5))
            .environment(\.colorScheme, .dark)
    }
}
