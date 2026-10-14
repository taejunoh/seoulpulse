import Foundation
import XCTest
@testable import SeoulPulse

enum TestSupport {
    /// `ios/` directory of this checkout (derived from this source file's path).
    static var iosDirectory: URL {
        URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
    }

    static var repoDirectory: URL { iosDirectory.deletingLastPathComponent() }

    /// `$SP_SNAPSHOT_DIR` from the scheme, else `ios/build/snapshots`.
    static var snapshotDirectory: URL {
        if let env = ProcessInfo.processInfo.environment["SP_SNAPSHOT_DIR"], !env.isEmpty, !env.contains("$(") {
            return URL(fileURLWithPath: env)
        }
        return iosDirectory.appendingPathComponent("build/snapshots")
    }

    static func sampleData() throws -> Data {
        let url = try XCTUnwrap(Bundle.main.url(forResource: "sample-trends", withExtension: "json"),
                                "sample-trends.json missing from the app bundle")
        return try Data(contentsOf: url)
    }

    static func sampleSnapshot() throws -> TrendsSnapshot {
        try TrendsSnapshot.decode(sampleData())
    }

    /// Parses the sample into a mutable JSON tree, lets the test edit it, and re-serializes it.
    static func mutatedSample(_ edit: (inout [String: Any]) throws -> Void) throws -> Data {
        var root = try XCTUnwrap(try JSONSerialization.jsonObject(with: sampleData()) as? [String: Any])
        try edit(&root)
        return try JSONSerialization.data(withJSONObject: root)
    }

    static func date(_ iso: String) -> Date {
        ISO8601DateFormatter().date(from: iso)!
    }
}

/// URLProtocol stub so TrendStore tests never touch the network.
final class StubURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (Int, Data))?
    nonisolated(unsafe) static var requests: [URLRequest] = []

    static func reset() {
        handler = nil
        requests = []
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
            return
        }
        do {
            let (status, data) = try handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: "HTTP/1.1",
                                           headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}
