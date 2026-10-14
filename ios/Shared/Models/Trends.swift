import Foundation

// Codable model for `trends.json` (docs/trends.schema.json, schema_version 1).
//
// Consumer rules (SPEC §7):
// - unknown keys are ignored (Decodable does this by default),
// - unknown enum values map to a fallback (`RankChange.same`, `VideoKind.other`, `SourceState.degraded`),
// - nulls are tolerated wherever the schema allows them; for robustness, a null or missing
//   non-essential field falls back to a neutral default instead of failing the whole payload,
// - `wiki_day` stays a String.
// Keys are decoded with `.convertFromSnakeCase`, so `headlines_24h` -> `headlines24h` etc.

// MARK: - Enums with unknown fallback

enum RankChange: String, Sendable, CaseIterable {
    case new, up, down, same
}

extension RankChange: Decodable {
    init(from decoder: Decoder) throws {
        let raw = try? decoder.singleValueContainer().decode(String.self)
        self = raw.flatMap(RankChange.init(rawValue:)) ?? .same
    }
}

enum VideoKind: String, Sendable, CaseIterable {
    case mv, perf, teaser, show, other

    var label: String {
        switch self {
        case .mv: return "MV"
        case .perf: return "Performance"
        case .teaser: return "Teaser"
        case .show: return "Music show"
        case .other: return "Video"
        }
    }
}

extension VideoKind: Decodable {
    init(from decoder: Decoder) throws {
        let raw = try? decoder.singleValueContainer().decode(String.self)
        self = raw.flatMap(VideoKind.init(rawValue:)) ?? .other
    }
}

enum SourceState: String, Sendable, CaseIterable {
    case ok, degraded, error, disabled

    var label: String {
        switch self {
        case .ok: return "OK"
        case .degraded: return "Degraded"
        case .error: return "Error"
        case .disabled: return "Disabled"
        }
    }
}

extension SourceState: Decodable {
    init(from decoder: Decoder) throws {
        let raw = try? decoder.singleValueContainer().decode(String.self)
        self = raw.flatMap(SourceState.init(rawValue:)) ?? .degraded
    }
}

// MARK: - Snapshot

struct TrendsSnapshot: Decodable, Equatable, Sendable {
    var schemaVersion: Int
    var generatedAt: Date
    var nextUpdateHint: Date?
    var rankChangeBasisAt: Date?
    var coldStart: Bool
    var items: [TrendItem]
    var sources: [SourceStatus]

    init(schemaVersion: Int = 1, generatedAt: Date, nextUpdateHint: Date? = nil,
         rankChangeBasisAt: Date? = nil, coldStart: Bool = false,
         items: [TrendItem] = [], sources: [SourceStatus] = []) {
        self.schemaVersion = schemaVersion
        self.generatedAt = generatedAt
        self.nextUpdateHint = nextUpdateHint
        self.rankChangeBasisAt = rankChangeBasisAt
        self.coldStart = coldStart
        self.items = items
        self.sources = sources
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, generatedAt, nextUpdateHint, rankChangeBasisAt, coldStart, items, sources
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = (try? c.decodeIfPresent(Int.self, forKey: .schemaVersion)) ?? 1
        // generated_at is the one field the UI cannot do without; a payload without it is rejected.
        generatedAt = try c.decode(Date.self, forKey: .generatedAt)
        nextUpdateHint = try? c.decodeIfPresent(Date.self, forKey: .nextUpdateHint)
        rankChangeBasisAt = try? c.decodeIfPresent(Date.self, forKey: .rankChangeBasisAt)
        coldStart = (try? c.decodeIfPresent(Bool.self, forKey: .coldStart)) ?? false
        // `items` must be present (an array, possibly empty); individual malformed items are skipped.
        let lossyItems = try c.decode([Lossy<TrendItem>].self, forKey: .items)
        items = lossyItems.compactMap(\.value).sorted { $0.rank < $1.rank }
        let lossySources = (try? c.decodeIfPresent([Lossy<SourceStatus>].self, forKey: .sources)) ?? []
        sources = lossySources.compactMap(\.value)
    }

    func item(id: String) -> TrendItem? { items.first { $0.id == id } }

    /// Shared decoder configuration (SPEC §8.2).
    static func makeDecoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .iso8601
        return d
    }

    static func decode(_ data: Data) throws -> TrendsSnapshot {
        try makeDecoder().decode(TrendsSnapshot.self, from: data)
    }
}

