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
    /// Small/medium widgets give a name ~85 pt; SF's condensed width keeps "Xdinary Heroes" whole there.
    var condensedName: Bool = false
    /// Reserve the 6 pt KR-first dot column (keeps badges aligned). Callers turn it off when no visible
    /// row is KR-first, which hands those 10 pt back to the name. WidgetKit truncates long names here
    /// where an in-process renderer would apply `minimumScaleFactor`, so the width really matters.
    var reservesDotColumn: Bool = true

    var body: some View {
        HStack(alignment: .center, spacing: condensedName ? 5 : 7) {
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
        .layoutProbe("row-\(item.rank)")
    }

    private var firstLine: some View {
        HStack(alignment: .center, spacing: 3) {
            Text(item.name)
                .font(condensedName ? Theme.nameFont.width(.condensed) : Theme.nameFont)
                .foregroundStyle(Theme.textPrimary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .allowsTightening(true)
                .layoutPriority(1)
            if krMarker == .badge && item.koreaFirst {
                KRFirstBadge()
                    .padding(.leading, 3)
            }
            Spacer(minLength: 0)
            HStack(spacing: 4) {
                RankChangeBadge(change: item.change, amount: item.changeAmount)
                if krMarker == .dot && reservesDotColumn {
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
    /// "4 min, 50 sec" and crowds the headers.
    case live
    /// Static "8m ago" computed from the supplied `now` (deterministic snapshots, gallery).
    case fixed
}

extension EnvironmentValues {
    @Entry var widgetClockStyle: WidgetClockStyle = .live
}

/// Footer/header state (SPEC §8.3): "Sample data" for `.sample`, "Offline" for `.cache`, "Stale" (amber)
/// when generated more than 3 h ago, otherwise just the relative update time.
///
/// WidgetKit lays out live date text as if it were as wide as whatever width it is offered (it reserves
/// room for the text to change). An `HStack { Wordmark; Spacer; status }` therefore collapses the spacer,
/// and anything that asks for the status's *ideal* width (`fixedSize`, measuring containers) gets an
/// unbounded answer and overflows the margins. So the status is one `Text`, always given the remaining
/// width, aligning its own content with `multilineTextAlignment`. The layout is then identical whether
/// the time text is greedy (WidgetKit) or not (ImageRenderer snapshots, the in-app gallery).
struct WidgetStatusLine: View {
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date
    var alignment: TextAlignment = .leading
    @Environment(\.widgetClockStyle) private var clock
    @Environment(\.widgetKitTextEmulation) private var emulateWidgetKit

    @ViewBuilder var body: some View {
        let frameAlignment: Alignment = alignment == .trailing ? .trailing : .leading
        let styled = statusText.font(.caption2).foregroundStyle(Theme.textTertiary)
        if emulateWidgetKit {
            // Test harness: behave like WidgetKit's live time text. The box claims every point it is
            // offered and reports an unbounded ideal width; the glyphs sit at the text alignment inside
            // it ("status-text"). A header that measures the status, or forgets to align it, fails the
            // layout tests under this emulation exactly as it fails on the Home Screen.
            styled
                .lineLimit(1)
                .fixedSize()
                .layoutProbe("status-text")
                .frame(minWidth: 0, idealWidth: 4096, maxWidth: .infinity, alignment: frameAlignment)
                .layoutProbe("status")
        } else {
            styled
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .multilineTextAlignment(alignment)
                .frame(maxWidth: .infinity, alignment: frameAlignment)
                .layoutProbe("status")
        }
    }

    /// True when the status reads as just a short time ("8 minutes ago" / "Sample data"), i.e. there is
    /// room for the large widget's subtitle next to it.
    static func isShort(snapshot: TrendsSnapshot, origin: TrendOrigin, now: Date) -> Bool {
        switch origin {
        case .sample: return true
        case .cache: return false
        case .network: return !snapshot.isStale(now: now)
        }
    }

    private var statusText: Text {
        switch origin {
        case .sample:
            return Text("Sample data")
        case .cache, .network:
            if snapshot.isStale(now: now) {
                return Text("\(Text("Stale").fontWeight(.semibold).foregroundStyle(Theme.warn)) · \(timeText)")
            }
            if origin == .cache {
                return Text("\(Text("Offline").fontWeight(.semibold)) · \(timeText)")
            }
            return timeText
        }
    }

    private var timeText: Text {
        let date = min(snapshot.generatedAt, now)
        switch clock {
        case .live:
            return Text(.currentDate, format: .reference(to: date, allowedFields: [.minute, .hour, .day], maxFieldCount: 1))
        case .fixed:
            return Text(Format.relative(date, now: now))
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
        .layoutProbe("empty")
    }
}

// MARK: - Layout probe (tests only)

/// Collects the frames of tagged widget elements, in the widget's coordinate space, so tests can assert
/// "inside the content margins" and "header pieces don't overlap". Absent in production (nil), where
/// `layoutProbe` is a no-op.
final class WidgetLayoutRecorder: @unchecked Sendable {
    static let coordinateSpace = "widget"
    private(set) var frames: [String: CGRect] = [:]

    func record(_ id: String, _ frame: CGRect) -> Color {
        frames[id] = frame
        return .clear
    }
}

extension EnvironmentValues {
    @Entry var widgetLayoutRecorder: WidgetLayoutRecorder? = nil
    /// Tests only: make `WidgetStatusLine` lay out the way WidgetKit lays out live date text.
    @Entry var widgetKitTextEmulation: Bool = false
}

private struct LayoutProbe: ViewModifier {
    let id: String
    @Environment(\.widgetLayoutRecorder) private var recorder

    func body(content: Content) -> some View {
        if let recorder {
            content.background(
                GeometryReader { proxy in
                    recorder.record(id, proxy.frame(in: .named(WidgetLayoutRecorder.coordinateSpace)))
                }
            )
        } else {
            content
        }
    }
}

extension View {
    func layoutProbe(_ id: String) -> some View { modifier(LayoutProbe(id: id)) }
}
