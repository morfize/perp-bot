#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    printf 'Usage: %s <asset-name>\n' "$0" >&2
    exit 1
fi

ASSET_NAME="$1"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

cd "$ROOT"
rm -rf build/pyinstaller build/spec
mkdir -p build/binary build/pyinstaller build/spec dist

uvx --from pyinstaller pyinstaller \
    --noconfirm \
    --clean \
    --onefile \
    --name perpbot \
    --paths src \
    --distpath build/binary \
    --workpath build/pyinstaller \
    --specpath build/spec \
    main.py

tar -C build/binary -czf "dist/$ASSET_NAME" perpbot
shasum -a 256 "dist/$ASSET_NAME" > "dist/$ASSET_NAME.sha256"