// MARK: - Item

struct TrendItem: Decodable, Equatable, Sendable, Identifiable {
    var id: String
    var rank: Int
    var prevRank: Int?
    var change: RankChange
    var changeAmount: Int
    var name: String
    var nameKo: String?
    var score: Double
    var koreaFirst: Bool
    var why: String
    var whySource: String
    var imageUrl: URL?
    var metrics: TrendMetrics
    var signals: TrendSignals
    var topVideo: TopVideo?
    var headlines: [Headline]
    var links: TrendLinks

    private enum CodingKeys: String, CodingKey {
        case id, rank, prevRank, change, changeAmount, name, nameKo, score, koreaFirst, why, whySource,
             imageUrl, metrics, signals, topVideo, headlines, links
    }

    init(id: String, rank: Int, prevRank: Int? = nil, change: RankChange = .same, changeAmount: Int = 0,
         name: String, nameKo: String? = nil, score: Double = 0, koreaFirst: Bool = false, why: String = "",
         whySource: String = "template", imageUrl: URL? = nil, metrics: TrendMetrics = TrendMetrics(),
         signals: TrendSignals = TrendSignals(), topVideo: TopVideo? = nil, headlines: [Headline] = [],
         links: TrendLinks = TrendLinks()) {
        self.id = id; self.rank = rank; self.prevRank = prevRank; self.change = change
        self.changeAmount = changeAmount; self.name = name; self.nameKo = nameKo; self.score = score
        self.koreaFirst = koreaFirst; self.why = why; self.whySource = whySource; self.imageUrl = imageUrl
        self.metrics = metrics; self.signals = signals; self.topVideo = topVideo; self.headlines = headlines
        self.links = links
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        rank = try c.decode(Int.self, forKey: .rank)
        name = try c.decode(String.self, forKey: .name)
        prevRank = try? c.decodeIfPresent(Int.self, forKey: .prevRank)
        change = (try? c.decodeIfPresent(RankChange.self, forKey: .change)) ?? .same
        changeAmount = (try? c.decodeIfPresent(Int.self, forKey: .changeAmount)) ?? 0
        nameKo = (try? c.decodeIfPresent(String.self, forKey: .nameKo)).flatMap { $0.isEmpty ? nil : $0 }
        score = (try? c.decodeIfPresent(Double.self, forKey: .score)) ?? 0
        koreaFirst = (try? c.decodeIfPresent(Bool.self, forKey: .koreaFirst)) ?? false
        why = (try? c.decodeIfPresent(String.self, forKey: .why)) ?? ""
        whySource = (try? c.decodeIfPresent(String.self, forKey: .whySource)) ?? "template"
        imageUrl = (try? c.decodeIfPresent(String.self, forKey: .imageUrl)).flatMap { URL(string: $0) }
        metrics = (try? c.decodeIfPresent(TrendMetrics.self, forKey: .metrics)) ?? TrendMetrics()
        signals = (try? c.decodeIfPresent(TrendSignals.self, forKey: .signals)) ?? TrendSignals()
        topVideo = (try? c.decodeIfPresent(TopVideo.self, forKey: .topVideo)) ?? nil
        let lossyHeadlines = (try? c.decodeIfPresent([Lossy<Headline>].self, forKey: .headlines)) ?? []
        headlines = lossyHeadlines.compactMap(\.value)
        links = (try? c.decodeIfPresent(TrendLinks.self, forKey: .links)) ?? TrendLinks()
    }
}

struct TrendMetrics: Decodable, Equatable, Sendable {
    var ytViewsPerHour: Int?
    var ytViewsPerHourEstimated: Bool = false
    var ytAccel: Double?
    var wikiDay: String?
    var wikiKoViews: Int?
    var wikiKoRatio: Double?
    var wikiEnViews: Int?
    var wikiEnRatio: Double?
    var headlines24h: Int = 0
    var headlinesKo24h: Int = 0

