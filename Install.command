#!/bin/bash
# Finder entry point. The folder may contain spaces or non-ASCII characters.
set -u
LUMAVIEW_SOURCE="$(cd -- "$(dirname -- "$0")" && pwd)"
/bin/bash "$LUMAVIEW_SOURCE/scripts/bootstrap_macos.sh"
LUMAVIEW_STATUS=$?
printf '\nPress Return to close this window. '
read -r _ || true
exit "$LUMAVIEW_STATUS"
