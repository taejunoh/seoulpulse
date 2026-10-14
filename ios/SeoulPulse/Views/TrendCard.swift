import SwiftUI

/// Home list card: rank numeral, name + name_ko, change badge, KR FIRST, why, score bar.
struct TrendCard: View {
    let item: TrendItem
    @ScaledMetric(relativeTo: .title) private var rankColumn: CGFloat = 40

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RankNumeral(rank: item.rank, size: item.rank <= 3 ? 34 : 26, dimmed: item.rank > 3)
                .frame(width: rankColumn, alignment: .center)
                .padding(.top, item.rank <= 3 ? -3 : 0)

            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(item.name)
                        .font(Theme.nameFont)
                        .foregroundStyle(Theme.textPrimary)
                        .layoutPriority(1)
                    if let ko = item.nameKo {
                        Text(ko)
                            .font(Theme.nameKoFont)
                            .foregroundStyle(Theme.textTertiary)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 6)
                    RankChangeBadge(change: item.change, amount: item.changeAmount)
                }
                if item.koreaFirst {
                    KRFirstBadge()
                }
                Text(item.why)
                    .font(Theme.whyFont)
                    .foregroundStyle(Theme.textSecondary)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                ScoreBar(score: item.score)
                    .padding(.top, 2)
            }
        }
        .padding(Theme.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                .fill(Theme.surface)
        )
        .overlay {
            if item.rank == 1 {
                RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                    .strokeBorder(Theme.pulseGradient.opacity(0.55), lineWidth: 1)
            }
        }
        .contentShape(RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}
