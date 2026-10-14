import SwiftUI
import WidgetKit

/// systemMedium: header row (wordmark + status), then top 6 in two columns of three.
/// Every row is its own deep link.
struct MediumTrendsView: View {
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center) {
                Wordmark()
                Spacer(minLength: 8)
                WidgetStatusLine(snapshot: snapshot, origin: origin, now: now)
            }
            Spacer(minLength: 8)
            if snapshot.items.isEmpty {
                WidgetEmptyMessage()
                Spacer(minLength: 0)
            } else {
                HStack(alignment: .top, spacing: 12) {
                    column(Array(snapshot.items.prefix(3)))
                    Rectangle()
                        .fill(Theme.hairline)
                        .frame(width: 0.5)
                        .padding(.vertical, 2)
                    column(Array(snapshot.items.dropFirst(3).prefix(3)))
                }
                .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .dynamicTypeSize(...DynamicTypeSize.xLarge)
    }

    private func column(_ items: [TrendItem]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(items) { item in
                Link(destination: Route.itemURL(item.id)) {
                    TrendRow(item: item, rankSize: 20, rankWidth: 13, condensedName: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
    }
}
