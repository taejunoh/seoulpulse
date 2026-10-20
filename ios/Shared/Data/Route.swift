import Foundation

/// App navigation routes and the `seoulpulse://` deep-link grammar (SPEC §8.4).
/// Lives in Shared so the widget builds its links from the same source of truth.
/// `seoulrising://` (the product's current name) is accepted as an alias; links are still built with
/// `seoulpulse://`, which build 1 shipped and which stays registered.
enum Route: Hashable, Sendable {
    case item(String)
    case settings
    case widgetGallery

    static let scheme = "seoulpulse"
    /// Every scheme the app registers (Info.plist `CFBundleURLSchemes`) and parses.
    static let acceptedSchemes: Set<String> = [scheme, "seoulrising"]

    /// `seoulpulse://item/<id>` -> [.item(id)], `seoulpulse://settings` -> [.settings],
    /// `seoulpulse://widget-gallery` -> [.widgetGallery], anything else -> [] (home).
    static func parse(_ url: URL) -> [Route] {
        guard let s = url.scheme?.lowercased(), acceptedSchemes.contains(s) else { return [] }
        let host = url.host?.lowercased() ?? ""
        let parts = url.pathComponents.filter { $0 != "/" }
        switch host {
        case "item":
            guard let id = parts.first?.removingPercentEncoding?.trimmingCharacters(in: .whitespaces),
                  !id.isEmpty else { return [] }
            return [.item(id)]
        case "settings":
            return [.settings]
        case "widget-gallery":
            return [.widgetGallery]
        default:
            return []
        }
    }

    var url: URL {
        switch self {
        case .item(let id): return Route.itemURL(id)
        case .settings: return URL(string: "\(Route.scheme)://settings")!
        case .widgetGallery: return URL(string: "\(Route.scheme)://widget-gallery")!
        }
    }

    static func itemURL(_ id: String) -> URL {
        let encoded = id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id
        return URL(string: "\(scheme)://item/\(encoded)") ?? URL(string: "\(scheme)://")!
    }
}
