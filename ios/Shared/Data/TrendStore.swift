import Foundation
import os

enum TrendOrigin: String, Sendable {
    case network, cache, sample
}

struct LoadedTrends: Equatable, Sendable {
    var snapshot: TrendsSnapshot
    var origin: TrendOrigin
    var fetchedAt: Date?
}

enum TrendFetchError: LocalizedError, Equatable {
    case badStatus(Int)
    case decode(String)
    case transport(String)

    var errorDescription: String? {
        switch self {
        case .badStatus(let code): return "HTTP \(code)"
        case .decode(let msg): return "Couldn't decode trends.json (\(msg))"
        case .transport(let msg): return msg
        }
    }
}

/// Loads trends with the fallback chain network -> App Group cache -> bundled sample (SPEC §8.2, §8.5).
/// `load` never throws: the UI always gets something to show, labelled with its origin.
struct TrendStore: Sendable {
    static let cacheFileName = "trends-cache.json"
    static let sampleResourceName = "sample-trends"
    static let logger = Logger(subsystem: "com.seoulpulse.app", category: "TrendStore")

    var urlProvider: @Sendable () -> URL = { AppConfig.trendsURL }
    var cacheDirectory: @Sendable () -> URL = { AppConfig.sharedContainerDirectory() }
    var sessionConfiguration: @Sendable () -> URLSessionConfiguration = { .ephemeral }
    var sampleURL: @Sendable () -> URL? = {
        Bundle.main.url(forResource: TrendStore.sampleResourceName, withExtension: "json")
    }
    var now: @Sendable () -> Date = { Date() }

    init() {}

    var cacheFileURL: URL { cacheDirectory().appendingPathComponent(Self.cacheFileName) }

    // MARK: Chain

    func load(network: Bool = true, timeout: TimeInterval = 8) async -> LoadedTrends {
        if network {
            let url = urlProvider()
            do {
                let (snapshot, data) = try await fetch(url: url, timeout: timeout)
                let fetchedAt = now()
                writeCache(payload: data, fetchedAt: fetchedAt)
                return LoadedTrends(snapshot: snapshot, origin: .network, fetchedAt: fetchedAt)
            } catch {
                Self.logger.info("network load failed for \(url.absoluteString, privacy: .public): \(error.localizedDescription, privacy: .public)")
            }
        }
        return loadLocal()
    }

    /// Cache, else bundled sample, else an empty snapshot. No network.
    func loadLocal() -> LoadedTrends {
        if let cached = readCache() { return cached }
        return sampleLoaded()
    }

    func sampleLoaded() -> LoadedTrends {
        if let url = sampleURL(), let data = try? Data(contentsOf: url) {
            do {
                return LoadedTrends(snapshot: try TrendsSnapshot.decode(data), origin: .sample, fetchedAt: nil)
            } catch {
                Self.logger.error("bundled sample failed to decode: \(String(describing: error), privacy: .public)")
            }
        }
        return LoadedTrends(snapshot: TrendsSnapshot(generatedAt: now()), origin: .sample, fetchedAt: nil)
    }

    // MARK: Network

    /// Fetches and decodes one payload. Requires HTTP 200. Used by `load` and Settings' "Test connection".
    func fetch(url: URL, timeout: TimeInterval = 8) async throws -> (TrendsSnapshot, Data) {
        let config = sessionConfiguration()
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.urlCache = nil
        config.timeoutIntervalForRequest = timeout
        config.timeoutIntervalForResource = timeout
        let session = URLSession(configuration: config)
        defer { session.finishTasksAndInvalidate() }

        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: timeout)
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw TrendFetchError.transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else { throw TrendFetchError.transport("Not an HTTP response") }
        guard http.statusCode == 200 else { throw TrendFetchError.badStatus(http.statusCode) }
        do {
            let snapshot = try TrendsSnapshot.decode(data)
            if snapshot.schemaVersion > 1 {
                Self.logger.notice("schema_version \(snapshot.schemaVersion) > 1 decoded with v1 model")
            }
            return (snapshot, data)
        } catch {
            Self.logger.error("decode failed: \(String(describing: error), privacy: .public)")
            throw TrendFetchError.decode(Self.shortDescription(of: error))
        }
    }

    // MARK: Cache

    /// The cache file is a small envelope `{"fetched_at": "...", "payload": <raw trends.json>}`, written
    /// atomically so the payload and its fetch time can never disagree.
    func writeCache(payload: Data, fetchedAt: Date) {
        let stamp = ISO8601DateFormatter().string(from: fetchedAt)
        var envelope = Data("{\"fetched_at\":\"\(stamp)\",\"payload\":".utf8)
        envelope.append(payload)
        envelope.append(Data("}".utf8))
        do {
            try FileManager.default.createDirectory(at: cacheDirectory(), withIntermediateDirectories: true)
            try envelope.write(to: cacheFileURL, options: .atomic)
        } catch {
            Self.logger.error("cache write failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    func readCache() -> LoadedTrends? {
        guard let data = try? Data(contentsOf: cacheFileURL) else { return nil }
        do {
            let env = try TrendsSnapshot.makeDecoder().decode(CacheEnvelope.self, from: data)
            return LoadedTrends(snapshot: env.payload, origin: .cache, fetchedAt: env.fetchedAt)
        } catch {
            Self.logger.error("cache unreadable: \(String(describing: error), privacy: .public)")
            return nil
        }
    }

    func clearCache() {
        try? FileManager.default.removeItem(at: cacheFileURL)
    }

    private struct CacheEnvelope: Decodable {
        let fetchedAt: Date?
        let payload: TrendsSnapshot
    }

    private static func shortDescription(of error: Error) -> String {
        if let e = error as? DecodingError {
            switch e {
            case .keyNotFound(let key, _): return "missing \(key.stringValue)"
            case .typeMismatch(_, let ctx), .valueNotFound(_, let ctx):
                return "bad value at \(ctx.codingPath.map(\.stringValue).joined(separator: "."))"
            case .dataCorrupted: return "not valid JSON"
            @unknown default: return "invalid"
            }
        }
        return error.localizedDescription
    }
}

/// Widget timeline refresh rule (SPEC §8.3).
enum WidgetRefreshPolicy {
    static func nextRefresh(for loaded: LoadedTrends, now: Date) -> Date {
        let minDate = now.addingTimeInterval(15 * 60)
        guard loaded.origin == .network, let hint = loaded.snapshot.nextUpdateHint else { return minDate }
        let maxDate = now.addingTimeInterval(60 * 60)
        return min(max(hint.addingTimeInterval(120), minDate), maxDate)
    }
}