    init(ytViewsPerHour: Int? = nil, ytViewsPerHourEstimated: Bool = false, ytAccel: Double? = nil,
         wikiDay: String? = nil, wikiKoViews: Int? = nil, wikiKoRatio: Double? = nil, wikiEnViews: Int? = nil,
         wikiEnRatio: Double? = nil, headlines24h: Int = 0, headlinesKo24h: Int = 0) {
        self.ytViewsPerHour = ytViewsPerHour; self.ytViewsPerHourEstimated = ytViewsPerHourEstimated
        self.ytAccel = ytAccel; self.wikiDay = wikiDay; self.wikiKoViews = wikiKoViews
        self.wikiKoRatio = wikiKoRatio; self.wikiEnViews = wikiEnViews; self.wikiEnRatio = wikiEnRatio
        self.headlines24h = headlines24h; self.headlinesKo24h = headlinesKo24h
    }

    private enum CodingKeys: String, CodingKey {
        case ytViewsPerHour, ytViewsPerHourEstimated, ytAccel, wikiDay, wikiKoViews, wikiKoRatio,
             wikiEnViews, wikiEnRatio
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ytViewsPerHour = (try? c.decodeIfPresent(Int.self, forKey: .ytViewsPerHour)) ?? nil
        ytViewsPerHourEstimated = (try? c.decodeIfPresent(Bool.self, forKey: .ytViewsPerHourEstimated)) ?? false
        ytAccel = (try? c.decodeIfPresent(Double.self, forKey: .ytAccel)) ?? nil
        wikiDay = (try? c.decodeIfPresent(String.self, forKey: .wikiDay)) ?? nil
        wikiKoViews = (try? c.decodeIfPresent(Int.self, forKey: .wikiKoViews)) ?? nil
        wikiKoRatio = (try? c.decodeIfPresent(Double.self, forKey: .wikiKoRatio)) ?? nil
        wikiEnViews = (try? c.decodeIfPresent(Int.self, forKey: .wikiEnViews)) ?? nil
        wikiEnRatio = (try? c.decodeIfPresent(Double.self, forKey: .wikiEnRatio)) ?? nil
        // `.convertFromSnakeCase` turns `headlines_24h` into `headlines24H` (Foundation capitalizes "24h"
        // to "24H"), so a plain `headlines24h` key never matches. Accept every spelling explicitly.
        let any = try decoder.container(keyedBy: AnyCodingKey.self)
        func firstInt(_ names: [String]) -> Int? {
            for name in names {
                if let v = try? any.decodeIfPresent(Int.self, forKey: AnyCodingKey(name)) { return v }
            }
            return nil
        }
        headlines24h = firstInt(["headlines24H", "headlines24h", "headlines_24h"]) ?? 0
        headlinesKo24h = firstInt(["headlinesKo24H", "headlinesKo24h", "headlines_ko_24h"]) ?? 0
    }
}

struct TrendSignals: Decodable, Equatable, Sendable {
    var youtube: Double = 0
    var accel: Double = 0
    var newRelease: Double = 0
    var wiki: Double = 0
    var news: Double = 0

    init(youtube: Double = 0, accel: Double = 0, newRelease: Double = 0, wiki: Double = 0, news: Double = 0) {
        self.youtube = youtube; self.accel = accel; self.newRelease = newRelease; self.wiki = wiki; self.news = news
    }

    private enum CodingKeys: String, CodingKey { case youtube, accel, newRelease, wiki, news }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        youtube = (try? c.decodeIfPresent(Double.self, forKey: .youtube)) ?? 0
        accel = (try? c.decodeIfPresent(Double.self, forKey: .accel)) ?? 0
        newRelease = (try? c.decodeIfPresent(Double.self, forKey: .newRelease)) ?? 0
        wiki = (try? c.decodeIfPresent(Double.self, forKey: .wiki)) ?? 0
        news = (try? c.decodeIfPresent(Double.self, forKey: .news)) ?? 0
    }
}

struct TopVideo: Decodable, Equatable, Sendable {
    var videoId: String
    var title: String
    var url: URL
    var kind: VideoKind
    var publishedAt: Date?
    var views: Int
    var channelName: String

    private enum CodingKeys: String, CodingKey { case videoId, title, url, kind, publishedAt, views, channelName }

