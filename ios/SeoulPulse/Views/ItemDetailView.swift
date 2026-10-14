import SwiftUI

struct ItemDetailView: View {
    let itemID: String
    var onBackToTop: () -> Void

    @Environment(TrendsModel.self) private var model

    var body: some View {
        Group {
            if let item = model.item(id: itemID) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        DetailHero(item: item)
                            .padding(.bottom, 4)
                        MetricChips(item: item)
                        SurfaceCard(title: "Signals") {
                            SignalBars(signals: item.signals)
                        }
                        if let video = item.topVideo {
                            TopVideoCard(video: video, imageURL: item.imageUrl)
                        }
                        HeadlinesCard(headlines: item.headlines)
                        if !item.links.isEmpty {
                            LinksCard(links: item.links)
                        }
                        Text("Headlines link to the original publishers. SeoulPulse shows headlines and its own metrics only.")
                            .font(.caption2)
                            .foregroundStyle(Theme.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 4)
                            .padding(.top, 4)
                    }
                    .padding(.horizontal, Theme.screenPadding)
                    .padding(.top, 4)
                    .padding(.bottom, 32)
                }
                .scrollIndicators(.hidden)
            } else {
                MissingTrendView(onBackToTop: onBackToTop)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.ink.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
    }
}

// MARK: - Hero

private struct DetailHero: View {
    let item: TrendItem
    @ScaledMetric(relativeTo: .largeTitle) private var rankSize: CGFloat = 44

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 14) {
                RankNumeral(rank: item.rank, size: min(rankSize, 60))
                    .frame(minWidth: 44, alignment: .leading)
                    .padding(.top, -4)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.name)
                        .font(.system(.title, design: .rounded).weight(.bold))
                        .foregroundStyle(Theme.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                    if let ko = item.nameKo {
                        Text(ko)
                            .font(.subheadline)
                            .foregroundStyle(Theme.textTertiary)
                    }
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 4) {
                    RankChangeBadge(change: item.change, amount: item.changeAmount, scale: 1.35)
                    if let prev = item.prevRank, item.change != .new {
                        Text("was #\(prev)")
                            .font(.caption2)
                            .foregroundStyle(Theme.textTertiary)
                    }
                }
                .padding(.top, 8)
            }
            .accessibilityElement(children: .combine)

            if item.koreaFirst {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    KRFirstBadge(scale: 1.1)
                    Text("Korean sources moved before English-language coverage.")
                        .font(.footnote)
                        .foregroundStyle(Theme.seoulCyan.opacity(0.85))
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .combine)
            }

            HStack(alignment: .top, spacing: 10) {
                Capsule()
                    .fill(Theme.pulseGradient)
                    .frame(width: 3)
                Text(item.why)
                    .font(.headline.weight(.medium))
                    .foregroundStyle(Theme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.vertical, 2)
            }
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Why: \(item.why)")
        }
    }
}

// MARK: - Metric chips

private struct MetricChips: View {
    let item: TrendItem

    private struct Chip: Identifiable {
        let id: String
        let icon: String
        let text: String
        let tint: Color
    }

    private var chips: [Chip] {
        let m = item.metrics
        var out: [Chip] = []
        if let vph = m.ytViewsPerHour {
            out.append(Chip(id: "vph", icon: "play.rectangle.fill",
                            text: "\(Format.compact(vph)) views/hr\(m.ytViewsPerHourEstimated ? " est." : "")",
                            tint: Theme.pulsePink))
        }
        if let accel = m.ytAccel {
            out.append(Chip(id: "accel", icon: "bolt.fill", text: "Accel \(Format.ratio(accel))", tint: Theme.neonViolet))
        }
        if let ko = m.wikiKoRatio {
            out.append(Chip(id: "ko", icon: "book.closed.fill", text: "KO Wiki \(Format.ratio(ko))", tint: Theme.seoulCyan))
        }
        if let en = m.wikiEnRatio {
            out.append(Chip(id: "en", icon: "book.closed", text: "EN Wiki \(Format.ratio(en))", tint: Theme.textSecondary))
        }
        let n = m.headlines24h
        out.append(Chip(id: "news", icon: "newspaper.fill",
                        text: "\(n) \(n == 1 ? "headline" : "headlines") · \(m.headlinesKo24h) KR",
                        tint: Theme.textSecondary))
        return out
    }

