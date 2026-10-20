# Seoul Rising: App Store metadata draft (US English, version 1.0)

Draft only. Nothing here has been uploaded. Character counts are measured (Python `len`); limits are App Store Connect's.

Renamed from SeoulPulse to Seoul Rising on 2026-10-19 (see `docs/LAUNCH.md`). The product name in the App Name,
description, review notes and screenshot captions changed; the subtitle, promotional text and URLs did not.

| Field | Limit | Used |
|---|---|---|
| App Name | 30 | 12 |
| Subtitle | 30 | 24 |
| Promotional Text | 170 | 163 |
| Keywords | 100 | 89 |
| Description | 4000 | 2765 |
| Copyright | n/a | 14 |

## App Name (12/30)

```
Seoul Rising
```

## Subtitle (24/30)

```
Korea-first K-pop trends
```

## Promotional Text (163/170)

```
See what's spiking in Korea right now. A live K-pop ranking built from YouTube momentum, Wikipedia attention and Korean and English headlines, on your Home Screen.
```

## Description (2765/4000)

```
Seoul Rising shows what is spiking in K-pop right now, and flags when Korea moves before the English-language internet does.

Most K-pop news reaches US fans hours after Korean media and fans have already moved on. Seoul Rising watches Korean sources directly and ranks the artists gaining the most momentum, so you can see what is trending before it reaches your feed.

WHAT YOU GET
- A live ranking of the top 20 K-pop acts by current momentum, with how far each one moved since the last snapshot
- A one-line "why" for every entry, such as which signal is driving it
- A detail screen for each act with its Korean name, key numbers, signal breakdown, most-watched recent video and the latest headlines from the original publishers
- Home Screen widgets in small, medium and large sizes, plus a Lock Screen widget for the current number one
- Pull to refresh in the app; the data itself is refreshed about every 30 minutes

HOW THE RANKING WORKS
Seoul Rising combines public signals, with no black box and no paid placements:
- YouTube view velocity: how fast recent videos from official channels are gaining views compared with their normal pace
- Wikipedia attention: English and Korean Wikipedia pageviews compared with each article's own recent baseline
- Headlines: how many Korean and English headlines mention an act in the last 24 hours
- Freshness: new releases get a boost while they are still new

Each signal is shown on the detail screen, so you can see exactly why an act is where it is.

WHAT "KR FIRST" MEANS
When Korean sources picked up an act's story hours before English-language coverage did, the act gets a KR FIRST badge (or a small cyan dot in the compact widgets). It is the quickest way to spot news that has not reached English-language media yet.

WIDGETS
- Small: top 3
- Medium: top 6
- Large: top 10, with the reason for each of the top three
- Lock Screen: the current number one and why it is moving

Widgets show when the data was last updated and tell you if it is stale or offline.

PRIVACY
No account, no sign-in, no ads and no tracking. Seoul Rising does not collect any personal data. The app only downloads a public data file and a few video thumbnails. Headline and video links open in your browser, and Seoul Rising shows only headlines and its own computed numbers, never full articles.

Seoul Rising is an independent fan-made project. It is not affiliated with, endorsed by or sponsored by any artist, agency, record label or media outlet. All artist names, titles and trademarks belong to their respective owners and are used only to identify the subjects of public trend data. Rankings are computed automatically from public data and are not an official chart.

Questions or feedback: taejunoh.com/seoulpulse/support
```

## Keywords (89/100)

```
kpop,korean,idol,ranking,charts,music,news,widget,hallyu,fandom,momentum,hangul,headlines
```

Rules followed: comma-separated, no spaces, no word from the name or subtitle repeated (Seoul, Rising, Korea-first, K-pop, trends), no artist, label or app names, no competitor names, no "best" or "#1" claims. "kpop" (no hyphen) is a different token from "K-pop" and is what many people type. "rising" was dropped on 2026-10-19 because it is now part of the App Name, which the App Store already indexes; that leaves 11 characters free (for example `,comeback`, which would make 98/100). The description's "no tracking" and "no account" lines match `PrivacyInfo.xcprivacy` (tracking false, no collected data types).

## URLs

| Field | Value |
|---|---|
| Support URL | https://taejunoh.com/seoulpulse/support/ |
| Marketing URL | https://taejunoh.com/seoulpulse/ |
| Privacy Policy URL | https://taejunoh.com/seoulpulse/privacy/ |

## Categories

- Primary: **Music**. The app is about K-pop acts and what is rising in music, and its audience browses the Music category.
- Secondary: **Entertainment**. It is also a news and trends feed about pop culture, and the headlines are entertainment news.
- Considered and rejected: News (Apple expects original or licensed reporting; we only link to headlines) and Reference.

## Copyright

```
2026 Taejun Oh
```

## Age rating questionnaire (2025 format)

Expected result: **4+**. Answers were checked against the code, not guessed.

