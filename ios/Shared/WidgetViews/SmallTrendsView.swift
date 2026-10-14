import SwiftUI
import WidgetKit

/// systemSmall: wordmark, top 3 rows, relative-time footer. Whole widget opens item #1.
struct SmallTrendsView: View {
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Wordmark()
            Spacer(minLength: 6)
            if snapshot.items.isEmpty {
                WidgetEmptyMessage(compact: true)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(snapshot.items.prefix(3)) { item in
                        TrendRow(item: item, rankSize: 20, rankWidth: 13, condensedName: true)
                    }
                }
            }
            Spacer(minLength: 6)
            WidgetStatusLine(snapshot: snapshot, origin: origin, now: now)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .dynamicTypeSize(...DynamicTypeSize.xLarge)
        .widgetURL(snapshot.items.first.map { Route.itemURL($0.id) })
    }
}
