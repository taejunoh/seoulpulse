import XCTest
@testable import SeoulPulse

/// (a) bundled sample decodes with expected values; (b) tolerance: unknown keys, unknown enums, nulls.
final class DecodingTests: XCTestCase {

    // MARK: (a) Bundled sample

    func testBundledSampleDecodesTopLevel() throws {
        let snap = try TestSupport.sampleSnapshot()
        XCTAssertEqual(snap.schemaVersion, 1)
        XCTAssertEqual(snap.generatedAt, TestSupport.date("2026-10-14T09:00:00Z"))
        XCTAssertEqual(snap.nextUpdateHint, TestSupport.date("2026-10-14T09:30:00Z"))
        XCTAssertEqual(snap.rankChangeBasisAt, TestSupport.date("2026-10-14T03:00:00Z"))
        XCTAssertFalse(snap.coldStart)
        XCTAssertEqual(snap.items.count, 10)
        XCTAssertEqual(snap.items.map(\.rank), Array(1...10))
        XCTAssertEqual(snap.items.map(\.id), ["kiss-of-life", "aespa", "lim-young-woong", "seventeen", "le-sserafim",
                                              "illit", "stray-kids", "tws", "blackpink", "katseye"])
    }

    func testBundledSampleFirstItemFields() throws {
        let item = try XCTUnwrap(try TestSupport.sampleSnapshot().item(id: "kiss-of-life"))
        XCTAssertEqual(item.rank, 1)
        XCTAssertNil(item.prevRank)
        XCTAssertEqual(item.change, .new)
        XCTAssertEqual(item.changeAmount, 0)
        XCTAssertEqual(item.name, "KISS OF LIFE")
        XCTAssertEqual(item.nameKo, "키스오브라이프")
        XCTAssertEqual(item.score, 67.0, accuracy: 1e-9)
        XCTAssertTrue(item.koreaFirst)
        XCTAssertEqual(item.why, "\"Lucky Seven\" views up 4.0x vs. normal decay")
        XCTAssertEqual(item.whySource, "template")
        XCTAssertEqual(item.imageUrl?.absoluteString, "https://i.ytimg.com/vi/SAMPLE0001A/hqdefault.jpg")

        let m = item.metrics
        XCTAssertEqual(m.ytViewsPerHour, 20000)
        XCTAssertFalse(m.ytViewsPerHourEstimated)
        XCTAssertEqual(try XCTUnwrap(m.ytAccel), 4.0, accuracy: 1e-9)
        XCTAssertEqual(m.wikiDay, "2026-10-13")
        XCTAssertEqual(m.wikiKoViews, 1300)
        XCTAssertEqual(try XCTUnwrap(m.wikiKoRatio), 3.25, accuracy: 1e-9)
        XCTAssertEqual(m.wikiEnViews, 1560)
        XCTAssertEqual(try XCTUnwrap(m.wikiEnRatio), 1.04, accuracy: 1e-9)
        XCTAssertEqual(m.headlines24h, 1)
        XCTAssertEqual(m.headlinesKo24h, 1)

        let s = item.signals
        XCTAssertEqual(s.youtube, 0.814, accuracy: 1e-9)
        XCTAssertEqual(s.accel, 1.0, accuracy: 1e-9)
        XCTAssertEqual(s.newRelease, 0.0, accuracy: 1e-9)
        XCTAssertEqual(s.wiki, 0.683, accuracy: 1e-9)
        XCTAssertEqual(s.news, 0.26, accuracy: 1e-9)

        let v = try XCTUnwrap(item.topVideo)
        XCTAssertEqual(v.videoId, "SAMPLE0001A")
        XCTAssertEqual(v.title, "KISS OF LIFE (키스오브라이프) 'Lucky Seven' Official MV")
        XCTAssertEqual(v.url.absoluteString, "https://www.youtube.com/watch?v=SAMPLE0001A")
        XCTAssertEqual(v.kind, .mv)
        XCTAssertEqual(v.publishedAt, TestSupport.date("2026-10-05T09:00:00Z"))
        XCTAssertEqual(v.views, 1_620_000)
        XCTAssertEqual(v.channelName, "KISS OF LIFE")

        XCTAssertEqual(item.headlines.count, 1)
        let h = try XCTUnwrap(item.headlines.first)
        XCTAssertEqual(h.title, "[Sample] 키스오브라이프 '럭키 세븐' 역주행…음원차트 재진입")
        XCTAssertEqual(h.url.absoluteString, "https://www.yna.co.kr/entertainment/all")
        XCTAssertEqual(h.sourceId, "yonhap")
        XCTAssertEqual(h.sourceName, "Yonhap")
        XCTAssertEqual(h.lang, "ko")
        XCTAssertEqual(h.langLabel, "KR")
        XCTAssertEqual(h.publishedAt, TestSupport.date("2026-10-14T05:00:00Z"))

        XCTAssertEqual(item.links.youtubeChannel?.absoluteString,
                       "https://www.youtube.com/channel/UCvEEeBssb4XxIfWWIB8IjMw")
        XCTAssertEqual(item.links.wikipediaEn?.absoluteString, "https://en.wikipedia.org/wiki/Kiss_of_Life_(group)")
        XCTAssertNil(item.links.wikipediaKo)
    }

