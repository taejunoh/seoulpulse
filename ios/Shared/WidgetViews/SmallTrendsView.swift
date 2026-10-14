import SwiftUI
import WidgetKit

/// systemSmall: wordmark, top 3 rows, relative-time footer. Whole widget opens item #1.
struct SmallTrendsView: View {
    let snapshot: TrendsSnapshot
    let origin: TrendOrigin
    let now: Date

    var body: some View {
        let items = Array(snapshot.items.prefix(3))
        let dots = items.contains(where: \.koreaFirst)
        VStack(alignment: .leading, spacing: 0) {
            Wordmark()
                .layoutProbe("wordmark")
            Spacer(minLength: 6)
            if items.isEmpty {
                WidgetEmptyMessage(compact: true)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(items) { item in
                        TrendRow(item: item, rankSize: 20, rankWidth: 13, condensedName: true,
                                 reservesDotColumn: dots)
                    }
                }
            }
            Spacer(minLength: 6)
            WidgetStatusLine(snapshot: snapshot, origin: origin, now: now, alignment: .leading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .dynamicTypeSize(...DynamicTypeSize.xLarge)
        .widgetURL(snapshot.items.first.map { Route.itemURL($0.id) })
    }
}
