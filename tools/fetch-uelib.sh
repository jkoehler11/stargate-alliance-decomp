#!/bin/sh
# Fetch UELib at the pinned commit into tools/uelib (git-ignored), then apply this repository's patches.
# The commit is the one in tools/versions.lock; update both together.
set -eu
UELIB_COMMIT=8236c39
UELIB_REPO=https://github.com/EliotVU/Unreal-Library.git
here=$(cd "$(dirname "$0")" && pwd)
dest="$here/uelib"
if [ -d "$dest/.git" ]; then
    git -C "$dest" fetch --quiet origin "$UELIB_COMMIT" 2>/dev/null || \
        git -C "$dest" fetch --quiet --depth 1 origin
    git -C "$dest" checkout --quiet "$UELIB_COMMIT"
else
    git clone --quiet "$UELIB_REPO" "$dest"
    git -C "$dest" checkout --quiet "$UELIB_COMMIT" 2>/dev/null || {
        git -C "$dest" fetch --quiet --depth 50 origin && git -C "$dest" checkout --quiet "$UELIB_COMMIT"; }
fi

# tools/patches/uelib-*.patch are applied in name order, idempotently: one that already applies in
# reverse is reported and left alone, and one that neither applies nor reverses stops the fetch with
# exit 3 rather than leaving a checkout that is patched halfway. A dirty checkout is refused for the
# same reason -- `git apply --check` would otherwise hide the earlier patch behind the working tree.
for patch in "$here"/patches/uelib-*.patch; do
    [ -e "$patch" ] || continue
    name=$(basename "$patch")
    if git -C "$dest" apply --reverse --check "$patch" 2>/dev/null; then
        echo "patch: $name already applied"
    elif git -C "$dest" apply --check "$patch" 2>/dev/null; then
        git -C "$dest" apply "$patch"
        echo "patch: applied $name"
    else
        echo "patch: refusing $name -- it neither applies nor reverses against $UELIB_COMMIT" >&2
        exit 3
    fi
done
echo "UELib at $(git -C "$dest" rev-parse --short HEAD) ($dest)"