    func testBundledSampleOtherItems() throws {
        let snap = try TestSupport.sampleSnapshot()

        let aespa = try XCTUnwrap(snap.item(id: "aespa"))
        XCTAssertEqual(aespa.prevRank, 4)
        XCTAssertEqual(aespa.change, .up)
        XCTAssertEqual(aespa.changeAmount, 2)
        XCTAssertEqual(aespa.headlines.map(\.sourceId), ["soompi", "yonhap", "yonhap"])
        XCTAssertEqual(aespa.headlines.first?.langLabel, "EN")
        XCTAssertEqual(aespa.topVideo?.channelName, "SMTOWN")

        let seventeen = try XCTUnwrap(snap.item(id: "seventeen"))
        XCTAssertEqual(seventeen.change, .down)
        XCTAssertEqual(seventeen.changeAmount, 2)
        XCTAssertNil(seventeen.metrics.ytAccel)
        XCTAssertTrue(seventeen.metrics.ytViewsPerHourEstimated)
        XCTAssertEqual(seventeen.topVideo?.kind, .perf)
        XCTAssertEqual(seventeen.links.wikipediaKo?.absoluteString,
                       "https://ko.wikipedia.org/wiki/%EC%84%B8%EB%B8%90%ED%8B%B4_(%EC%9D%8C%EC%95%85_%EA%B7%B8%EB%A3%B9)")

        let lim = try XCTUnwrap(snap.item(id: "lim-young-woong"))
        XCTAssertEqual(lim.change, .same)
        XCTAssertNil(lim.links.youtubeChannel)

        let illit = try XCTUnwrap(snap.item(id: "illit"))
        XCTAssertEqual(illit.topVideo?.kind, .teaser)

        let tws = try XCTUnwrap(snap.item(id: "tws"))
        XCTAssertNil(tws.imageUrl)
        XCTAssertNil(tws.topVideo)
        XCTAssertTrue(tws.headlines.isEmpty)

        XCTAssertEqual(snap.item(id: "stray-kids")?.nameKo, "스트레이 키즈")
        XCTAssertNil(snap.item(id: "nope"))
    }

    func testBundledSampleSources() throws {
        let sources = try TestSupport.sampleSnapshot().sources
        XCTAssertEqual(sources.map(\.id), ["youtube", "wikipedia", "yonhap", "soompi", "koreaboo"])
        XCTAssertEqual(sources.map(\.status), [.ok, .ok, .ok, .ok, .error])
        let koreaboo = sources[4]
        XCTAssertEqual(koreaboo.name, "Koreaboo")
        XCTAssertEqual(koreaboo.lastSuccessAt, TestSupport.date("2026-10-14T08:30:00Z"))
        XCTAssertEqual(koreaboo.detail, "timeout after 2 retries")
    }

