# Launch status

Status as of 2026-10-14. Everything below was verified that day. The backend is live and the App Store draft is
complete but **not submitted for review**. What remains is owner-only: legal attestations, contact details, a
real-device test and the submit button.

## Done

**Backend (GitHub Actions + Pages)**
- Feed is live: https://taejunoh.com/seoulpulse/trends.json
- Cron is `7,37 * * * *`. It was moved off :00/:30 because GitHub delayed or dropped the earlier schedule.
- `SP_CONTACT` variable is set to the repo URL.
- `YOUTUBE_API_KEY` secret is set, so the API pass is active (budget 9,000 units/day or less). Six channels stay
  RSS-only because their uploads playlist is not available to the API.
- Daily social draft issue workflow runs at 13:17 UTC; the first issue (#1) exists.
- Privacy and support pages: https://taejunoh.com/seoulpulse/privacy/ and https://taejunoh.com/seoulpulse/support/

**App Store Connect**
- App "SeoulPulse": Apple ID 6822836898, bundle `com.taejunoh.seoulpulse`, SKU `seoulpulse-ios`.
- TestFlight: build 1.0.0 (1) processed. Internal group "Internal" (auto-distribution), account holder as tester.
- Version 1.0.0 draft is filled in:
  - subtitle, promotional text, description, keywords, support and marketing URLs, copyright
  - categories Music / Entertainment, age rating 4+
  - App Privacy "Data Not Collected" published, with the privacy policy URL
  - price Free (175 countries)
  - 3 screenshots (6.3" 1206x2622, from `docs/appstore/screenshots/6.3in`)
  - build 1 attached

## Owner checklist (in order)

- [ ] **1. License agreement.** Account Holder accepts the updated Apple Developer Program License Agreement at
  developer.apple.com/account. Required before submitting new apps or updates.
- [ ] **2. Availability.** App Store Connect, Pricing and Availability, App Availability, Set Up Availability,
  All countries, Confirm. Left unconfirmed on purpose.
- [ ] **3. Content Rights.** App Information, Content Rights: declare third-party content. The app shows
  third-party headlines/titles and YouTube thumbnails via links. This is the owner's legal attestation.
- [ ] **4. App Review Information.** On the version page, add the owner's contact phone and email, then paste the
  review notes from `docs/appstore/metadata.md`. The notes did not persist without the contact fields.
- [ ] **5. Real-device test.** Install through the TestFlight app on an iPhone and test the widgets, including the
  Lock Screen widgets.
- [ ] **6. Submit.** "Add for Review", then Submit.
- [ ] **7. Optional: Naver DataLab.** Get a key via NAVER API HUB on Naver Cloud (needs a Naver Cloud account), then:
  `gh secret set NAVER_CLIENT_ID --repo taejunoh/seoulpulse` and
  `gh secret set NAVER_CLIENT_SECRET --repo taejunoh/seoulpulse`.
- [ ] **8. Stage-0 validation.** Post the daily draft issues manually to X/Threads for 2-3 weeks. Track followers
  and engagement before investing further.

## Ops notes

- Next TestFlight uploads: `ios/scripts/testflight.sh` (automatic build number, export retry). See
  [DEPLOY.md](DEPLOY.md#4-testflight).
- GitHub disables scheduled workflows after 60 days without repo activity. Push a commit or re-enable the
  workflow in the Actions tab if the feed stops updating.
- If the Actions cache is evicted (7 days unused), history resets and the feed returns to cold start until it
  recalibrates; the social drafts carry a warning while that lasts.
