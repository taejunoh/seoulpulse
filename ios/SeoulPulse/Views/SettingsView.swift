import SwiftUI
import WidgetKit

struct SettingsView: View {
    @Environment(TrendsModel.self) private var model
    @Environment(\.openURL) private var openURL

    @State private var draft: String = AppConfig.overrideString ?? ""
    @State private var effective = AppConfig.resolveTrendsURL()
    @State private var testState: TestState = .idle
    @State private var savedFlash = false
    @FocusState private var fieldFocused: Bool

    enum TestState: Equatable {
        case idle, running
        case ok(String)
        case failed(String)
    }

    private var trimmedDraft: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var draftURL: URL? { AppConfig.validatedURL(trimmedDraft) }
    private var draftInvalid: Bool { !trimmedDraft.isEmpty && draftURL == nil }
    private var canSave: Bool {
        guard let url = draftURL else { return false }
        return url.absoluteString != AppConfig.overrideString
    }

    var body: some View {
        Form {
            feedSection
            effectiveSection
            Section {
                NavigationLink(value: Route.widgetGallery) {
                    Label("Widget preview", systemImage: "rectangle.3.group")
                        .foregroundStyle(Theme.textPrimary)
                }
            } header: {
                Text("Widget")
            } footer: {
                Text("Renders all four widget sizes from the current data.")
            }
            .listRowBackground(Theme.surface)
            sourcesSection
            aboutSection
            legalSection
        }
        .scrollContentBackground(.hidden)
        .background(Theme.ink.ignoresSafeArea())
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .scrollDismissesKeyboard(.interactively)
    }

    // MARK: Feed URL

