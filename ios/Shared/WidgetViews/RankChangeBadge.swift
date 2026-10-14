import SwiftUI

/// Rank movement since the 6 h basis (SPEC §8.6). VoiceOver reads "Up 2 places", "Down 1 place",
/// "No change" or "New entry" instead of the glyphs.
struct RankChangeBadge: View {
    let change: RankChange
    let amount: Int
    /// 1.0 matches the spec sizes (8 pt arrows, 9 pt NEW); the app detail hero uses a larger scale.
    var scale: CGFloat = 1

    var body: some View {
        content
            .fixedSize()
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(Self.accessibilityLabel(change: change, amount: amount))
    }

    @ViewBuilder private var content: some View {
        switch change {
        case .up:
            arrow("arrowtriangle.up.fill", color: Theme.up)
        case .down:
            arrow("arrowtriangle.down.fill", color: Theme.down)
        case .same:
            Image(systemName: "minus")
                .font(.system(size: 10 * scale, weight: .heavy))
                .foregroundStyle(Theme.same)
        case .new:
            Text("NEW")
                .font(.system(size: 9 * scale, weight: .heavy))
                .kerning(0.3)
                .foregroundStyle(.white)
                .padding(.horizontal, 5 * scale)
                .padding(.vertical, 2 * scale)
                .background(Capsule().fill(Theme.pulsePink))
        }
    }

    private func arrow(_ symbol: String, color: Color) -> some View {
        HStack(spacing: 2 * scale) {
            Image(systemName: symbol).font(.system(size: 8 * scale, weight: .bold))
            Text("\(amount)")
                .font(.system(size: 11 * scale, weight: .bold, design: .rounded).monospacedDigit())
        }
        .foregroundStyle(color)
    }

    static func accessibilityLabel(change: RankChange, amount: Int) -> String {
        switch change {
        case .up: return amount > 0 ? "Up \(amount) \(amount == 1 ? "place" : "places")" : "Up"
        case .down: return amount > 0 ? "Down \(amount) \(amount == 1 ? "place" : "places")" : "Down"
        case .same: return "No change"
        case .new: return "New entry"
        }
    }

    /// Plain-text form for the lock screen line `#1 {name} {▲2}`.
    static func token(change: RankChange, amount: Int) -> String {
        switch change {
        case .up: return "▲\(amount)"
        case .down: return "▼\(amount)"
        case .same: return "–"
        case .new: return "NEW"
        }
    }
}

extension RankChange {
    var color: Color {
        switch self {
        case .up: return Theme.up
        case .down: return Theme.down
        case .same: return Theme.same
        case .new: return Theme.pulsePink
        }
    }
}
