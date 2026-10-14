import SwiftUI
import UIKit

/// YouTube thumbnail with a designed placeholder.
///
/// Not `AsyncImage`: i.ytimg.com answers unknown ids (e.g. the sample's `SAMPLE…` ids) with HTTP 404
/// *plus* a grey 120x90 "no thumbnail" JPEG, which AsyncImage happily shows. This loader requires HTTP
/// 200 and a real-sized image, otherwise the placeholder stays.
struct ThumbnailImage: View {
    let url: URL?
    let kind: VideoKind
    @State private var image: UIImage?

    var body: some View {
        Color.clear
            .aspectRatio(16.0 / 9.0, contentMode: .fit)
            .overlay {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                        .transition(.opacity)
                } else {
                    placeholder
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(Theme.hairline, lineWidth: 0.5)
            )
            .accessibilityHidden(true)
            .task(id: url) { await load() }
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(colors: [Color(hex: 0x24183A), Theme.surface2, Color(hex: 0x14202A)],
                           startPoint: .topLeading, endPoint: .bottomTrailing)
            RadialGradient(colors: [Theme.pulsePink.opacity(0.22), .clear],
                           center: .center, startRadius: 4, endRadius: 140)
            Image(systemName: "play.fill")
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 54, height: 54)
                .background(Circle().fill(Theme.pulseGradient))
                .shadow(color: Theme.pulsePink.opacity(0.5), radius: 14)
        }
    }

    private func load() async {
        guard let url else { return }
        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let decoded = UIImage(data: data), decoded.size.width >= 200 else { return }
            withAnimation(.easeOut(duration: 0.25)) { image = decoded }
        } catch {
            // Keep the placeholder; offline is a normal state.
        }
    }
}
