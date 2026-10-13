# Releases

Release version numbers must match `lumaview/__init__.py`, `pyproject.toml`, the `v<version>` tag and `docs/releases/v<version>.md`.

## GitHub Actions

Pushing a `v*` tag runs the macOS release workflow. It verifies the version, installs pinned dependencies, runs tests, builds the Apple Silicon app, validates its signature and startup, and packages the downloads. Successful builds create a **draft** release using the checked-in release notes. Failures do not publish a release.

From a source archive, the helper below clones the existing repository into a new folder under `~/Developer`, copies the release sources, preserves remote history and additional ignore rules, commits, tags and pushes. It waits for the macOS workflow and publishes the completed draft only after checking that all six assets are present:

```sh
bash scripts/publish_github.sh OWNER/REPOSITORY
```

The branch is pushed before the tag so that the workflows are present in newly created repositories. The helper finds the release run by workflow file, tag and commit. If no run appears, it starts `release.yml` explicitly on the same tag and waits for that build. API or permission errors stop with an explanation.

Requires `git`, `gh`, `rsync`, a configured Git author, an existing repository and GitHub CLI authentication with repository and workflow access. It does not force-push, overwrite a conflicting tag or replace a published release. It does not install the app on the machine running the command. Corrections to a version already tagged should use a new patch version and new release notes.

For an existing development checkout, commit the release changes and push the annotated tag manually. After the workflow succeeds, publish the draft on GitHub or with `gh release edit v<version> --draft=false --latest`.

**Build macOS** can also run manually: a branch build produces downloadable workflow artifacts; a version-tag build also creates the draft release. Native macOS UI testing, including Finder opening, permissions and printing, remains part of release review.

## Local build

On an Apple Silicon Mac, activate a Python 3.12 environment with `requirements-dev.txt` installed, then run:

```sh
QT_QPA_PLATFORM=offscreen python -m pytest
bash scripts/build_macos.sh
```

`dist/` contains the `.app`, DMG, app ZIP, source installer ZIP, full source ZIP, PDF guide and checksums. Application bundles contain the required runtime; optional Ghostscript and OptiPNG are not bundled.

Builds use ad-hoc signing. Developer ID signing and notarization are not configured. Do not describe these artifacts as notarized.
