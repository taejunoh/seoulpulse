import Foundation
import Observation
import WidgetKit

/// App-wide state: the currently shown trends (never empty: cache/sample on launch, then network).
@MainActor
@Observable
final class TrendsModel {
    private(set) var loaded: LoadedTrends
    private(set) var isRefreshing = false
    private(set) var lastFetchAttempt: Date?

    @ObservationIgnored let store: TrendStore

    init(store: TrendStore = TrendStore()) {
        self.store = store
        self.loaded = store.loadLocal()
    }

    var snapshot: TrendsSnapshot { loaded.snapshot }

    func item(id: String) -> TrendItem? { loaded.snapshot.item(id: id) }

    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        let result = await store.load()
        lastFetchAttempt = Date()
        loaded = result
        if result.origin == .network {
            WidgetCenter.shared.reloadTimelines(ofKind: AppConfig.widgetKind)
        }
    }

    /// Scene became active: refresh only if the last fetch is more than 5 minutes old.
    func refreshIfStale() async {
        guard let last = lastFetchAttempt else { return await refresh() }
        if Date().timeIntervalSince(last) > 5 * 60 { await refresh() }
    }
}
