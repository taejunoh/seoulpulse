import SwiftUI
import WidgetKit

/// accessoryRectangular (Lock Screen): `#1 {name} {▲2}`, then `why` on up to two lines.
/// System fonts only; the rank is `.widgetAccentable()` so it picks up the Lock Screen tint.
struct RectangularTrendView: View {
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            if let item = snapshot.items.first {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text("#\(item.rank)")
                        .font(.headline)
                        .fixedSize()
                        .widgetAccentable()
                    Text(item.name)
                        .font(.headline)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .layoutPriority(1)
                    Text(RankChangeBadge.token(change: item.change, amount: item.changeAmount))
                        .font(.caption.weight(.bold))
                        .foregroundStyle(item.change.color)
                        .fixedSize()
                        .accessibilityLabel(RankChangeBadge.accessibilityLabel(change: item.change,
                                                                               amount: item.changeAmount))
                }
                .accessibilityElement(children: .combine)
                Text(item.why)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)
            } else {
                Text("SeoulPulse").font(.headline).widgetAccentable()
                Text("No trends yet").font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .widgetURL(snapshot.items.first.map { Route.itemURL($0.id) })
    }
}