    var body: some View {
        FlowLayout(spacing: 8, lineSpacing: 8) {
            ForEach(chips) { chip in
                HStack(spacing: 6) {
                    Image(systemName: chip.icon)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(chip.tint)
                    Text(chip.text)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(Theme.textPrimary)
                }
                .padding(.horizontal, 11)
                .padding(.vertical, 7)
                .background(Capsule().fill(Theme.surface2))
                .accessibilityElement(children: .combine)
            }
        }
    }
}

// MARK: - Signals

private struct SignalBars: View {
    let signals: TrendSignals
    @ScaledMetric(relativeTo: .footnote) private var labelWidth: CGFloat = 94
    @Environment(\.dynamicTypeSize) private var typeSize

    private var rows: [(label: String, value: Double, centered: Bool)] {
        [("YouTube", signals.youtube, false),
         ("Acceleration", signals.accel, true),
         ("New release", signals.newRelease, false),
         ("Wikipedia", signals.wiki, false),
         ("Headlines", signals.news, false)]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            ForEach(rows, id: \.label) { row in
                Group {
                    if typeSize.isAccessibilitySize {
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                label(row.label)
                                Spacer()
                                value(row.value)
                            }
                            SignalBar(value: row.value, centered: row.centered).frame(height: 6)
                        }
                    } else {
                        HStack(spacing: 10) {
                            label(row.label).frame(width: labelWidth, alignment: .leading)
                            SignalBar(value: row.value, centered: row.centered).frame(height: 6)
                            value(row.value).frame(minWidth: 40, alignment: .trailing)
                        }
                    }
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(row.label)
                .accessibilityValue(Format.signal(row.value))
            }
            Text("Acceleration grows from the center line: left is slowing, right is speeding up.")
                .font(.caption2)
                .foregroundStyle(Theme.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 2)
        }
    }

    private func label(_ text: String) -> some View {
        Text(text).font(.footnote).foregroundStyle(Theme.textSecondary).lineLimit(1)
    }

    private func value(_ v: Double) -> some View {
        Text(Format.signal(v))
            .font(.caption.weight(.semibold).monospacedDigit())
            .foregroundStyle(Theme.textPrimary)
    }
}

/// Horizontal bar; `centered` draws from a middle zero line so negative values grow left.
private struct SignalBar: View {
    let value: Double
    let centered: Bool

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            ZStack(alignment: .leading) {
                ZStack(alignment: .leading) {
                    Rectangle().fill(Theme.surface2)
                    if centered {
                        let v = min(max(value, -1), 1)
                        let length = abs(v) * w / 2
                        Rectangle()
                            .fill(v >= 0 ? AnyShapeStyle(Theme.pulseGradientHorizontal) : AnyShapeStyle(Theme.down))
                            .frame(width: length)
                            .offset(x: v >= 0 ? w / 2 : w / 2 - length)
                    } else {
                        Rectangle()
                            .fill(Theme.pulseGradientHorizontal)
                            .frame(width: min(max(value, 0), 1) * w)
                    }
                }
                .clipShape(Capsule())
                if centered {
                    Rectangle()
                        .fill(Theme.textTertiary)
                        .frame(width: 1, height: h + 6)
                        .offset(x: w / 2 - 0.5, y: 0)
                }
            }
            .frame(height: h)
        }
    }
}

// MARK: - Top video

private struct TopVideoCard: View {
    let video: TopVideo
    let imageURL: URL?
    @Environment(\.openURL) private var openURL

    var body: some View {
        SurfaceCard(title: "Top video") {
            VStack(alignment: .leading, spacing: 10) {
                ThumbnailImage(url: imageURL, kind: video.kind)
                Text(video.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    SourceTag(text: video.kind.label.uppercased(), tint: Theme.pulsePink)
                    Text("\(Format.compact(video.views)) views")
                    if !video.channelName.isEmpty {
                        Text("·")
                        Text(video.channelName).lineLimit(1)
                    }
                }
                .font(.caption)
                .foregroundStyle(Theme.textSecondary)
                Button {
                    openURL(video.url)
                } label: {
                    Label("Watch on YouTube", systemImage: "play.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 11)
                        .background(Capsule().fill(Theme.pulseGradient))
                }
                .buttonStyle(PressableCardStyle())
                .padding(.top, 2)
            }
        }
    }
}

// MARK: - Headlines

private struct HeadlinesCard: View {
    let headlines: [Headline]
    @Environment(\.openURL) private var openURL

