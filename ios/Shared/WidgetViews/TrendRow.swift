import SwiftUI

/// Gradient wordmark "SeoulPulse" + pink pulse dot.
struct Wordmark: View {
    var font: Font = Theme.wordmarkFont
    var dotSize: CGFloat = 6

    var body: some View {
        HStack(alignment: .center, spacing: dotSize * 0.6) {
            Text("SeoulPulse")
                .font(font)
                .foregroundStyle(Theme.pulseGradient)
                .lineLimit(1)
                .fixedSize()
            Circle()
                .fill(Theme.pulsePink)
                .frame(width: dotSize, height: dotSize)
                .shadow(color: Theme.pulsePink.opacity(0.9), radius: dotSize * 0.6)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("SeoulPulse")
    }
}

/// Heavy rounded rank numeral; rank 1 is filled with the pulse gradient.
struct RankNumeral: View {
    let rank: Int
    var size: CGFloat = 20
    var dimmed: Bool = false

    var body: some View {
        Text("\(rank)")
            .font(Theme.rankFont(size))
            .foregroundStyle(style)
            .lineLimit(1)
            .minimumScaleFactor(0.6)
            .accessibilityLabel("Rank \(rank)")
    }

    private var style: AnyShapeStyle {
        if rank == 1 { return AnyShapeStyle(Theme.pulseGradient) }
        return AnyShapeStyle(dimmed ? Theme.textSecondary : Theme.textPrimary)
    }
}

/// One trend in a widget list.
/// `.compact`: rank · name · change badge (· cyan dot or KR FIRST capsule), one line.
/// `.twoLine`: as compact, plus the `why` line in caption underneath (large widget rows 1-3).
struct TrendRow: View {
    enum Style { case compact, twoLine }
    enum KRMarker { case dot, badge }

    let item: TrendItem
    var style: Style = .compact
    var rankSize: CGFloat = 20
    var rankWidth: CGFloat = 16
    var krMarker: KRMarker = .dot
    var dimRank: Bool = false
    /// Small/medium widgets give a name ~70 pt; SF's condensed width keeps "KISS OF LIFE" whole there.
    var condensedName: Bool = false

    var body: some View {
        HStack(alignment: .center, spacing: condensedName ? 6 : 7) {
            RankNumeral(rank: item.rank, size: rankSize, dimmed: dimRank)
                .frame(width: rankWidth, alignment: .leading)
            switch style {
            case .compact:
                firstLine
            case .twoLine:
                VStack(alignment: .leading, spacing: 1) {
                    firstLine
                    Text(item.why)
                        .font(Theme.whyFont)
                        .foregroundStyle(Theme.textSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var firstLine: some View {
        HStack(alignment: .center, spacing: 4) {
            Text(item.name)
                .font(condensedName ? Theme.nameFont.width(.condensed) : Theme.nameFont)
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .layoutPriority(1)
            if krMarker == .badge && item.koreaFirst {
                KRFirstBadge()
                    .padding(.leading, 2)
            }
            Spacer(minLength: 0)
            HStack(spacing: 4) {
                RankChangeBadge(change: item.change, amount: item.changeAmount)
                if krMarker == .dot {
                    KRFirstDot(visible: item.koreaFirst)
                }
            }
        }
    }
}

// MARK: - Footer / status

enum WidgetClockStyle: Sendable {
    /// Live relative time that ticks on its own inside a widget. Uses the iOS 18 `DateReference` format
    /// limited to one unit ("5 min ago") rather than `Text(date, style: .relative)`, which renders
    /// "4 min, 50 sec" and crowds the small/large headers.
    case live
    /// Static "8m ago" computed from the supplied `now` (deterministic snapshots, gallery).
    case fixed
}

extension EnvironmentValues {
    @Entry var widgetClockStyle: WidgetClockStyle = .live
}

/// Footer state (SPEC §8.3): "Sample data" for `.sample`, "Offline" for `.cache`, "Stale" (amber) when
/// generated more than 3 h ago, otherwise just the relative update time.
struct WidgetStatusLine: View {
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date
    @Environment(\.widgetClockStyle) private var clock

    var body: some View {
        HStack(spacing: 3) {
            switch origin {
            case .sample:
                Text("Sample data")
            case .cache, .network:
                if snapshot.isStale(now: now) {
                    Text("Stale").foregroundStyle(Theme.warn).fontWeight(.semibold)
                    Text("·")
                } else if origin == .cache {
                    Text("Offline").fontWeight(.semibold)
                    Text("·")
                }
                timeText
            }
        }
        .font(.caption2)
        .foregroundStyle(Theme.textTertiary)
        .lineLimit(1)
        .minimumScaleFactor(0.8)
    }

    @ViewBuilder private var timeText: some View {
        let date = min(snapshot.generatedAt, now)
        switch clock {
        case .live:
            Text(.currentDate, format: .reference(to: date, allowedFields: [.minute, .hour, .day], maxFieldCount: 1))
        case .fixed:
            Text(Format.relative(date, now: now))
        }
    }
}

/// Empty `items` state, in long or short form.
struct WidgetEmptyMessage: View {
    var compact: Bool = false
    var showsIcon: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            if showsIcon {
                Image(systemName: "waveform.path")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(Theme.pulseGradient)
                    .padding(.bottom, 6)
            }
            Text("No trends yet")
                .font(compact ? .footnote.weight(.semibold) : .subheadline.weight(.semibold))
                .foregroundStyle(Theme.textPrimary)
            Text(compact ? "Pipeline hasn't published." : "The pipeline hasn't published yet.")
                .font(.caption2)
                .foregroundStyle(Theme.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
