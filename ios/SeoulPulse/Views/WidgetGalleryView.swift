import SwiftUI

/// Renders all four widget families at their real point sizes from the current snapshot (for screenshots).
struct WidgetGalleryView: View {
    @Environment(TrendsModel.self) private var model

    var body: some View {
        ScrollView {
            VStack(spacing: 26) {
                ForEach(WidgetPreviewFamily.allCases) { family in
                    VStack(spacing: 10) {
                        WidgetPreview(family: family,
                                      snapshot: model.loaded.snapshot,
                                      origin: model.loaded.origin,
                                      now: Date())
                            .shadow(color: .black.opacity(0.55), radius: 18, y: 10)
                        Text(family.title)
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(Theme.textSecondary)
                    }
                }
            }
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .background(wallpaper.ignoresSafeArea())
        .navigationTitle("Widget preview")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
    }

    /// A dim "Home Screen wallpaper" so the ink widgets read against something.
    private var wallpaper: some View {
        ZStack {
            Color(hex: 0x10101C)
            RadialGradient(colors: [Theme.neonViolet.opacity(0.35), .clear],
                           center: .topLeading, startRadius: 10, endRadius: 520)
            RadialGradient(colors: [Theme.pulsePink.opacity(0.22), .clear],
                           center: .bottomTrailing, startRadius: 10, endRadius: 560)
        }
    }
}
