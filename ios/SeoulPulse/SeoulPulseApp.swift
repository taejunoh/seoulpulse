import SwiftUI

@main
struct SeoulPulseApp: App {
    @State private var model = TrendsModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .preferredColorScheme(.dark)
                .tint(Theme.pulsePink)
                .dynamicTypeSize(...DynamicTypeSize.accessibility3)
        }
    }
}

struct RootView: View {
    @Environment(TrendsModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var path: [Route] = []

    var body: some View {
        NavigationStack(path: $path) {
            HomeView(path: $path)
                .navigationDestination(for: Route.self) { route in
                    switch route {
                    case .item(let id):
                        ItemDetailView(itemID: id) { path = [] }
                    case .settings:
                        SettingsView()
                    case .widgetGallery:
                        WidgetGalleryView()
                    }
                }
        }
        .background(Theme.ink.ignoresSafeArea())
        .onOpenURL { url in
            path = Route.parse(url)
        }
        .task {
            await model.refresh()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task { await model.refreshIfStale() }
            }
        }
    }
}
