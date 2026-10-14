import SwiftUI

/// "KR FIRST" capsule: cyan text on a 16% cyan fill (app + large widget).
struct KRFirstBadge: View {
    var scale: CGFloat = 1

    var body: some View {
        Text("KR FIRST")
            .font(.system(size: 9 * scale, weight: .bold))
            .kerning(0.4)
            .foregroundStyle(Theme.seoulCyan)
            .padding(.horizontal, 5 * scale)
            .padding(.vertical, 2 * scale)
            .background(Capsule().fill(Theme.seoulCyan.opacity(0.16)))
            .fixedSize()
            .accessibilityLabel("Korea first")
    }
}

/// 6 pt cyan dot used by the small and medium widgets.
struct KRFirstDot: View {
    var visible: Bool = true

    var body: some View {
        Circle()
            .fill(Theme.seoulCyan)
            .frame(width: 6, height: 6)
            .opacity(visible ? 1 : 0)
            .accessibilityHidden(!visible)
            .accessibilityLabel("Korea first")
    }
}
