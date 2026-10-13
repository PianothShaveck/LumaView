#!/bin/bash
# Installs only into this user's home directory; no Homebrew or administrator access.
set -euo pipefail
umask 022

LUMAVIEW_SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
LUMAVIEW_ROOT="$HOME/Library/Application Support/LumaView"
LUMAVIEW_APP="$HOME/Applications/LumaView.app"
LUMAVIEW_UV_VERSION="0.12.22"
LUMAVIEW_PYTHON_VERSION="3.12.15"
# https://github.com/astral-sh/uv/releases/download/0.12.22/uv-aarch64-apple-darwin.tar.gz.sha256
LUMAVIEW_UV_SHA256="5d714de09501a59393ceca78f4bc232a50478729640d251907160299b2a93ddd"
LUMAVIEW_TMP=""
LUMAVIEW_APP_STAGE=""
LUMAVIEW_LOCKED=0

finish() {
    local result=$?
    [ -z "$LUMAVIEW_TMP" ] || /bin/rm -rf -- "$LUMAVIEW_TMP"
    [ -z "$LUMAVIEW_APP_STAGE" ] || /bin/rm -rf -- "$LUMAVIEW_APP_STAGE"
    if [ "$LUMAVIEW_LOCKED" = 1 ]; then /bin/rmdir "$LUMAVIEW_ROOT/install.lock" 2>/dev/null || true; fi
    if [ "$result" -ne 0 ]; then
        printf '\nInstallation stopped. Your previous installation was not replaced.\n'
        printf 'Check the error above, then run Install.command again.\n'
    fi
}
trap finish EXIT
die() { printf '\n%s\n' "$*" >&2; exit 1; }

[ "$(/usr/bin/uname -s)" = Darwin ] || die 'This installer requires macOS.'
[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" = 1 ] || die 'LumaView requires an Apple Silicon Mac.'
if [ "$(/usr/bin/uname -m)" != arm64 ]; then
    exec /usr/bin/arch -arm64 /bin/bash "$0" "$@"
fi
LUMAVIEW_OS_MAJOR="$(/usr/bin/sw_vers -productVersion | /usr/bin/cut -d. -f1)"
[ "$LUMAVIEW_OS_MAJOR" -ge 14 ] || die 'LumaView requires macOS 14 or later.'
[ -f "$LUMAVIEW_SOURCE/lumaview/__main__.py" ] || die 'Keep Install.command inside the complete extracted LumaView folder.'
[ -f "$LUMAVIEW_SOURCE/requirements.txt" ] || die 'requirements.txt is missing. Download the complete source archive again.'

/bin/mkdir -p "$LUMAVIEW_ROOT/tools" "$LUMAVIEW_ROOT/runtime" "$LUMAVIEW_ROOT/versions" "$HOME/Applications"
if ! /bin/mkdir "$LUMAVIEW_ROOT/install.lock" 2>/dev/null; then
    die "Another installation is running. If it was interrupted, remove the empty folder: $LUMAVIEW_ROOT/install.lock"
fi
LUMAVIEW_LOCKED=1
LUMAVIEW_TMP="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/lumaview-install.XXXXXX")"
LUMAVIEW_APP_STAGE="$(/usr/bin/mktemp -d "$HOME/Applications/.lumaview-stage.XXXXXX")"

if [ -e "$LUMAVIEW_APP" ]; then
    LUMAVIEW_EXISTING_ID="$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$LUMAVIEW_APP/Contents/Info.plist" 2>/dev/null || true)"
    [ "$LUMAVIEW_EXISTING_ID" = org.lumaview.LumaView ] || die "$LUMAVIEW_APP already exists and is not a recognized LumaView installation. Move it before continuing."
fi