    /// DoD: `Shared/Resources/sample-trends.json` must be byte-identical to `docs/trends.example.json`.
    func testBundledSampleIsByteIdenticalToContract() throws {
        let docs = TestSupport.repoDirectory.appendingPathComponent("docs/trends.example.json")
        let shared = TestSupport.iosDirectory.appendingPathComponent("Shared/Resources/sample-trends.json")
        guard FileManager.default.fileExists(atPath: docs.path) else {
            throw XCTSkip("docs/trends.example.json not reachable from the test process")
        }
        let docsData = try Data(contentsOf: docs)
        XCTAssertEqual(try Data(contentsOf: shared), docsData, "source copy differs from docs/trends.example.json")
        XCTAssertEqual(try TestSupport.sampleData(), docsData, "bundled copy differs from docs/trends.example.json")
    }

    // MARK: (b) Tolerance

    func testUnknownKeysAndUnknownEnumValues() throws {
        let data = try TestSupport.mutatedSample { root in
            root["schema_version"] = 2
            root["brand_new_top_level"] = ["anything": [1, 2, 3]]
            var items = root["items"] as! [[String: Any]]
            items[0]["change"] = "sideways"
            items[0]["future_field"] = "x"
            items[0]["why_source"] = "human"
            var metrics = items[0]["metrics"] as! [String: Any]
            metrics["tiktok_views"] = 123
            items[0]["metrics"] = metrics
            var signals = items[0]["signals"] as! [String: Any]
            signals["tiktok"] = 0.5
            items[0]["signals"] = signals
            var video = items[0]["top_video"] as! [String: Any]
            video["kind"] = "livestream"
            video["duration_s"] = 213
            items[0]["top_video"] = video
            var headlines = items[0]["headlines"] as! [[String: Any]]
            headlines[0]["source_id"] = "newsis"
            headlines[0]["lang"] = "ja"
            headlines[0]["summary"] = "never shown"
            items[0]["headlines"] = headlines
            var links = items[0]["links"] as! [String: Any]
            links["instagram"] = "https://instagram.com/x"
            items[0]["links"] = links
            root["items"] = items
            var sources = root["sources"] as! [[String: Any]]
            sources[0]["status"] = "maintenance"
            sources[0]["latency_ms"] = 42
            root["sources"] = sources
        }
        let snap = try TrendsSnapshot.decode(data)
        XCTAssertEqual(snap.schemaVersion, 2)
        XCTAssertEqual(snap.items.count, 10)
        let item = snap.items[0]
        XCTAssertEqual(item.change, .same, "unknown change maps to .same")
        XCTAssertEqual(item.whySource, "human")
        XCTAssertEqual(item.topVideo?.kind, .other, "unknown kind maps to .other")
        XCTAssertEqual(item.headlines.first?.sourceId, "newsis")
        XCTAssertEqual(item.headlines.first?.langLabel, "JA")
        XCTAssertEqual(snap.sources[0].status, .degraded, "unknown status maps to .degraded")
    }

    func testNullOptionalsAllowedBySchema() throws {
        let data = try TestSupport.mutatedSample { root in
            root["rank_change_basis_at"] = NSNull()
            var items = root["items"] as! [[String: Any]]
            items[0]["prev_rank"] = NSNull()
            items[0]["name_ko"] = NSNull()
            items[0]["image_url"] = NSNull()
            items[0]["top_video"] = NSNull()
            items[0]["metrics"] = [
                "yt_views_per_hour": NSNull(), "yt_views_per_hour_estimated": false, "yt_accel": NSNull(),
                "wiki_day": NSNull(), "wiki_ko_views": NSNull(), "wiki_ko_ratio": NSNull(),
                "wiki_en_views": NSNull(), "wiki_en_ratio": NSNull(), "headlines_24h": 0, "headlines_ko_24h": 0,
            ]
            items[0]["links"] = ["youtube_channel": NSNull(), "wikipedia_en": NSNull(), "wikipedia_ko": NSNull()]
            root["items"] = items
            var sources = root["sources"] as! [[String: Any]]
            sources[1]["last_success_at"] = NSNull()
            root["sources"] = sources
        }
        let snap = try TrendsSnapshot.decode(data)
        XCTAssertNil(snap.rankChangeBasisAt)
        let item = snap.items[0]
        XCTAssertNil(item.prevRank)
        XCTAssertNil(item.nameKo)
        XCTAssertNil(item.imageUrl)
        XCTAssertNil(item.topVideo)
        XCTAssertNil(item.metrics.ytViewsPerHour)
        XCTAssertNil(item.metrics.ytAccel)
        XCTAssertNil(item.metrics.wikiDay)
        XCTAssertNil(item.metrics.wikiKoRatio)
        XCTAssertNil(item.metrics.wikiEnViews)
        XCTAssertTrue(item.links.isEmpty)
        XCTAssertNil(snap.sources[1].lastSuccessAt)
    }

