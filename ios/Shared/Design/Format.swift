import Foundation

enum Format {
    /// Same thresholds as the backend's `fmt(n)` (SPEC §6):
    /// <1000 -> integer, <10K -> "7.6K" (one decimal, ".0" dropped), <1M -> "127K", else "1.2M".
    static func compact(_ n: Int) -> String {
        if n < 0 { return "-" + compact(-n) }
        if n < 1_000 { return "\(n)" }
        if n < 10_000 {
            return trimZero(String(format: "%.1f", Double(n) / 1_000)) + "K"
        }
        if n < 1_000_000 {
            let k = String(format: "%.0f", Double(n) / 1_000)
            if k != "1000" { return k + "K" }
        }
        return trimZero(String(format: "%.1f", Double(n) / 1_000_000)) + "M"
    }

    /// Ratio / multiplier with up to two decimals, keeping at least one: 3.25 -> "3.25x", 1.7 -> "1.7x", 3 -> "3.0x".
    static func ratio(_ x: Double) -> String {
        var s = String(format: "%.2f", x)
        if s.hasSuffix("0") { s.removeLast() }
        return s + "x"
    }

    /// Signal value 0.814 -> "0.81", -0.03 -> "-0.03".
    static func signal(_ x: Double) -> String {
        let s = String(format: "%.2f", x)
        return s == "-0.00" ? "0.00" : s
    }

    /// "just now", "8m ago", "3h ago", "2d ago". Future dates (clock skew) read as "just now".
    static func relative(_ date: Date, now: Date = Date()) -> String {
        let seconds = now.timeIntervalSince(date)
        if seconds < 60 { return "just now" }
        let minutes = Int(seconds / 60)
        if minutes < 60 { return "\(minutes)m ago" }
        let hours = minutes / 60
        if hours < 24 { return "\(hours)h ago" }
        return "\(hours / 24)d ago"
    }

    /// "18:00 KST"
    static func kstTime(_ date: Date) -> String {
        kstFormatter.string(from: date) + " KST"
    }

    /// "Oct 14, 18:00 KST"
    static func kstDateTime(_ date: Date) -> String {
        kstDateFormatter.string(from: date) + " KST"
    }

    private static func trimZero(_ s: String) -> String {
        s.hasSuffix(".0") ? String(s.dropLast(2)) : s
    }

    private static let kstFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "Asia/Seoul")
        f.dateFormat = "HH:mm"
        return f
    }()

    private static let kstDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "Asia/Seoul")
        f.dateFormat = "MMM d, HH:mm"
        return f
    }()
}

extension TrendsSnapshot {
    /// Widget "Stale" rule: generated more than 3 h ago.
    func isStale(now: Date) -> Bool { now.timeIntervalSince(generatedAt) > 3 * 3600 }
    /// App banner rule: more than a day old.
    func isOverADayOld(now: Date) -> Bool { now.timeIntervalSince(generatedAt) > 24 * 3600 }
}