LUMAVIEW_UV="$LUMAVIEW_ROOT/tools/uv-$LUMAVIEW_UV_VERSION"
if [ ! -x "$LUMAVIEW_UV" ]; then
    printf 'Downloading the Python installer…\n'
    LUMAVIEW_ARCHIVE="$LUMAVIEW_TMP/uv.tar.gz"
    /usr/bin/curl --fail --location --retry 3 --connect-timeout 20 --max-time 300 \
        --proto '=https' --tlsv1.2 \
        "https://github.com/astral-sh/uv/releases/download/$LUMAVIEW_UV_VERSION/uv-aarch64-apple-darwin.tar.gz" \
        --output "$LUMAVIEW_ARCHIVE"
    LUMAVIEW_ACTUAL_SHA="$(/usr/bin/shasum -a 256 "$LUMAVIEW_ARCHIVE" | /usr/bin/awk '{print $1}')"
    [ "$LUMAVIEW_ACTUAL_SHA" = "$LUMAVIEW_UV_SHA256" ] || die 'The installer download failed its SHA-256 check. No downloaded code was executed.'
    /usr/bin/tar -xzf "$LUMAVIEW_ARCHIVE" -C "$LUMAVIEW_TMP"
    /bin/cp "$LUMAVIEW_TMP/uv-aarch64-apple-darwin/uv" "$LUMAVIEW_UV"
    /bin/chmod 755 "$LUMAVIEW_UV"
fi
"$LUMAVIEW_UV" --version

# A dedicated runtime never changes the user's shell, system Python or packages.
export UV_PYTHON_INSTALL_DIR="$LUMAVIEW_ROOT/python"
export UV_CACHE_DIR="$LUMAVIEW_ROOT/cache"
export UV_PYTHON_PREFERENCE=only-managed
export UV_NO_CONFIG=1
export UV_PYTHON_BIN_DIR="$LUMAVIEW_ROOT/tools/python-bin"
unset PYTHONHOME PYTHONPATH VIRTUAL_ENV || true

printf '\nChecking native Python %s…\n' "$LUMAVIEW_PYTHON_VERSION"
# Explicit managed preference prevents an existing Intel Python from being selected.
"$LUMAVIEW_UV" python install "$LUMAVIEW_PYTHON_VERSION"
LUMAVIEW_DEP_HASH="$(/usr/bin/shasum -a 256 "$LUMAVIEW_SOURCE/requirements.txt" | /usr/bin/awk '{print substr($1,1,16)}')"
LUMAVIEW_ENV="$LUMAVIEW_ROOT/runtime/python-$LUMAVIEW_PYTHON_VERSION-$LUMAVIEW_DEP_HASH"
if [ ! -x "$LUMAVIEW_ENV/bin/python" ]; then
    "$LUMAVIEW_UV" venv --python "$LUMAVIEW_PYTHON_VERSION" "$LUMAVIEW_ENV"
fi
printf '\nChecking image libraries…\n'
"$LUMAVIEW_UV" pip install --python "$LUMAVIEW_ENV/bin/python" --only-binary :all: \
    --index-url https://pypi.org/simple -r "$LUMAVIEW_SOURCE/requirements.txt"
"$LUMAVIEW_UV" pip check --python "$LUMAVIEW_ENV/bin/python"
"$LUMAVIEW_ENV/bin/python" - <<'PY'
import platform
from PySide6 import QtCore, QtGui, QtWidgets, QtSvg, QtPrintSupport
from PIL import Image, features
import numpy, numba, llvmlite, pypdfium2, reportlab, pillow_heif, imagecodecs, rawpy
import Vision, Quartz, Foundation
if platform.machine() != 'arm64':
    raise SystemExit('The installed Python is not native Apple Silicon.')
for codec in ('jpg', 'zlib', 'libtiff', 'webp', 'jpg_2000'):
    if not features.check(codec):
        raise SystemExit(f'Required image codec unavailable: {codec}')
print('Native Python and image libraries verified.')
PY

LUMAVIEW_VERSION="$("$LUMAVIEW_ENV/bin/python" -c 'import ast,pathlib,sys; t=ast.parse(pathlib.Path(sys.argv[1]).read_text()); print(next(ast.literal_eval(n.value) for n in t.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=="__version__" for x in n.targets)))' "$LUMAVIEW_SOURCE/lumaview/__init__.py")"
LUMAVIEW_RELEASE="$LUMAVIEW_ROOT/versions/$LUMAVIEW_VERSION-$(/bin/date +%Y%m%d%H%M%S)-$$"
/bin/mkdir -p "$LUMAVIEW_RELEASE"
for item in lumaview assets docs LICENSE THIRD_PARTY.md; do
    [ ! -e "$LUMAVIEW_SOURCE/$item" ] || /bin/cp -R "$LUMAVIEW_SOURCE/$item" "$LUMAVIEW_RELEASE/"
