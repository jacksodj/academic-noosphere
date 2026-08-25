#!/bin/bash
# Sign the built .app (including every Mach-O inside the frozen sidecar),
# notarize + staple the APP, then package/sign/notarize/staple the DMG.
# Run from the repo root AFTER `npx tauri build` in app/.
#
#   packaging/sign-and-dmg.sh [--allow-unnotarized]
#
# Notarization is REQUIRED: any failure (missing keychain profile, submission
# not Accepted, staple failure) aborts with a nonzero exit — a skipped
# notarization once shipped a "cannot verify free of malware" DMG (issue on
# v0.2.2). --allow-unnotarized is the explicit dev-only escape hatch.
#
# Ordering matters: the app is stapled BEFORE hdiutil snapshots it, so the
# copy users drag out of the DMG carries its ticket physically (no reliance
# on Gatekeeper's online lookup).
#
# One-time setup (needs your Apple ID; app-specific password from
# account.apple.com → Sign-In and Security → App-Specific Passwords):
#   xcrun notarytool store-credentials noosphere-notary \
#     --apple-id <you@example.com> --team-id Z3L3V842L2
set -euo pipefail

IDENTITY="Developer ID Application: Dennis Jackson (Z3L3V842L2)"
PROFILE="noosphere-notary"
APP="app/src-tauri/target/release/bundle/macos/Academic Noosphere.app"
ENTITLEMENTS="packaging/entitlements.plist"
VERSION=$(python3 -c "import json;print(json.load(open('app/src-tauri/tauri.conf.json'))['version'])")
BUNDLE_DIR="app/src-tauri/target/release/bundle"
DMG="$BUNDLE_DIR/Academic Noosphere_${VERSION}_aarch64_signed.dmg"
ALLOW_UNNOTARIZED="${1:-}"

[ -d "$APP" ] || { echo "no built app at $APP — run 'npx tauri build' in app/ first" >&2; exit 1; }

notarize() { # notarize <path> — submit and require Accepted
  local path="$1" out status
  echo "== notarizing $(basename "$path")"
  out=$(xcrun notarytool submit "$path" --keychain-profile "$PROFILE" --wait --output-format json)
  status=$(printf '%s' "$out" | python3 -c "import json,sys;print(json.load(sys.stdin).get('status',''))")
  if [ "$status" != "Accepted" ]; then
    echo "NOTARIZATION FAILED (status: ${status:-unknown})" >&2
    printf '%s\n' "$out" >&2
    exit 3
  fi
  echo "   status: Accepted"
}

echo "== signing nested sidecar Mach-O files"
SIDECAR="$APP/Contents/Resources/sidecar/noosphere-core"
find "$SIDECAR" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 |
  xargs -0 codesign --force --options runtime --timestamp -s "$IDENTITY"
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

if xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null 2>&1; then
  APPZIP="$BUNDLE_DIR/notarize-app.zip"
  ditto -c -k --keepParent "$APP" "$APPZIP"
  notarize "$APPZIP"
  rm -f "$APPZIP"
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP" && echo "app staple: OK"
elif [ "$ALLOW_UNNOTARIZED" = "--allow-unnotarized" ]; then
  echo "!! UNNOTARIZED build (explicitly allowed) — do not publish this DMG"
else
  echo "NOTARIZATION UNAVAILABLE: keychain profile '$PROFILE' not reachable." >&2
  echo "Fix the profile (see header) or pass --allow-unnotarized for a dev build." >&2
  exit 2
fi

echo "== building DMG (from the stapled app)"
rm -f "$DMG"
hdiutil create -volname "Academic Noosphere" -srcfolder "$APP" -ov -format UDZO "$DMG" >/dev/null
codesign --force --timestamp -s "$IDENTITY" "$DMG"

if [ "$ALLOW_UNNOTARIZED" != "--allow-unnotarized" ]; then
  notarize "$DMG"
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG" && echo "dmg staple: OK"
  spctl -a -t open --context context:primary-signature "$DMG" -v 2>&1 | tail -1
fi
echo "dmg: $DMG"
