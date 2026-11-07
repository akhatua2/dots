#!/usr/bin/env bash
# Builds dots for people to download: the app with its worker and uv inside, signed with a Developer ID,
# notarized by Apple and stapled, in a disk image.
#
#   DOTS_SIGN_IDENTITY="Developer ID Application: Name (TEAMID)" DOTS_NOTARY_PROFILE=dots apps/macos/tools/release.sh
#
# DOTS_NOTARY_PROFILE is a notarytool profile, saved once with `xcrun notarytool store-credentials dots`. Without
# DOTS_SIGN_IDENTITY it builds an ad hoc signed disk image, to try the steps (another Mac won't open that app).
# The result is apps/macos/build/release/dots.dmg.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MAC="$ROOT/apps/macos"
OUT="$MAC/build/release"
IDENTITY="${DOTS_SIGN_IDENTITY:--}"
PROFILE="${DOTS_NOTARY_PROFILE:-}"
APP="$MAC/build/Build/Products/Release/dots.app"
ENTITLEMENTS="$MAC/tools/worker.entitlements"

step() { echo "==> $*"; }
# A Developer ID signature carries a secure timestamp; an ad hoc one can't
timestamp() { [ "$IDENTITY" = "-" ] && echo "--timestamp=none" || echo "--timestamp"; }

step "packaging the worker"
(cd "$ROOT/core" && uv run --extra dev --extra mac pyinstaller packaging/worker.spec \
  --distpath dist --workpath build/pyinstaller --noconfirm >/dev/null)

step "building the app"
(cd "$MAC" && xcodegen generate -q && xcodebuild -project dots.xcodeproj -scheme dots -configuration Release \
  -derivedDataPath build -destination 'platform=macOS,arch=arm64' \
  CODE_SIGN_IDENTITY="$IDENTITY" DEVELOPMENT_TEAM="" CODE_SIGN_STYLE=Manual build -quiet)

step "signing the worker, uv and the app, with the hardened runtime"
# The worker is Python packed with its libraries, which it unpacks and loads as it starts
codesign --force --options runtime "$(timestamp)" --entitlements "$ENTITLEMENTS" -s "$IDENTITY" \
  "$APP/Contents/Resources/dots-worker"
codesign --force --options runtime "$(timestamp)" -s "$IDENTITY" "$APP/Contents/Resources/uv"
codesign --force --options runtime "$(timestamp)" -s "$IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"

mkdir -p "$OUT"
if [ -n "$PROFILE" ] && [ "$IDENTITY" != "-" ]; then
  step "notarizing (Apple checks it, a few minutes)"
  ditto -c -k --keepParent "$APP" "$OUT/dots.zip"
  xcrun notarytool submit "$OUT/dots.zip" --keychain-profile "$PROFILE" --wait
  xcrun stapler staple "$APP"
  rm "$OUT/dots.zip"
else
  step "not notarizing: set DOTS_SIGN_IDENTITY and DOTS_NOTARY_PROFILE for a build other Macs open"
fi

step "making the disk image"
rm -f "$OUT/dots.dmg"
hdiutil create -quiet -volname dots -srcfolder "$APP" -ov -format UDZO "$OUT/dots.dmg"
codesign --force "$(timestamp)" -s "$IDENTITY" "$OUT/dots.dmg"
echo "$OUT/dots.dmg ($(du -h "$OUT/dots.dmg" | cut -f1))"
