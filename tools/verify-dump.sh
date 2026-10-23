#!/bin/sh
# Verify the working dump against inventory/manifest.sha256.
# Usage: tools/verify-dump.sh [DUMP_ROOT]   (default: $SG1_DUMP)
set -eu
root="${1:-${SG1_DUMP:-}}"
if [ -z "$root" ]; then
    echo "error: pass a dump root or set SG1_DUMP" >&2
    echo "       (the directory containing README.txt and Stargate_SG-1_-_The_Alliance-2005-12-15/)" >&2
    exit 2
fi
if [ ! -d "$root" ]; then
    echo "error: not a directory: $root" >&2
    exit 2
fi
exec python3 "$(dirname "$0")/make-manifest.py" "$root" \
    -o "$(dirname "$0")/../inventory/manifest.sha256" --check
