#!/bin/bash
# Publish from a source archive using the local git/gh credentials.
set -euo pipefail
LUMAVIEW_SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
LUMAVIEW_REPO="${1:-}"
die() { printf '\n%s\n' "$*" >&2; exit 1; }
[[ "$LUMAVIEW_REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die 'Usage: bash scripts/publish_github.sh OWNER/REPOSITORY'
for tool in git gh rsync; do command -v "$tool" >/dev/null || die "Required command not found: $tool"; done
git var GIT_AUTHOR_IDENT >/dev/null || die 'Configure your Git user.name and user.email before publishing.'
gh auth status --hostname github.com >/dev/null
gh repo view "$LUMAVIEW_REPO" --json nameWithOwner --jq .nameWithOwner
LUMAVIEW_VERSION="$(sed -n "s/^__version__ = '\([^']*\)'/\1/p" "$LUMAVIEW_SOURCE/lumaview/__init__.py")"
[[ "$LUMAVIEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'Invalid source version.'
LUMAVIEW_TAG="v$LUMAVIEW_VERSION"
LUMAVIEW_NOTES="docs/releases/$LUMAVIEW_TAG.md"
[ -s "$LUMAVIEW_SOURCE/$LUMAVIEW_NOTES" ] || die "Missing release notes: $LUMAVIEW_NOTES"

LUMAVIEW_PUBLISH_ROOT="${LUMAVIEW_PUBLISH_ROOT:-$HOME/Developer}"
mkdir -p "$LUMAVIEW_PUBLISH_ROOT"
LUMAVIEW_WORK="$(mktemp -d "$LUMAVIEW_PUBLISH_ROOT/LumaView-publish.XXXXXX")"
printf '\nWorking folder: %s\n' "$LUMAVIEW_WORK"
gh repo clone "$LUMAVIEW_REPO" "$LUMAVIEW_WORK/repository"
cd "$LUMAVIEW_WORK/repository"
if ! git rev-parse --verify HEAD >/dev/null 2>&1; then git checkout -B main; fi
LUMAVIEW_BRANCH="$(git branch --show-current)"
[ -n "$LUMAVIEW_BRANCH" ] || die 'The clone has no active branch.'
if [ -f .gitignore ]; then cp .gitignore "$LUMAVIEW_WORK/previous.gitignore"; fi

rsync -a --exclude='.git/' --exclude='.DS_Store' --exclude='__pycache__/' \
    --exclude='.venv*/' --exclude='.pytest_cache/' --exclude='.ruff_cache/' \
    --exclude='.mypy_cache/' --exclude='*.egg-info/' --exclude='build/' --exclude='dist/' \
    --exclude='.env' --exclude='.env.*' --exclude='*.log' \
    "$LUMAVIEW_SOURCE/" ./

# Keep the remote's extra rules, and make retries of the same source idempotent.
if [ -s "$LUMAVIEW_WORK/previous.gitignore" ]; then
    LUMAVIEW_IGNORE_LINES="$(wc -l < .gitignore | tr -d ' ')"
    head -n "$LUMAVIEW_IGNORE_LINES" "$LUMAVIEW_WORK/previous.gitignore" > "$LUMAVIEW_WORK/ignore-prefix"
    if cmp -s .gitignore "$LUMAVIEW_WORK/ignore-prefix"; then
        cp "$LUMAVIEW_WORK/previous.gitignore" .gitignore
    else
        printf '\n# Repository-specific ignore rules\n' >> .gitignore
        cat "$LUMAVIEW_WORK/previous.gitignore" >> .gitignore
    fi
fi
# Finder metadata has no place in the published tree, even if previously tracked.
while IFS= read -r -d '' LUMAVIEW_METADATA; do
    git rm -- "$LUMAVIEW_METADATA"
done < <(git ls-files -z -- '.DS_Store' ':(glob)**/.DS_Store')
git add --all
for required in lumaview/bilevel.py .github/workflows/release.yml Install.command "$LUMAVIEW_NOTES"; do
    git ls-files --error-unmatch -- "$required" >/dev/null || die "A repository ignore rule excluded $required. Nothing was pushed."
done
if ! git diff --cached --quiet; then
    git commit -m "Release $LUMAVIEW_TAG: image viewer, batch conversion and macOS distribution"
fi
LUMAVIEW_COMMIT="$(git rev-parse HEAD)"
if git show-ref --verify --quiet "refs/tags/$LUMAVIEW_TAG"; then
    [ "$(git rev-list -n 1 "$LUMAVIEW_TAG")" = "$LUMAVIEW_COMMIT" ] || die "Tag $LUMAVIEW_TAG already points to another commit. Nothing was pushed."
else
    git tag -a "$LUMAVIEW_TAG" -m "LumaView $LUMAVIEW_VERSION"
fi

LUMAVIEW_DESCRIPTION="Image viewer and batch converter for Apple Silicon Macs, inspired by IrfanView. Supports advanced resizing, format-specific export options, and adaptive monochrome TIFF conversion."
gh repo edit "$LUMAVIEW_REPO" --description "$LUMAVIEW_DESCRIPTION"
# Register the branch/workflows before sending the release tag on a new repository.
git push -u origin "$LUMAVIEW_BRANCH"
git push origin "refs/tags/$LUMAVIEW_TAG"

printf '\nWaiting for the macOS release build…\n'
LUMAVIEW_RUN=""
LUMAVIEW_RUN_FILTER="[.workflow_runs[] | select(.path == \".github/workflows/release.yml\" and .head_branch == \"$LUMAVIEW_TAG\" and .head_sha == \"$LUMAVIEW_COMMIT\" and (.event == \"push\" or .event == \"workflow_dispatch\"))] | sort_by(.id) | last | .id // empty"
wait_for_release_run() {
    local attempt
    for ((attempt=0; attempt<$1; attempt++)); do
        LUMAVIEW_RUN="$(gh api "repos/$LUMAVIEW_REPO/actions/runs?head_sha=$LUMAVIEW_COMMIT&per_page=100" \
            --jq "$LUMAVIEW_RUN_FILTER")" || die 'Could not query GitHub Actions. Check gh authentication and retry this script.'
        [ -z "$LUMAVIEW_RUN" ] || return 0
        sleep 3
    done
    return 1
}
if ! wait_for_release_run 20; then
    printf '\nNo tag-triggered build found; starting the release workflow for %s…\n' "$LUMAVIEW_TAG"
    gh workflow run release.yml --repo "$LUMAVIEW_REPO" --ref "$LUMAVIEW_TAG" || \
        die "Could not start the release workflow. Check https://github.com/$LUMAVIEW_REPO/actions and retry this script."
    wait_for_release_run 40 || die "The requested release workflow did not appear. Check https://github.com/$LUMAVIEW_REPO/actions. Sources and tag are already uploaded."
fi
if ! gh run watch "$LUMAVIEW_RUN" --repo "$LUMAVIEW_REPO" --exit-status; then
    die "Build failed; the release was not published. Logs: https://github.com/$LUMAVIEW_REPO/actions/runs/$LUMAVIEW_RUN"
fi

LUMAVIEW_ASSETS="$(gh release view "$LUMAVIEW_TAG" --repo "$LUMAVIEW_REPO" --json assets --jq '.assets[].name')"
for asset in "LumaView-$LUMAVIEW_VERSION-macos-arm64.dmg" "LumaView-$LUMAVIEW_VERSION-macos-arm64.zip" \
    "LumaView-$LUMAVIEW_VERSION-installer.zip" "LumaView-$LUMAVIEW_VERSION-github.zip" \
    LumaView-Guide.pdf SHA256SUMS.txt; do
    printf '%s\n' "$LUMAVIEW_ASSETS" | grep -Fx -- "$asset" >/dev/null || die "Missing release asset: $asset. The release remains a draft."
done
LUMAVIEW_IS_DRAFT="$(gh release view "$LUMAVIEW_TAG" --repo "$LUMAVIEW_REPO" --json isDraft --jq .isDraft)"
if [ "$LUMAVIEW_IS_DRAFT" = true ]; then
    gh release edit "$LUMAVIEW_TAG" --repo "$LUMAVIEW_REPO" --draft=false --latest --verify-tag
fi
printf '\nPublished: https://github.com/%s/releases/tag/%s\n' "$LUMAVIEW_REPO" "$LUMAVIEW_TAG"
printf 'Local checkout: %s/repository\n' "$LUMAVIEW_WORK"
