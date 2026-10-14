import XCTest
@testable import SeoulPulse

/// Fallback chain network -> cache -> sample, all offline via `StubURLProtocol`.
final class TrendStoreTests: XCTestCase {
    private var dir: URL!
    private let fixedNow = TestSupport.date("2026-10-14T09:05:00Z")

    override func setUpWithError() throws {
        StubURLProtocol.reset()
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("trendstore-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() {
        StubURLProtocol.reset()
        try? FileManager.default.removeItem(at: dir)
    }

    private func makeStore() -> TrendStore {
        var store = TrendStore()
        let cacheDir: URL = dir
        let now = fixedNow
        store.urlProvider = { URL(string: "http://stub.local/trends.json")! }
        store.cacheDirectory = { cacheDir }
        store.sessionConfiguration = {
            let c = URLSessionConfiguration.ephemeral
            c.protocolClasses = [StubURLProtocol.self]
            return c
        }
        store.now = { now }
        return store
    }

    func testNetworkSuccessIsLabelledAndCached() async throws {
        let sample = try TestSupport.sampleData()
        StubURLProtocol.handler = { _ in (200, sample) }
        let store = makeStore()

        let loaded = await store.load()
        XCTAssertEqual(loaded.origin, .network)
        XCTAssertEqual(loaded.fetchedAt, fixedNow)
        XCTAssertEqual(loaded.snapshot.items.count, 10)
        XCTAssertEqual(StubURLProtocol.requests.count, 1)
        XCTAssertEqual(StubURLProtocol.requests.first?.url?.absoluteString, "http://stub.local/trends.json")

        XCTAssertTrue(FileManager.default.fileExists(atPath: store.cacheFileURL.path))
        let cached = try XCTUnwrap(store.readCache())
        XCTAssertEqual(cached.origin, .cache)
        XCTAssertEqual(cached.fetchedAt, fixedNow)
        XCTAssertEqual(cached.snapshot, loaded.snapshot)
    }

    func testNetworkFailureFallsBackToCache() async throws {
        let sample = try TestSupport.sampleData()
        StubURLProtocol.handler = { _ in (200, sample) }
        let store = makeStore()
        _ = await store.load()

        StubURLProtocol.handler = { _ in (500, Data()) }
        let loaded = await store.load()
        XCTAssertEqual(loaded.origin, .cache)
        XCTAssertEqual(loaded.fetchedAt, fixedNow)
        XCTAssertEqual(loaded.snapshot.items.first?.id, "kiss-of-life")
    }

    func testTransportErrorWithoutCacheFallsBackToSample() async throws {
        StubURLProtocol.handler = { _ in throw URLError(.cannotConnectToHost) }
        let loaded = await makeStore().load()
        XCTAssertEqual(loaded.origin, .sample)
        XCTAssertNil(loaded.fetchedAt)
        XCTAssertEqual(loaded.snapshot.items.count, 10)
    }

    func testDecodeFailureIsTreatedAsNetworkFailure() async throws {
        StubURLProtocol.handler = { _ in (200, Data("<html>oops</html>".utf8)) }
        let store = makeStore()
        let loaded = await store.load()
        XCTAssertEqual(loaded.origin, .sample)
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.cacheFileURL.path), "bad payload must not be cached")
    }

    func testNon200IsFailure() async throws {
        let sample = try TestSupport.sampleData()
        for status in [204, 304, 404] {
            StubURLProtocol.handler = { _ in (status, sample) }
            let loaded = await makeStore().load()
            XCTAssertEqual(loaded.origin, .sample, "HTTP \(status) must not count as success")
        }
    }

    func testNetworkFalseNeverFetches() async throws {
        StubURLProtocol.handler = { _ in (200, try TestSupport.sampleData()) }
        let loaded = await makeStore().load(network: false)
        XCTAssertEqual(loaded.origin, .sample)
        XCTAssertTrue(StubURLProtocol.requests.isEmpty)
    }

    func testCorruptCacheIsIgnored() async throws {
        let store = makeStore()
        try Data("{garbage".utf8).write(to: store.cacheFileURL)
        XCTAssertNil(store.readCache())
        XCTAssertEqual(store.loadLocal().origin, .sample)
    }

    func testFetchErrorsDescribeThemselves() async throws {
        StubURLProtocol.handler = { _ in (503, Data()) }
        do {
            _ = try await makeStore().fetch(url: URL(string: "http://stub.local/trends.json")!)
            XCTFail("expected an error")
        } catch {
            XCTAssertEqual(error as? TrendFetchError, .badStatus(503))
            XCTAssertEqual(error.localizedDescription, "HTTP 503")
        }
    }

    func testMissingSampleStillYieldsEmptySnapshot() {
        var store = makeStore()
        store.sampleURL = { nil }
        let loaded = store.loadLocal()
        XCTAssertEqual(loaded.origin, .sample)
        XCTAssertTrue(loaded.snapshot.items.isEmpty)
    }
}

/// Widget timeline refresh rule (SPEC §8.3).
final class WidgetRefreshPolicyTests: XCTestCase {
    private let now = TestSupport.date("2026-10-14T09:05:00Z")

    private func loaded(_ origin: TrendOrigin, hint: Date?) throws -> LoadedTrends {
        var snap = try TestSupport.sampleSnapshot()
        snap.nextUpdateHint = hint
        return LoadedTrends(snapshot: snap, origin: origin, fetchedAt: now)
    }

    func testNetworkUsesHintPlusTwoMinutes() throws {
        let r = WidgetRefreshPolicy.nextRefresh(for: try loaded(.network, hint: now.addingTimeInterval(25 * 60)), now: now)
        XCTAssertEqual(r, now.addingTimeInterval(27 * 60))
    }

    func testNetworkClampsToFifteenAndSixtyMinutes() throws {
        XCTAssertEqual(WidgetRefreshPolicy.nextRefresh(for: try loaded(.network, hint: now.addingTimeInterval(-600)), now: now),
                       now.addingTimeInterval(15 * 60))
        XCTAssertEqual(WidgetRefreshPolicy.nextRefresh(for: try loaded(.network, hint: now.addingTimeInterval(5 * 3600)), now: now),
                       now.addingTimeInterval(60 * 60))
    }

    func testFallbackOriginsRetryInFifteenMinutes() throws {
        for origin in [TrendOrigin.cache, .sample] {
            XCTAssertEqual(WidgetRefreshPolicy.nextRefresh(for: try loaded(origin, hint: now.addingTimeInterval(1800)), now: now),
                           now.addingTimeInterval(15 * 60))
        }
    }
}
