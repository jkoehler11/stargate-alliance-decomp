#!/bin/sh
# Fetch UELib at the pinned commit into tools/uelib (git-ignored).
# The commit is the one in tools/versions.lock; update both together.
set -eu
UELIB_COMMIT=8236c39
UELIB_REPO=https://github.com/EliotVU/Unreal-Library.git
dest="$(dirname "$0")/uelib"
if [ -d "$dest/.git" ]; then
    git -C "$dest" fetch --quiet origin "$UELIB_COMMIT" 2>/dev/null || \
        git -C "$dest" fetch --quiet --depth 1 origin
    git -C "$dest" checkout --quiet "$UELIB_COMMIT"
else
    git clone --quiet "$UELIB_REPO" "$dest"
    git -C "$dest" checkout --quiet "$UELIB_COMMIT" 2>/dev/null || {
        git -C "$dest" fetch --quiet --depth 50 origin && git -C "$dest" checkout --quiet "$UELIB_COMMIT"; }
fi
echo "UELib at $(git -C "$dest" rev-parse --short HEAD) ($dest)"
