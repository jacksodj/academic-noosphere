#!/bin/bash
# Sign the built .app (including every Mach-O inside the frozen sidecar),
# package a DMG, and — when a notarytool keychain profile exists — notarize
# and staple. Run from the repo root AFTER `npx tauri build` in app/.
#
#   packaging/sign-and-dmg.sh
#
# One-time notarization setup (needs your Apple ID; store an app-specific
# password from appleid.apple.com under a keychain profile):
#   xcrun notarytool store-credentials noosphere-notary \
#     --apple-id <you@example.com> --team-id Z3L3V842L2
set -euo pipefail

IDENTITY="Developer ID Application: Dennis Jackson (Z3L3V842L2)"
PROFILE="noosphere-notary"
APP="app/src-tauri/target/release/bundle/macos/Academic Noosphere.app"
ENTITLEMENTS="packaging/entitlements.plist"
VERSION=$(python3 -c "import json;print(json.load(open('app/src-tauri/tauri.conf.json'))['version'])")
DMG="app/src-tauri/target/release/bundle/Academic Noosphere_${VERSION}_aarch64_signed.dmg"

[ -d "$APP" ] || { echo "no built app at $APP — run 'npx tauri build' in app/ first" >&2; exit 1; }

echo "== signing nested sidecar Mach-O files"
SIDECAR="$APP/Contents/Resources/sidecar/noosphere-core"
find "$SIDECAR" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 |
  xargs -0 codesign --force --options runtime --timestamp -s "$IDENTITY"
# Frameworks and extensionless Mach-O executables (Python.framework etc.)
find "$SIDECAR" -type f -perm +111 ! -name "*.so" ! -name "*.dylib" -print0 |
  while IFS= read -r -d '' f; do
    file -b "$f" | grep -q "Mach-O" &&
      codesign --force --options runtime --timestamp \
        --entitlements "$ENTITLEMENTS" -s "$IDENTITY" "$f"
  done || true

echo "== signing app bundle"
codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" -s "$IDENTITY" "$APP"
codesign --verify --strict --deep "$APP" && echo "codesign verify: OK"

echo "== building DMG"
rm -f "$DMG"
hdiutil create -volname "Academic Noosphere" -srcfolder "$APP" -ov -format UDZO "$DMG" >/dev/null
codesign --force --timestamp -s "$IDENTITY" "$DMG"
echo "dmg: $DMG"

if xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null 2>&1; then
  echo "== notarizing (profile: $PROFILE)"
  xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler staple "$APP"
  echo "notarized + stapled"
else
  echo "== notarization SKIPPED: no keychain profile '$PROFILE'"
  echo "   set it up once with:"
  echo "   xcrun notarytool store-credentials $PROFILE --apple-id <your-apple-id> --team-id Z3L3V842L2"
  echo "   (password = app-specific password from appleid.apple.com)"
  echo "   then re-run this script; users must right-click -> Open until then."
fi
