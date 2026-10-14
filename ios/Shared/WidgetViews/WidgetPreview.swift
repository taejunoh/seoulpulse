import SwiftUI

/// The four supported widget families with their iPhone 17 Pro Max point sizes (SPEC §10 f).
enum WidgetPreviewFamily: String, CaseIterable, Identifiable, Sendable {
    case small, medium, large, rectangular

    var id: String { rawValue }

    var size: CGSize {
        switch self {
        case .small: return CGSize(width: 170, height: 170)
        case .medium: return CGSize(width: 364, height: 170)
        case .large: return CGSize(width: 364, height: 382)
        case .rectangular: return CGSize(width: 172, height: 76)
        }
    }

    var title: String {
        switch self {
        case .small: return "Small · top 3"
        case .medium: return "Medium · top 6"
        case .large: return "Large · top 10"
        case .rectangular: return "Lock Screen · top 1"
        }
    }

    /// Home Screen widgets get the system's 16 pt content margins. Accessory widgets have none; the
    /// preview adds a hairline inset only so text doesn't touch the rounded clip.
    var contentPadding: EdgeInsets {
        switch self {
        case .rectangular: return EdgeInsets(top: 4, leading: 8, bottom: 4, trailing: 8)
        default: return EdgeInsets(top: 16, leading: 16, bottom: 16, trailing: 16)
        }
    }

    var cornerRadius: CGFloat { self == .rectangular ? 14 : 24 }
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

/// Content plus the chrome WidgetKit would draw (ink container background, margins, rounded clip),
/// at the family's exact size. Used by the in-app gallery and the snapshot tests.
struct WidgetPreview: View {
    let family: WidgetPreviewFamily
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: family.cornerRadius, style: .continuous)
        WidgetFamilyContent(family: family, snapshot: snapshot, origin: origin, now: now)
            .padding(family.contentPadding)
            .frame(width: family.size.width, height: family.size.height)
            .background(Theme.ink)
            .clipShape(shape)
            .overlay(shape.strokeBorder(Theme.hairline, lineWidth: 0.5))
            .environment(\.colorScheme, .dark)
    }
}
