#!/bin/bash
# Archive SeoulPulse (Release) and upload it to App Store Connect / TestFlight.
#
# Prerequisites (manual, one-time):
#   1. Xcode is signed in to the Apple Developer team account (Xcode > Settings > Accounts, team 3BMF4LM6TM).
#   2. An app record exists in App Store Connect with bundle ID com.taejunoh.seoulpulse
#      (app "Seoul Rising", created as "SeoulPulse", Apple ID 6822836898).
#
# Usage: ios/scripts/testflight.sh
#   The build number (CFBundleVersion) is generated automatically as a UTC timestamp (YYYYMMDDHHMM), so each
#   upload is higher than the last. It is passed to xcodebuild as a CURRENT_PROJECT_VERSION override and applies to
#   the app and the widget alike; ios/project.yml is not edited. To force a specific number:
#     BUILD_NUMBER=202610141800 ios/scripts/testflight.sh
#   The marketing version (CFBundleShortVersionString) still comes from MARKETING_VERSION in ios/project.yml.
set -euo pipefail

cd "$(dirname "$0")/.."   # ios/

BUILD_NUMBER="${BUILD_NUMBER:-$(date -u +%Y%m%d%H%M)}"
if [[ ! "$BUILD_NUMBER" =~ ^[0-9]+(\.[0-9]+){0,2}$ ]]; then
  echo "error: BUILD_NUMBER must be integers separated by periods (e.g. 202610141800), got '$BUILD_NUMBER'" >&2
  exit 1
fi
MARKETING_VERSION="$(sed -n '/^[[:space:]]*MARKETING_VERSION:/{s/^[^:]*:[[:space:]]*"\{0,1\}\([^"[:space:]]*\).*$/\1/p;q;}' project.yml)"

echo "==> Uploading SeoulPulse ${MARKETING_VERSION:-?} (build ${BUILD_NUMBER}) to TestFlight"

xcodegen generate

rm -rf build/SeoulPulse.xcarchive build/export

# A command-line build setting applies to every target, so the app and the widget get the same CFBundleVersion
# (both Info.plists use $(CURRENT_PROJECT_VERSION)).
xcodebuild archive \
  -project SeoulPulse.xcodeproj \
  -scheme SeoulPulse \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath build/SeoulPulse.xcarchive \
  -allowProvisioningUpdates \
  CURRENT_PROJECT_VERSION="$BUILD_NUMBER"

# ExportOptions.plist has destination=upload, so this exports and uploads to App Store Connect in one step.
# Cloud-managed distribution signing can fail the first time with "no identity found" and succeed moments later,
# so retry once after a short wait. Run inside `if` so `set -e` does not abort before the retry; xcodebuild is
# never piped, so nothing can SIGPIPE it mid-upload.
export_archive() {
  xcodebuild -exportArchive \
    -archivePath build/SeoulPulse.xcarchive \
    -exportOptionsPlist ExportOptions.plist \
    -exportPath build/export \
    -allowProvisioningUpdates
}

if ! export_archive; then
  echo "warning: export/upload failed; retrying once in 10s (distribution signing may still be warming up)" >&2
  sleep 10
  rm -rf build/export
  export_archive
fi

echo "==> Uploaded SeoulPulse ${MARKETING_VERSION:-?} (build ${BUILD_NUMBER}). It appears in TestFlight after processing."
