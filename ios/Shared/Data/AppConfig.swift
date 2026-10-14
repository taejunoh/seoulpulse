import Foundation

/// Where the effective feed URL came from (shown in Settings).
enum TrendsURLSource: String, Sendable {
    case override
    case infoPlist
    case builtIn

    var label: String {
        switch self {
        case .override: return "Override (Settings)"
        case .infoPlist: return "Info.plist (SPTrendsURL)"
        case .builtIn: return "Built-in default"
        }
    }
}

enum AppConfig {
    static let defaultTrendsURLString = "https://taejunoh.com/seoulpulse/trends.json"
    static let overrideKey = "trendsURLOverride"
    static let infoPlistURLKey = "SPTrendsURL"
    static let infoPlistAppGroupKey = "SPAppGroupID"
    static let defaultAppGroupID = "group.com.taejunoh.seoulpulse"
    static let widgetKind = "SeoulPulseTrendsWidget"

    static var appGroupID: String {
        (Bundle.main.object(forInfoDictionaryKey: infoPlistAppGroupKey) as? String) ?? defaultAppGroupID
    }

    /// App Group defaults shared by app and widget (falls back to `.standard` if the suite is unavailable).
    static var sharedDefaults: UserDefaults {
        UserDefaults(suiteName: appGroupID) ?? .standard
    }

    /// A valid feed URL is http(s) with a non-empty host.
    static func validatedURL(_ string: String?) -> URL? {
        guard let trimmed = string?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty,
              let comps = URLComponents(string: trimmed),
              let scheme = comps.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = comps.host, !host.isEmpty,
              let url = comps.url
        else { return nil }
        return url
    }

    /// Resolution order (SPEC §8.2): App Group override -> Info.plist `SPTrendsURL` -> built-in constant.
    static func resolveTrendsURL(defaults: UserDefaults? = sharedDefaults,
                                 info: [String: Any]? = Bundle.main.infoDictionary) -> (url: URL, source: TrendsURLSource) {
        if let override = validatedURL(defaults?.string(forKey: overrideKey)) {
            return (override, .override)
        }
        if let plist = validatedURL(info?[infoPlistURLKey] as? String) {
            return (plist, .infoPlist)
        }
        return (URL(string: defaultTrendsURLString)!, .builtIn)
    }

    static var trendsURL: URL { resolveTrendsURL().url }

    static var overrideString: String? {
        let raw = sharedDefaults.string(forKey: overrideKey)?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (raw?.isEmpty ?? true) ? nil : raw
    }

    /// Saves (or clears, when `nil`/empty) the override. Returns false if the value is not a valid URL.
    @discardableResult
    static func setOverride(_ string: String?, defaults: UserDefaults = sharedDefaults) -> Bool {
        let trimmed = string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if trimmed.isEmpty {
            defaults.removeObject(forKey: overrideKey)
            return true
        }
        guard let url = validatedURL(trimmed) else { return false }
        defaults.set(url.absoluteString, forKey: overrideKey)
        return true
    }

    /// App Group container, or this process's own Caches directory when the group is unavailable.
    static func sharedContainerDirectory() -> URL {
        if let group = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroupID) {
            return group
        }
        return FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
    }

    static var appVersion: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
        return "\(v) (\(b))"
    }
}