done
printf '%s\n' "$LUMAVIEW_ENV" > "$LUMAVIEW_RELEASE/runtime-path"

printf '\nPreparing native monochrome conversion…\n'
PYTHONPATH="$LUMAVIEW_RELEASE" PYTHONNOUSERSITE=1 "$LUMAVIEW_ENV/bin/python" -c \
    'from lumaview.bilevel import warm_up; warm_up()'

LUMAVIEW_STAGED_APP="$LUMAVIEW_APP_STAGE/LumaView.app"
/bin/mkdir -p "$LUMAVIEW_STAGED_APP/Contents/MacOS" "$LUMAVIEW_STAGED_APP/Contents/Resources"
printf '%s\n' "$LUMAVIEW_RELEASE" > "$LUMAVIEW_STAGED_APP/Contents/Resources/source-path"
if [ -f "$LUMAVIEW_SOURCE/assets/lumaview.icns" ]; then
    /bin/cp "$LUMAVIEW_SOURCE/assets/lumaview.icns" "$LUMAVIEW_STAGED_APP/Contents/Resources/lumaview.icns"
fi
cat > "$LUMAVIEW_STAGED_APP/Contents/MacOS/LumaView" <<'SH'
#!/bin/bash
set -euo pipefail
LUMAVIEW_CONTENTS="$(cd -- "$(dirname -- "$0")/.." && pwd)"
IFS= read -r LUMAVIEW_CODE < "$LUMAVIEW_CONTENTS/Resources/source-path"
IFS= read -r LUMAVIEW_RUNTIME < "$LUMAVIEW_CODE/runtime-path"
unset PYTHONHOME PYTHONPATH VIRTUAL_ENV || true
export PYTHONPATH="$LUMAVIEW_CODE"
export PYTHONNOUSERSITE=1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$HOME/Library/Logs/LumaView"
cd "$LUMAVIEW_CODE"
exec "$LUMAVIEW_RUNTIME/bin/python" -m lumaview "$@" >> "$HOME/Library/Logs/LumaView/launch.log" 2>&1
SH
/bin/chmod 755 "$LUMAVIEW_STAGED_APP/Contents/MacOS/LumaView"
"$LUMAVIEW_ENV/bin/python" - "$LUMAVIEW_STAGED_APP/Contents/Info.plist" "$LUMAVIEW_VERSION" <<'PY'
import plistlib, sys
info = {
    'CFBundleName':'LumaView', 'CFBundleDisplayName':'LumaView',
    'CFBundleExecutable':'LumaView', 'CFBundleIdentifier':'org.lumaview.LumaView',
    'CFBundlePackageType':'APPL', 'CFBundleShortVersionString':sys.argv[2],
    'CFBundleVersion':sys.argv[2], 'CFBundleIconFile':'lumaview.icns',
    'LSMinimumSystemVersion':'14.0', 'NSHighResolutionCapable':True,
    'LSApplicationCategoryType':'public.app-category.photography',
    'CFBundleDocumentTypes':[{'CFBundleTypeName':'Images', 'CFBundleTypeRole':'Editor',
      'LSHandlerRank':'Alternate', 'LSItemContentTypes':['public.image','com.adobe.pdf','public.svg-image']}],
}
with open(sys.argv[1], 'wb') as f: plistlib.dump(info, f)
PY

# Keep the previous app in place until dependency checks and staging have succeeded.
LUMAVIEW_OLD_APP="$LUMAVIEW_APP_STAGE/Previous-LumaView.app"
if [ -e "$LUMAVIEW_APP" ]; then /bin/mv "$LUMAVIEW_APP" "$LUMAVIEW_OLD_APP"; fi
if ! /bin/mv "$LUMAVIEW_STAGED_APP" "$LUMAVIEW_APP"; then
    [ ! -e "$LUMAVIEW_OLD_APP" ] || /bin/mv "$LUMAVIEW_OLD_APP" "$LUMAVIEW_APP"
    die 'Could not install LumaView.app.'
fi
printf '\nInstalled: %s\n' "$LUMAVIEW_APP"
if [ "${LUMAVIEW_NO_OPEN:-0}" != 1 ]; then
    /usr/bin/open "$LUMAVIEW_APP" || printf 'Open LumaView from your Applications folder.\n'
fi
