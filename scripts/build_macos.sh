#!/bin/bash
set -euo pipefail
LUMAVIEW_SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$LUMAVIEW_SOURCE"
[ "$(uname -s)" = Darwin ] || { printf 'Build on macOS.\n' >&2; exit 1; }
[ "$(uname -m)" = arm64 ] || { printf 'Build with a native Apple Silicon shell and Python.\n' >&2; exit 1; }
LUMAVIEW_PYTHON="${LUMAVIEW_PYTHON:-python3}"
"$LUMAVIEW_PYTHON" -c 'import platform; assert platform.machine() == "arm64", "Python must be arm64"'
[ -f assets/lumaview.icns ] || { printf 'assets/lumaview.icns is missing.\n' >&2; exit 1; }
[ -f docs/LumaView-Guide.pdf ] || { printf 'docs/LumaView-Guide.pdf is missing.\n' >&2; exit 1; }
export MACOSX_DEPLOYMENT_TARGET=14.0
"$LUMAVIEW_PYTHON" -m PyInstaller --clean --noconfirm LumaView.spec
LUMAVIEW_APP="$LUMAVIEW_SOURCE/dist/LumaView.app"
LUMAVIEW_VERSION="$("$LUMAVIEW_PYTHON" -c 'from lumaview import __version__; print(__version__)')"
LUMAVIEW_NAME="LumaView-$LUMAVIEW_VERSION-macos-arm64"

# Ad-hoc signatures are required by arm64 macOS; they are not Developer ID signing.
/usr/bin/codesign --verify --deep --strict "$LUMAVIEW_APP"
LUMAVIEW_BUILT_ARCHS="$(/usr/bin/lipo -archs "$LUMAVIEW_APP/Contents/MacOS/LumaView")"
[ "$LUMAVIEW_BUILT_ARCHS" = arm64 ] || {
    printf 'Expected an arm64 executable; found: %s\n' "$LUMAVIEW_BUILT_ARCHS" >&2
    exit 1
}
QT_QPA_PLATFORM=offscreen "$LUMAVIEW_APP/Contents/MacOS/LumaView" --smoke-test

LUMAVIEW_DMG_STAGE="$(mktemp -d "${TMPDIR:-/tmp}/lumaview-dmg.XXXXXX")"
trap 'rm -rf -- "$LUMAVIEW_DMG_STAGE"' EXIT
/usr/bin/ditto "$LUMAVIEW_APP" "$LUMAVIEW_DMG_STAGE/LumaView.app"
/bin/ln -s /Applications "$LUMAVIEW_DMG_STAGE/Applications"
/bin/cp docs/LumaView-Guide.pdf "$LUMAVIEW_DMG_STAGE/"
/bin/cp LICENSE THIRD_PARTY.md "$LUMAVIEW_DMG_STAGE/"
/usr/bin/hdiutil create -ov -format UDZO -volname LumaView \
    -srcfolder "$LUMAVIEW_DMG_STAGE" "dist/$LUMAVIEW_NAME.dmg"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$LUMAVIEW_APP" "dist/$LUMAVIEW_NAME.zip"

# Source and installer archives preserve executable permission for Finder.
"$LUMAVIEW_PYTHON" scripts/package_sources.py
/bin/cp docs/LumaView-Guide.pdf dist/LumaView-Guide.pdf
(
    cd dist
    /usr/bin/shasum -a 256 "$LUMAVIEW_NAME.dmg" "$LUMAVIEW_NAME.zip" \
        "LumaView-$LUMAVIEW_VERSION-installer.zip" "LumaView-$LUMAVIEW_VERSION-github.zip" \
        LumaView-Guide.pdf > SHA256SUMS.txt
    /usr/bin/shasum -a 256 -c SHA256SUMS.txt
)
printf '\nBuilt dist/%s.dmg and release downloads.\n' "$LUMAVIEW_NAME"
