import SwiftUI
import WidgetKit

/// systemLarge: header (wordmark, "Korea-first K-pop", status), rows 1-3 on two lines (name + badges,
/// then `why`), rows 4-10 on one line. Height budget per SPEC §8.3: 28 + 3x40 + 7x26, scaled to fit
/// the actual widget height so smaller devices don't clip.
struct LargeTrendsView: View {
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date
    @Environment(\.dynamicTypeSize) private var typeSize

    private static let headerHeight: CGFloat = 28
    private static let tallRow: CGFloat = 40
    private static let shortRow: CGFloat = 26
    /// Header + rows + 9 hairlines + the gap under the header.
    private static let budget: CGFloat = headerHeight + 3 * tallRow + 7 * shortRow + 9 * 0.5 + 4
    /// Narrowest content box that still gets the "Korea-first K-pop" subtitle. At the default text size the
    /// header needs about 307 pt on the Home Screen: wordmark and dot 114, subtitle 95, the longest short
    /// live status ("59 minutes ago") 82, two 8 pt gaps (`testSubtitleThresholdCoversLiveStatus` measures
    /// it). The Pro Max large widgets (332-339 pt of content) keep the subtitle; 6.1-inch and smaller
    /// iPhones (large widget 338 pt wide or less, about 306 pt of content) drop it rather than squeeze or
    /// truncate the status.
    static let subtitleMinContentWidth: CGFloat = 316

    /// The subtitle only appears when the status is short and the header has room for all three pieces, so
    /// it never has to squeeze. Larger text sizes than the default drop it too.
    static func showsSubtitle(snapshot: TrendsSnapshot, origin: TrendOrigin, now: Date, contentWidth: CGFloat,
                              typeSize: DynamicTypeSize) -> Bool {
        WidgetStatusLine.isShort(snapshot: snapshot, origin: origin, now: now)
            && contentWidth >= subtitleMinContentWidth
            && typeSize <= .large
    }

    var body: some View {
        GeometryReader { geo in
            // Scale the budget to the real height: fills a Pro Max large widget, shrinks on an SE.
            let s = min(1.12, geo.size.height / Self.budget)
            VStack(alignment: .leading, spacing: 0) {
                header(contentWidth: geo.size.width)
                    .frame(height: Self.headerHeight * s)
                Color.clear.frame(height: 4 * s)
                if snapshot.items.isEmpty {
                    WidgetEmptyMessage(showsIcon: true)
                        .frame(maxHeight: .infinity)
                } else {
                    let items = Array(snapshot.items.prefix(10))
                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        if index > 0 {
                            Rectangle().fill(Theme.hairline).frame(height: 0.5)
                        }
                        Link(destination: Route.itemURL(item.id)) {
                            row(item, tall: index < 3)
                                .frame(height: (index < 3 ? Self.tallRow : Self.shortRow) * s)
                        }
                    }
                }
                Spacer(minLength: 0)
            }
        }
        .dynamicTypeSize(...DynamicTypeSize.xLarge)
    }

    /// Wordmark, then (see `showsSubtitle`) the subtitle, then the status, which takes the rest of
    /// the row and right-aligns itself. Plain HStack, no measuring containers and no `fixedSize` on the
    /// status: WidgetKit's live time text reports an unbounded ideal width (see WidgetStatusLine).
    private func header(contentWidth: CGFloat) -> some View {
        HStack(alignment: .center, spacing: 8) {
            Wordmark()
                .layoutProbe("wordmark")
            if Self.showsSubtitle(snapshot: snapshot, origin: origin, now: now, contentWidth: contentWidth,
                                  typeSize: typeSize) {
                Text("Korea-first K-pop")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Theme.textTertiary)
                    .lineLimit(1)
                    .fixedSize()
                    .layoutProbe("subtitle")
            }
            WidgetStatusLine(snapshot: snapshot, origin: origin, now: now, alignment: .trailing)
        }
    }

    private func row(_ item: TrendItem, tall: Bool) -> some View {
        TrendRow(item: item,
                 style: tall ? .twoLine : .compact,
                 rankSize: tall ? 24 : 17,
                 rankWidth: 24,
                 krMarker: .badge,
                 dimRank: !tall)
    }
}