    init(videoId: String, title: String, url: URL, kind: VideoKind = .other, publishedAt: Date? = nil,
         views: Int = 0, channelName: String = "") {
        self.videoId = videoId; self.title = title; self.url = url; self.kind = kind
        self.publishedAt = publishedAt; self.views = views; self.channelName = channelName
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        videoId = try c.decode(String.self, forKey: .videoId)
        title = try c.decode(String.self, forKey: .title)
        url = try c.decode(URL.self, forKey: .url)
        kind = (try? c.decodeIfPresent(VideoKind.self, forKey: .kind)) ?? .other
        publishedAt = (try? c.decodeIfPresent(Date.self, forKey: .publishedAt)) ?? nil
        views = (try? c.decodeIfPresent(Int.self, forKey: .views)) ?? 0
        channelName = (try? c.decodeIfPresent(String.self, forKey: .channelName)) ?? ""
    }
}

struct Headline: Decodable, Equatable, Sendable, Identifiable {
    var title: String
    var url: URL
    var sourceId: String
    var sourceName: String
    var lang: String
    var publishedAt: Date?

    var id: String { "\(sourceId)|\(url.absoluteString)|\(title)" }
    var isKorean: Bool { lang == "ko" }
    /// "KR" / "EN" for the source pill (`Yonhap · KR`).
    var langLabel: String { isKorean ? "KR" : lang.uppercased() }

    private enum CodingKeys: String, CodingKey { case title, url, sourceId, sourceName, lang, publishedAt }

    init(title: String, url: URL, sourceId: String, sourceName: String, lang: String, publishedAt: Date?) {
        self.title = title; self.url = url; self.sourceId = sourceId; self.sourceName = sourceName
        self.lang = lang; self.publishedAt = publishedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decode(String.self, forKey: .title)
        url = try c.decode(URL.self, forKey: .url)
        sourceId = (try? c.decodeIfPresent(String.self, forKey: .sourceId)) ?? ""
        let name = (try? c.decodeIfPresent(String.self, forKey: .sourceName)) ?? nil
        sourceName = name ?? sourceId.capitalized
        lang = (try? c.decodeIfPresent(String.self, forKey: .lang)) ?? "en"
        publishedAt = (try? c.decodeIfPresent(Date.self, forKey: .publishedAt)) ?? nil
    }
}

struct TrendLinks: Decodable, Equatable, Sendable {
    var youtubeChannel: URL?
    var wikipediaEn: URL?
    var wikipediaKo: URL?

    init(youtubeChannel: URL? = nil, wikipediaEn: URL? = nil, wikipediaKo: URL? = nil) {
        self.youtubeChannel = youtubeChannel; self.wikipediaEn = wikipediaEn; self.wikipediaKo = wikipediaKo
    }

    private enum CodingKeys: String, CodingKey { case youtubeChannel, wikipediaEn, wikipediaKo }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func url(_ key: CodingKeys) -> URL? {
            (try? c.decodeIfPresent(String.self, forKey: key)).flatMap { URL(string: $0) }
        }
        youtubeChannel = url(.youtubeChannel)
        wikipediaEn = url(.wikipediaEn)
        wikipediaKo = url(.wikipediaKo)
    }

    var isEmpty: Bool { youtubeChannel == nil && wikipediaEn == nil && wikipediaKo == nil }
}

struct SourceStatus: Decodable, Equatable, Sendable, Identifiable {
    var id: String
    var name: String
    var status: SourceState
    var lastSuccessAt: Date?
    var detail: String

    init(id: String, name: String, status: SourceState, lastSuccessAt: Date? = nil, detail: String = "") {
        self.id = id; self.name = name; self.status = status; self.lastSuccessAt = lastSuccessAt; self.detail = detail
    }

    private enum CodingKeys: String, CodingKey { case id, name, status, lastSuccessAt, detail }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = ((try? c.decodeIfPresent(String.self, forKey: .name)) ?? nil) ?? id.capitalized
        status = (try? c.decodeIfPresent(SourceState.self, forKey: .status)) ?? .degraded
        lastSuccessAt = (try? c.decodeIfPresent(Date.self, forKey: .lastSuccessAt)) ?? nil
        detail = ((try? c.decodeIfPresent(String.self, forKey: .detail)) ?? nil) ?? ""
    }
}

// MARK: - Helpers

private struct AnyCodingKey: CodingKey {
    var stringValue: String
    var intValue: Int? { nil }
    init(_ string: String) { stringValue = string }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { nil }
}

/// Decodes an element, swallowing its failure so one malformed entry can't sink the whole payload.
private struct Lossy<T: Decodable>: Decodable {
    let value: T?
    init(from decoder: Decoder) throws {
        value = try? T(from: decoder)
    }
}
