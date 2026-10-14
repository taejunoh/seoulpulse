import SwiftUI

/// Small uppercase section label ("SIGNALS", "HEADLINES"...).
struct SectionLabel: View {
    let title: String

    init(_ title: String) { self.title = title }

    var body: some View {
        Text(title.uppercased())
            .font(.caption2.weight(.bold))
            .kerning(1.2)
            .foregroundStyle(Theme.textTertiary)
            .accessibilityAddTraits(.isHeader)
    }
}

/// Surface card: radius 16, padding 12 (SPEC §8.6).
struct SurfaceCard<Content: View>: View {
    var title: String?
    var padding: CGFloat = Theme.cardPadding
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let title { SectionLabel(title) }
            content
        }
        .padding(padding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous).fill(Theme.surface))
    }
}

/// Inline notice banner (calibrating / sample data / day-old data).
struct NoticeBanner: View {
    let icon: String
    let text: String
    let tint: Color
    var showsChevron: Bool = false

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: icon)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(tint)
                .frame(width: 18)
            Text(text)
                .font(.footnote)
                .foregroundStyle(Theme.textPrimary.opacity(0.9))
                .frame(maxWidth: .infinity, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textTertiary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(tint.opacity(0.10))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(tint.opacity(0.22), lineWidth: 0.5)
        )
        .accessibilityElement(children: .combine)
    }
}

/// Subtle press feedback for tappable cards.
struct PressableCardStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .animation(.spring(duration: 0.2), value: configuration.isPressed)
    }
}

/// Gradient-filled score bar, 3 pt tall, width proportional to score/100.
struct ScoreBar: View {
    let score: Double
    /// Fixed label column ("100.0" wide) so bar ends line up across cards whatever the score.
    @ScaledMetric(relativeTo: .caption2) private var labelWidth: CGFloat = 34

    var body: some View {
        HStack(spacing: 8) {
            GeometryReader { geo in
                let fraction = min(max(score / 100, 0), 1)
                ZStack(alignment: .leading) {
                    Capsule().fill(Theme.hairline)
                    Capsule()
                        .fill(Theme.pulseGradientHorizontal)
                        .frame(width: max(3, geo.size.width * fraction))
                }
            }
            .frame(height: 3)
            Text(String(format: "%.1f", score))
                .font(.caption2.weight(.semibold).monospacedDigit())
                .foregroundStyle(Theme.textTertiary)
                .lineLimit(1)
                .frame(minWidth: labelWidth, alignment: .trailing)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Score \(String(format: "%.1f", score)) of 100")
    }
}

/// A colored dot that softly pulses (static when Reduce Motion is on).
struct StatusDot: View {
    let color: Color
    var animated: Bool = false
    var size: CGFloat = 7
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulse = false

    var body: some View {
        Circle()
            .fill(color)
            .frame(width: size, height: size)
            .background(
                Circle()
                    .fill(color.opacity(0.45))
                    .scaleEffect(pulse ? 2.4 : 1)
                    .opacity(pulse ? 0 : 1)
            )
            .onAppear {
                guard animated, !reduceMotion else { return }
                withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: false)) { pulse = true }
            }
            .accessibilityHidden(true)
    }
}

/// Capsule "Yonhap · KR" used on headline rows.
struct SourceTag: View {
    let text: String
    var tint: Color = Theme.textSecondary

    var body: some View {
        Text(text)
            .font(.caption2.weight(.bold))
            .foregroundStyle(tint)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Capsule().fill(Theme.surface2))
            .fixedSize()
    }
}
