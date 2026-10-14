import SwiftUI

struct HomeView: View {
    @Environment(TrendsModel.self) private var model
    @Binding var path: [Route]

    var body: some View {
        let loaded = model.loaded
        let snapshot = loaded.snapshot
        ScrollView {
            LazyVStack(alignment: .leading, spacing: Theme.cardSpacing) {
                header(loaded)
                    .padding(.bottom, 8)
                banners(loaded)
                if snapshot.items.isEmpty {
                    EmptyTrendsCard()
                } else {
                    ForEach(snapshot.items) { item in
                        NavigationLink(value: Route.item(item.id)) {
                            TrendCard(item: item)
                        }
                        .buttonStyle(PressableCardStyle())
                        .accessibilityHint("Shows details")
                    }
                }
                SourcesFooter(sources: snapshot.sources)
                    .padding(.top, 18)
                Text("Headlines link to their publishers. Fan-made, not affiliated with any artist or label.")
                    .font(.caption2)
                    .foregroundStyle(Theme.textTertiary)
                    .padding(.top, 10)
            }
            .padding(.horizontal, Theme.screenPadding)
            .padding(.bottom, 32)
        }
        .scrollIndicators(.hidden)
        .background(Theme.ink.ignoresSafeArea())
        .safeAreaInset(edge: .top, spacing: 0) {
            // The nav bar is hidden here, so give the status bar its own ink scrim with a short fade;
            // otherwise cards scroll underneath the clock. Zero height: it doesn't push content down.
            Color.clear
                .frame(height: 0)
                .background(alignment: .top) { Theme.ink.ignoresSafeArea(edges: .top) }
                .overlay(alignment: .top) {
                    LinearGradient(colors: [Theme.ink, Theme.ink.opacity(0)], startPoint: .top, endPoint: .bottom)
                        .frame(height: 14)
                }
                .allowsHitTesting(false)
                .accessibilityHidden(true)
        }
        .refreshable { await model.refresh() }
        .toolbar(.hidden, for: .navigationBar)
    }

    // MARK: Header

    private func header(_ loaded: LoadedTrends) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .center) {
                Wordmark(dotSize: 7)
                Spacer()
                Button {
                    path.append(.settings)
                } label: {
                    Image(systemName: "gearshape.fill")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(Theme.textSecondary)
                        .frame(width: 38, height: 38)
                        .background(Circle().fill(Theme.surface2))
                }
                .accessibilityLabel("Settings")
            }
            .padding(.top, 4)
            Text("Korea-first K-pop trends")
                .font(.system(.title, design: .rounded).weight(.bold))
                .foregroundStyle(Theme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10)
                .accessibilityAddTraits(.isHeader)
            OriginStatusLine(loaded: loaded, isRefreshing: model.isRefreshing)
        }
    }

    // MARK: Banners

    @ViewBuilder
    private func banners(_ loaded: LoadedTrends) -> some View {
        let snapshot = loaded.snapshot
        if snapshot.coldStart {
            NoticeBanner(icon: "waveform.path.ecg",
                         text: "Calibrating — rankings sharpen over the next few hours",
                         tint: Theme.seoulCyan)
        }
        if loaded.origin == .sample {
            Button {
                path.append(.settings)
            } label: {
                NoticeBanner(icon: "shippingbox",
                             text: "Showing sample data — set your feed URL in Settings",
                             tint: Theme.neonViolet,
                             showsChevron: true)
            }
            .buttonStyle(PressableCardStyle())
        } else if snapshot.isOverADayOld(now: Date()) {
            NoticeBanner(icon: "clock.badge.exclamationmark",
                         text: "Data is over a day old",
                         tint: Theme.warn)
        }
    }
}

/// "● Live · Updated 8m ago" — origin is always visible (network / cache / sample).
struct OriginStatusLine: View {
    let loaded: LoadedTrends
    let isRefreshing: Bool

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            HStack(spacing: 6) {
                StatusDot(color: loaded.origin.color, animated: loaded.origin == .network)
                Text(loaded.origin.label)
                    .fontWeight(.semibold)
                    .foregroundStyle(Theme.textSecondary)
                Text("·").foregroundStyle(Theme.textTertiary)
                Text(detail(now: context.date))
                    .foregroundStyle(Theme.textTertiary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                if isRefreshing {
                    ProgressView()
                        .controlSize(.mini)
                        .tint(Theme.textTertiary)
                        .padding(.leading, 2)
                }
            }
            .font(.caption)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("origin-\(loaded.origin.rawValue)")
        }
    }

    private func detail(now: Date) -> String {
        switch loaded.origin {
        case .sample:
            return "Snapshot of \(Format.kstDateTime(loaded.snapshot.generatedAt))"
        case .network, .cache:
            return "Updated \(Format.relative(loaded.snapshot.generatedAt, now: now))"
        }
    }
}

struct EmptyTrendsCard: View {
    var body: some View {
        SurfaceCard {
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: "waveform.path")
                    .font(.title2)
                    .foregroundStyle(Theme.pulseGradient)
                Text("No trends yet — the pipeline hasn't published")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Theme.textPrimary)
                Text("Pull to refresh, or check the feed URL in Settings.")
                    .font(.caption)
                    .foregroundStyle(Theme.textSecondary)
            }
            .padding(.vertical, 8)
        }
    }
}

// MARK: - Sources footer

struct SourcesFooter: View {
    let sources: [SourceStatus]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Sources")
            if sources.isEmpty {
                Text("No source status in this snapshot.")
                    .font(.caption)
                    .foregroundStyle(Theme.textTertiary)
            } else {
                FlowLayout(spacing: 8, lineSpacing: 8) {
                    ForEach(sources) { source in
                        SourcePill(source: source)
                    }
                }
            }
        }
    }
}

struct SourcePill: View {
    let source: SourceStatus
    @State private var showDetail = false

    var body: some View {
        Button {
            showDetail = true
        } label: {
            HStack(spacing: 6) {
                Circle().fill(source.status.color).frame(width: 7, height: 7)
                Text(source.name)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.textSecondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Capsule().fill(Theme.surface))
            .overlay(Capsule().strokeBorder(Theme.hairline, lineWidth: 0.5))
        }
        .buttonStyle(PressableCardStyle())
        .accessibilityLabel("\(source.name), \(source.status.label)")
        .accessibilityHint("Shows source details")
        .popover(isPresented: $showDetail) {
            SourceDetailPopover(source: source)
                .presentationCompactAdaptation(.popover)
                .presentationBackground(Theme.surface2)
        }
    }
}

struct SourceDetailPopover: View {
    let source: SourceStatus

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Circle().fill(source.status.color).frame(width: 7, height: 7)
                Text(source.name).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.textPrimary)
                Text(source.status.label).font(.caption.weight(.bold)).foregroundStyle(source.status.color)
            }
            if !source.detail.isEmpty {
                Text(source.detail)
                    .font(.footnote)
                    .foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(lastSuccessText)
                .font(.caption)
                .foregroundStyle(Theme.textTertiary)
        }
        .padding(14)
        .frame(minWidth: 220, maxWidth: 300, alignment: .leading)
    }

    private var lastSuccessText: String {
        guard let date = source.lastSuccessAt else { return "No successful run yet" }
        return "Last success \(Format.relative(date))"
    }
}