| Section | Question | Answer |
|---|---|---|
| In-app controls | Parental controls | No |
| In-app controls | Age assurance | No |
| Capabilities | Unrestricted web access | **No** |
| Capabilities | User-generated content | **No** |
| Capabilities | Messaging and chat | No |
| Capabilities | Advertising | No |
| Mature themes | Profanity or crude humor | None |
| Mature themes | Horror or fear themes | None |
| Mature themes | Alcohol, tobacco or drug use or references | None |
| Medical or wellness | Medical or treatment information | None |
| Medical or wellness | Health or wellness topics | No |
| Sexuality or nudity | Mature or suggestive themes | None |
| Sexuality or nudity | Sexual content or nudity | None |
| Sexuality or nudity | Graphic sexual content and nudity | None |
| Violence | Cartoon or fantasy violence | None |
| Violence | Realistic violence | None |
| Violence | Prolonged graphic or sadistic realistic violence | None |
| Violence | Guns or other weapons | None |
| Chance-based activities | Simulated gambling | None |
| Chance-based activities | Contests | None |
| Chance-based activities | Gambling | No |

Verification from the code:

- No in-app web view: a grep for `WKWebView`, `SFSafariViewController`, `WebKit` and `SafariServices` in `ios/SeoulPulse`, `ios/Shared` and `ios/SeoulPulseWidget` finds nothing. Every external link (headlines, YouTube, Wikipedia) goes through `@Environment(\.openURL)` and opens in Safari. So "Unrestricted web access" is No.
- No user-generated content: there are no accounts, comments, uploads or sharing. The only text field is Settings > Feed URL, which is the user's own configuration and is shown only to them.
- Honest caveat: headlines are third-party publisher text (Yonhap, Soompi, Koreaboo) shown unfiltered. They are entertainment news and we have not seen mature content in them, but we do not moderate them. If a reviewer asks: the headlines come from established news outlets and the app links out rather than hosting content.

## App Privacy (nutrition label), for reference

"Data Not Collected". The app has no accounts, analytics or ads. It makes two kinds of network request: the public JSON feed from taejunoh.com, and video thumbnails from i.ytimg.com (YouTube's image CDN). Those servers can see an IP address, as any web server does, but the app does not collect or store anything linked to the user. Matches `PrivacyInfo.xcprivacy`. Export compliance: `ITSAppUsesNonExemptEncryption` is false in both Info.plists.

## App Review notes

```
No sign-in is needed. Seoul Rising has no accounts, no in-app purchases and no ads. Open the app and the ranking appears immediately.

What it is: a ranking of K-pop acts by current momentum, with optional Home Screen and Lock Screen widgets. It is an independent fan-made project and is not affiliated with any artist, agency or label.

Data source: the app downloads one public JSON file, https://taejunoh.com/seoulpulse/trends.json, which a scheduled job (GitHub Actions, every 30 minutes) builds from public YouTube channel feeds, Wikipedia pageviews, and Yonhap, Soompi and Koreaboo RSS headlines. We store and show only headline text, the link, the source name, the time and our own computed numbers. Article bodies are never copied. Headlines and videos open in Safari; there is no in-app browser. Video thumbnails load from i.ytimg.com.

If the feed is unreachable the app shows its last cached data, then bundled sample data, and labels which one it is showing (Live, Offline or Sample data), so it never shows an empty screen.

Widgets: touch and hold the Home Screen, tap Add Widget, choose Seoul Rising. There is also a preview of all widget sizes in the app under Settings > Widget preview.

Settings has an optional "Feed URL" field for developers who run their own copy of the backend. Leave it empty to use the built-in feed.

Contact: https://github.com/taejunoh/seoulpulse/issues (also https://taejunoh.com/seoulpulse/support/). Source code is public at https://github.com/taejunoh/seoulpulse.
```

Reviewer contact fields (name, phone, email) must be filled in App Store Connect by the owner; the notes above deliberately contain no personal contact details.

## What's New

Not needed for 1.0.

## Guideline risk notes

- **2.3.7 / 5.2 (names and third-party IP):** the name, subtitle, promotional text, keywords and description name no artist, label or agency. Screenshots do show artist names, because the ranking is the product; they are used descriptively as factual trend data, and the description carries the fan-made, not-affiliated disclaimer. `02-detail.png` was retaken on an item with no top video (CORTIS, rank 19), so it contains no YouTube thumbnail or other third-party artwork. If the detail shot is ever retaken on an item that has a top video, it will show a music-video still, which is a possible 5.2 issue.
- **No superlatives:** no "best", "#1" or "top app" claims.
- **4.2 (minimum functionality):** the app is a data product with widgets, detail screens, deep links and offline fallback, not a thin web wrapper.
- **5.1.2 / data use:** nothing is collected; the privacy policy URL is the one above.

## Screenshot set (6.9" iPhone, 1320x2868, no alpha)

`01-list` and `03-widgets` were retaken on 2026-10-19 with the Seoul Rising name (live data, iPhone 17 Pro Max simulator on iOS 26.5;
6.3" copies in `6.3in/` via `sips -z 2622 1206`). `02-detail` (2026-10-14) was kept: it shows no product name, and every
item in the live feed on 2026-10-19 had a top video, so a retake would have added a YouTube thumbnail (see 5.2 above).

Files are in `docs/appstore/screenshots/`. Recommended upload order: 01-list, 03-widgets, 02-detail; 04-settings is optional (add it as a fourth if you want to show feed and source status).

| File | Shows |
|---|---|
| `01-list.png` | Home: Live ranking with a KR FIRST entry |
| `02-detail.png` | Detail view of a KR FIRST item (CORTIS) with Korean headlines and no video thumbnail |
| `03-widgets.png` | Clean Home Screen with a Medium and a Large Seoul Rising widget on live data; only stock Apple dock icons |
| `04-settings.png` | Settings with feed and source status (optional) |
