#!/bin/bash
# Archive SeoulPulse (Release) and upload it to App Store Connect / TestFlight.
#
# Prerequisites (manual, one-time):
#   1. Xcode is signed in to the Apple Developer team account (Xcode > Settings > Accounts, team 3BMF4LM6TM).
#   2. An app record exists in App Store Connect with bundle ID com.taejunoh.seoulpulse.
#
# Usage: ios/scripts/testflight.sh   (bump CURRENT_PROJECT_VERSION in ios/project.yml before each upload)
set -euo pipefail

cd "$(dirname "$0")/.."   # ios/

xcodegen generate

rm -rf build/SeoulPulse.xcarchive build/export
xcodebuild archive \
  -project SeoulPulse.xcodeproj \
  -scheme SeoulPulse \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath build/SeoulPulse.xcarchive \
  -allowProvisioningUpdates

# ExportOptions.plist has destination=upload, so this exports and uploads to App Store Connect in one step.
xcodebuild -exportArchive \
  -archivePath build/SeoulPulse.xcarchive \
  -exportOptionsPlist ExportOptions.plist \
  -exportPath build/export \
  -allowProvisioningUpdates