    var body: some View {
        SurfaceCard(title: "Headlines") {
            if headlines.isEmpty {
                Text("No headlines in the last 48 hours.")
                    .font(.footnote)
                    .foregroundStyle(Theme.textTertiary)
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(headlines.enumerated()), id: \.element.id) { index, headline in
                        if index > 0 {
                            Rectangle().fill(Theme.hairline).frame(height: 0.5).padding(.vertical, 10)
                        }
                        Button {
                            openURL(headline.url)
                        } label: {
                            HeadlineRow(headline: headline)
                        }
                        .buttonStyle(PressableCardStyle())
                    }
                }
            }
        }
    }
}

private struct HeadlineRow: View {
    let headline: Headline

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                SourceTag(text: "\(headline.sourceName) · \(headline.langLabel)",
                          tint: headline.isKorean ? Theme.seoulCyan : Theme.textSecondary)
                if let date = headline.publishedAt {
                    Text(Format.relative(date))
                        .font(.caption2)
                        .foregroundStyle(Theme.textTertiary)
                }
                Spacer()
                Image(systemName: "arrow.up.right")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(Theme.textTertiary)
            }
            Text(headline.title)
                .font(.subheadline)
                .foregroundStyle(Theme.textPrimary)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens \(headline.sourceName) in Safari")
    }
}

// MARK: - Links

private struct LinksCard: View {
    let links: TrendLinks
    @Environment(\.openURL) private var openURL

    var body: some View {
        SurfaceCard(title: "Links") {
            VStack(spacing: 0) {
                let rows = entries
                ForEach(Array(rows.enumerated()), id: \.element.title) { index, entry in
                    if index > 0 {
                        Rectangle().fill(Theme.hairline).frame(height: 0.5)
                    }
                    Button {
                        openURL(entry.url)
                    } label: {
                        HStack(spacing: 12) {
                            Image(systemName: entry.icon)
                                .font(.subheadline)
                                .foregroundStyle(entry.tint)
                                .frame(width: 22)
                            Text(entry.title)
                                .font(.subheadline)
                                .foregroundStyle(Theme.textPrimary)
                            Spacer()
                            Image(systemName: "arrow.up.right")
                                .font(.caption.weight(.bold))
                                .foregroundStyle(Theme.textTertiary)
                        }
                        .padding(.vertical, 11)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(PressableCardStyle())
                    .accessibilityHint("Opens in Safari")
                }
            }
        }
    }

    private var entries: [(title: String, icon: String, tint: Color, url: URL)] {
        var out: [(String, String, Color, URL)] = []
        if let u = links.youtubeChannel { out.append(("YouTube channel", "play.rectangle.fill", Theme.pulsePink, u)) }
        if let u = links.wikipediaEn { out.append(("Wikipedia · EN", "book.closed", Theme.textSecondary, u)) }
        if let u = links.wikipediaKo { out.append(("Wikipedia · KO", "book.closed.fill", Theme.seoulCyan, u)) }
        return out.map { (title: $0.0, icon: $0.1, tint: $0.2, url: $0.3) }
    }
}

// MARK: - Missing

private struct MissingTrendView: View {
    var onBackToTop: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "waveform.path.badge.minus")
                .font(.system(size: 40))
                .foregroundStyle(Theme.pulseGradient)
            Text("This trend isn't in the latest snapshot")
                .font(.headline)
                .foregroundStyle(Theme.textPrimary)
                .multilineTextAlignment(.center)
            Text("Rankings refresh every 30 minutes, so acts move in and out.")
                .font(.footnote)
                .foregroundStyle(Theme.textSecondary)
                .multilineTextAlignment(.center)
            Button(action: onBackToTop) {
                Text("Back to top trends")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 22)
                    .padding(.vertical, 11)
                    .background(Capsule().fill(Theme.pulseGradient))
            }
            .buttonStyle(PressableCardStyle())
            .padding(.top, 6)
        }
        .padding(32)
    }
}