    func testNullsInRequiredFieldsFallBackToDefaults() throws {
        let data = try TestSupport.mutatedSample { root in
            root["cold_start"] = NSNull()
            var items = root["items"] as! [[String: Any]]
            items[0]["score"] = NSNull()
            items[0]["change"] = NSNull()
            items[0]["headlines"] = NSNull()
            items[0]["metrics"] = NSNull()
            items[0]["signals"] = NSNull()
            items[0].removeValue(forKey: "links")
            root["items"] = items
        }
        let snap = try TrendsSnapshot.decode(data)
        XCTAssertFalse(snap.coldStart)
        let item = snap.items[0]
        XCTAssertEqual(item.id, "kiss-of-life")
        XCTAssertEqual(item.score, 0)
        XCTAssertEqual(item.change, .same)
        XCTAssertTrue(item.headlines.isEmpty)
        XCTAssertEqual(item.metrics, TrendMetrics())
        XCTAssertEqual(item.signals, TrendSignals())
        XCTAssertTrue(item.links.isEmpty)
    }

    func testEmptyItems() throws {
        let data = try TestSupport.mutatedSample { root in
            root["items"] = [Any]()
            root["cold_start"] = true
        }
        let snap = try TrendsSnapshot.decode(data)
        XCTAssertTrue(snap.items.isEmpty)
        XCTAssertTrue(snap.coldStart)
        XCTAssertEqual(snap.sources.count, 5)
    }

    func testMalformedItemIsSkippedNotFatal() throws {
        let data = try TestSupport.mutatedSample { root in
            var items = root["items"] as! [[String: Any]]
            items[1].removeValue(forKey: "id")          // aespa loses its id
            items[2]["rank"] = "three"                   // wrong type
            root["items"] = items
        }
        let snap = try TrendsSnapshot.decode(data)
        XCTAssertEqual(snap.items.count, 8)
        XCTAssertNil(snap.item(id: "aespa"))
        XCTAssertNil(snap.item(id: "lim-young-woong"))
        XCTAssertNotNil(snap.item(id: "seventeen"))
    }

    func testItemsAreSortedByRank() throws {
        let data = try TestSupport.mutatedSample { root in
            root["items"] = Array((root["items"] as! [[String: Any]]).reversed())
        }
        XCTAssertEqual(try TrendsSnapshot.decode(data).items.map(\.rank), Array(1...10))
    }

    func testRejectsPayloadsWithoutEssentials() throws {
        XCTAssertThrowsError(try TrendsSnapshot.decode(Data("not json".utf8)))
        XCTAssertThrowsError(try TrendsSnapshot.decode(Data("[]".utf8)))
        let noDate = try TestSupport.mutatedSample { $0.removeValue(forKey: "generated_at") }
        XCTAssertThrowsError(try TrendsSnapshot.decode(noDate))
        let badDate = try TestSupport.mutatedSample { $0["generated_at"] = "yesterday" }
        XCTAssertThrowsError(try TrendsSnapshot.decode(badDate))
        let noItems = try TestSupport.mutatedSample { $0.removeValue(forKey: "items") }
        XCTAssertThrowsError(try TrendsSnapshot.decode(noItems))
    }

    func testMinimalPayload() throws {
        let json = """
        {"schema_version":1,"generated_at":"2026-10-14T09:00:00Z","items":[{"id":"x","rank":1,"name":"X"}]}
        """
        let snap = try TrendsSnapshot.decode(Data(json.utf8))
        XCTAssertEqual(snap.items.first?.name, "X")
        XCTAssertNil(snap.nextUpdateHint)
        XCTAssertTrue(snap.sources.isEmpty)
    }
}
