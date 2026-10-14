import SwiftUI

/// Design tokens (SPEC §8.6): dark, neon-on-ink, restrained.
enum Theme {
    static let ink = Color(hex: 0x0A0A12)
    static let surface = Color(hex: 0x15151F)
    static let surface2 = Color(hex: 0x1E1E2C)
    static let hairline = Color(hex: 0x2A2A3A)

    static let textPrimary = Color(hex: 0xF5F5FA)
    static let textSecondary = Color(hex: 0xA1A1B5)
    static let textTertiary = Color(hex: 0x6B6B80)

    static let pulsePink = Color(hex: 0xFF2E88)
    static let neonViolet = Color(hex: 0x7C5CFF)
    static let seoulCyan = Color(hex: 0x2EE6D6)

    static let up = Color(hex: 0x3DDC97)
    static let down = Color(hex: 0xFF5A5F)
    static let same = Color(hex: 0x6B6B80)
    static let warn = Color(hex: 0xFFB020)

    /// pink -> violet at 135deg (top-leading to bottom-trailing).
    static let pulseGradient = LinearGradient(colors: [pulsePink, neonViolet],
                                              startPoint: .topLeading, endPoint: .bottomTrailing)
    /// Same colors, left to right, for thin horizontal bars.
    static let pulseGradientHorizontal = LinearGradient(colors: [pulsePink, neonViolet],
                                                        startPoint: .leading, endPoint: .trailing)

    // Layout
    static let cardRadius: CGFloat = 16
    static let cardPadding: CGFloat = 12
    static let cardSpacing: CGFloat = 10
    static let screenPadding: CGFloat = 16

    // Typography
    static func rankFont(_ size: CGFloat) -> Font {
        .system(size: size, weight: .black, design: .rounded).monospacedDigit()
    }
    static let wordmarkFont: Font = .system(.headline, design: .rounded).weight(.heavy)
    static let nameFont: Font = .subheadline.weight(.semibold)
    static let nameKoFont: Font = .caption2
    static let whyFont: Font = .caption
}

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: opacity)
    }
}

extension SourceState {
    var color: Color {
        switch self {
        case .ok: return Theme.up
        case .degraded: return Theme.warn
        case .error: return Theme.down
        case .disabled: return Theme.same
        }
    }
}

extension TrendOrigin {
    var label: String {
        switch self {
        case .network: return "Live"
        case .cache: return "Offline"
        case .sample: return "Sample data"
        }
    }

    var color: Color {
        switch self {
        case .network: return Theme.up
        case .cache: return Theme.warn
        case .sample: return Theme.neonViolet
        }
    }
}
