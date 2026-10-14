import SwiftUI
import WidgetKit

struct TrendsEntry: TimelineEntry {
    let date: Date
    let loaded: LoadedTrends
    var isPlaceholder: Bool = false
}

struct TrendsProvider: TimelineProvider {
    func placeholder(in context: Context) -> TrendsEntry {
        TrendsEntry(date: Date(), loaded: TrendStore().sampleLoaded(), isPlaceholder: true)
    }

    /// Cache-or-sample, never the network (always so for the widget gallery preview).
    func getSnapshot(in context: Context, completion: @escaping (TrendsEntry) -> Void) {
        completion(TrendsEntry(date: Date(), loaded: TrendStore().loadLocal()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<TrendsEntry>) -> Void) {
        Task {
            let loaded = await TrendStore().load(timeout: 8)
            let now = Date()
            let refresh = WidgetRefreshPolicy.nextRefresh(for: loaded, now: now)
            completion(Timeline(entries: [TrendsEntry(date: now, loaded: loaded)], policy: .after(refresh)))
        }
    }
}

struct SeoulPulseWidgetEntryView: View {
    @Environment(\.widgetFamily) private var family
    let entry: TrendsEntry

    var body: some View {
        content
            .redacted(reason: entry.isPlaceholder ? .placeholder : [])
            .containerBackground(for: .widget) { Theme.ink }
    }

    @ViewBuilder private var content: some View {
        let snapshot = entry.loaded.snapshot
        let origin = entry.loaded.origin
        switch family {
        case .systemMedium:
            MediumTrendsView(snapshot: snapshot, origin: origin, now: entry.date)
        case .systemLarge, .systemExtraLarge:
            LargeTrendsView(snapshot: snapshot, origin: origin, now: entry.date)
        case .accessoryRectangular:
            RectangularTrendView(snapshot: snapshot, origin: origin, now: entry.date)
        default:
            SmallTrendsView(snapshot: snapshot, origin: origin, now: entry.date)
        }
    }
}

struct SeoulPulseTrendsWidget: Widget {
    let kind = AppConfig.widgetKind

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: TrendsProvider()) { entry in
            SeoulPulseWidgetEntryView(entry: entry)
        }
        .configurationDisplayName("Korea-first K-pop")
        .description("What's spiking in Korea right now.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge, .accessoryRectangular])
    }
}

@main
struct SeoulPulseWidgetBundle: WidgetBundle {
    var body: some Widget {
        SeoulPulseTrendsWidget()
    }
}