    private var feedSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 8) {
                TextField(text: $draft, prompt: Text(infoPlistURLString).foregroundStyle(Theme.textTertiary)) {
                    Text("Feed URL")
                }
                .font(.callout.monospaced())
                .foregroundStyle(Theme.textPrimary)
                .keyboardType(.URL)
                .textContentType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.done)
                .focused($fieldFocused)
                .onSubmit { if canSave { save() } }
                .accessibilityLabel("Feed URL")

                if draftInvalid {
                    InlineStatus(icon: "exclamationmark.circle.fill",
                                 text: "Enter an http(s) URL with a host, e.g. http://192.168.0.10:8787/trends.json",
                                 color: Theme.down)
                } else if draftURL != nil {
                    InlineStatus(icon: "checkmark.circle.fill", text: savedFlash ? "Saved" : "Valid URL", color: Theme.up)
                }
            }
            .padding(.vertical, 4)
            .alignmentGuide(.listRowSeparatorLeading) { $0[.leading] }

            HStack(spacing: 10) {
                Button(action: save) {
                    Text("Save")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(canSave ? .white : Theme.textTertiary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 9)
                        .background(Capsule().fill(canSave ? AnyShapeStyle(Theme.pulseGradient)
                                                           : AnyShapeStyle(Theme.surface2)))
                }
                .buttonStyle(.borderless)
                .disabled(!canSave)

                Button(action: resetToDefault) {
                    Text("Reset to default")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.textPrimary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 9)
                        .background(Capsule().fill(Theme.surface2))
                }
                .buttonStyle(.borderless)
            }
            .padding(.vertical, 2)
            .alignmentGuide(.listRowSeparatorLeading) { $0[.leading] }

            Button(action: testConnection) {
                HStack(spacing: 10) {
                    Image(systemName: "antenna.radiowaves.left.and.right")
                        .foregroundStyle(Theme.pulsePink)
                    Text("Test connection")
                        .foregroundStyle(Theme.textPrimary)
                    Spacer()
                    if testState == .running {
                        ProgressView().controlSize(.small)
                    }
                }
                .alignmentGuide(.listRowSeparatorLeading) { $0[.leading] }
            }
            .disabled(testState == .running)

            switch testState {
            case .ok(let message):
                InlineStatus(icon: "checkmark.circle.fill", text: message, color: Theme.up, weight: .semibold)
                    .alignmentGuide(.listRowSeparatorLeading) { $0[.leading] }
            case .failed(let message):
                InlineStatus(icon: "xmark.octagon.fill", text: message, color: Theme.down)
                    .alignmentGuide(.listRowSeparatorLeading) { $0[.leading] }
            default:
                EmptyView()
            }
        } header: {
            Text("Feed URL")
        } footer: {
            Text("Leave empty to use the app's built-in feed. On a device, use your Mac's LAN address instead of localhost.")
        }
        .listRowBackground(Theme.surface)
    }

    private var effectiveSection: some View {
        Section("Effective feed") {
            VStack(alignment: .leading, spacing: 4) {
                Text(effective.url.absoluteString)
                    .font(.footnote.monospaced())
                    .foregroundStyle(Theme.textPrimary)
                    .textSelection(.enabled)
                Text(effective.source.label)
                    .font(.caption)
                    .foregroundStyle(Theme.textTertiary)
            }
            .padding(.vertical, 2)
            LabeledContent("Showing") {
                HStack(spacing: 6) {
                    Circle().fill(model.loaded.origin.color).frame(width: 7, height: 7)
                    Text(originDescription)
                }
                .foregroundStyle(Theme.textSecondary)
            }
            .accessibilityIdentifier("settings-origin-\(model.loaded.origin.rawValue)")
        }
        .listRowBackground(Theme.surface)
    }

    // MARK: Sources (source status screen)

    private var sourcesSection: some View {
        Section {
            if model.snapshot.sources.isEmpty {
                Text("No source status in this snapshot.")
                    .foregroundStyle(Theme.textTertiary)
            }
            ForEach(model.snapshot.sources) { source in
                SourceStatusRow(source: source)
            }
        } header: {
            Text("Data sources")
        } footer: {
            Text("Status from the snapshot generated \(Format.kstDateTime(model.snapshot.generatedAt)). Public sources only: YouTube, Wikipedia, and news headlines (plus Naver search trends when enabled).")
        }
        .listRowBackground(Theme.surface)
    }

    private var aboutSection: some View {
        Section("About") {
            LabeledContent("Version", value: AppConfig.appVersion)
                .foregroundStyle(Theme.textPrimary)
            Text("SeoulPulse ranks K-pop acts by what is moving in Korea first: YouTube view velocity, Korean and English Wikipedia pageviews, and headline counts from Yonhap, Soompi and Koreaboo. It shows only headline text, links, source names, publish times and metrics it computes itself. Korean headlines are shown as published, untranslated. Fan-made; not affiliated with any artist, label or publisher.")
                .font(.footnote)
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, 4)
        }
        .listRowBackground(Theme.surface)
    }

    private var legalSection: some View {
        Section("Legal") {
            legalRow("Privacy Policy", icon: "hand.raised.fill", url: AppConfig.privacyPolicyURL)
            legalRow("Support", icon: "questionmark.circle.fill", url: AppConfig.supportURL)
        }
        .listRowBackground(Theme.surface)
    }

    private func legalRow(_ title: String, icon: String, url: URL) -> some View {
        Button {
            openURL(url)
        } label: {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.subheadline)
                    .foregroundStyle(Theme.pulsePink)
                    .frame(width: 22)
                Text(title)
                    .foregroundStyle(Theme.textPrimary)
                Spacer()
                Image(systemName: "arrow.up.right")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(Theme.textTertiary)
            }
            .contentShape(Rectangle())
        }
        .accessibilityHint("Opens in Safari")
    }

    // MARK: Actions

    private var infoPlistURLString: String {
        (Bundle.main.object(forInfoDictionaryKey: AppConfig.infoPlistURLKey) as? String) ?? AppConfig.defaultTrendsURLString
    }

    private var originDescription: String {
        let loaded = model.loaded
        switch loaded.origin {
        case .network: return "Live · \(Format.relative(loaded.fetchedAt ?? Date()))"
        case .cache: return "Offline cache · \(Format.relative(loaded.fetchedAt ?? loaded.snapshot.generatedAt))"
        case .sample: return "Bundled sample"
        }
    }

    private func save() {
        guard AppConfig.setOverride(trimmedDraft) else { return }
        draft = AppConfig.overrideString ?? ""
        fieldFocused = false
        afterConfigChange()
        savedFlash = true
        Task {
            try? await Task.sleep(for: .seconds(1.5))
            savedFlash = false
        }
    }

    private func resetToDefault() {
        AppConfig.setOverride(nil)
        draft = ""
        fieldFocused = false
        afterConfigChange()
    }

    private func afterConfigChange() {
        effective = AppConfig.resolveTrendsURL()
        testState = .idle
        WidgetCenter.shared.reloadTimelines(ofKind: AppConfig.widgetKind)
        Task { await model.refresh() }
    }

    private func testConnection() {
        let url = draftURL ?? effective.url
        testState = .running
        Task {
            do {
                let (snapshot, _) = try await TrendStore().fetch(url: url, timeout: 8)
                let count = snapshot.items.count
                testState = .ok("OK · \(count) \(count == 1 ? "item" : "items") · generated \(Format.kstTime(snapshot.generatedAt))")
            } catch {
                testState = .failed(error.localizedDescription)
            }
        }
    }
}

/// Icon + text with a tight gap (a Form `Label` would put the icon in a wide list-icon column).
private struct InlineStatus: View {
    let icon: String
    let text: String
    let color: Color
    var weight: Font.Weight = .regular

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: icon)
            Text(text).fixedSize(horizontal: false, vertical: true)
        }
        .font(.footnote.weight(weight))
        .foregroundStyle(color)
        .accessibilityElement(children: .combine)
    }
}

struct SourceStatusRow: View {
    let source: SourceStatus

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Circle()
                .fill(source.status.color)
                .frame(width: 8, height: 8)
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(source.name)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.textPrimary)
                    Spacer()
                    Text(source.status.label)
                        .font(.caption.weight(.bold))
                        .foregroundStyle(source.status.color)
                }
                if !source.detail.isEmpty {
                    Text(source.detail)
                        .font(.caption)
                        .foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(source.lastSuccessAt.map { "Last success \(Format.kstDateTime($0))" } ?? "No successful run yet")
                    .font(.caption2)
                    .foregroundStyle(Theme.textTertiary)
            }
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
    }
}
