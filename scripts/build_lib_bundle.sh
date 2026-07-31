#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)

OUTPUT_PATH="$REPO_DIR/lib.zip"
ASSET_ARCHIVE="$REPO_DIR/lib.zip"
BUNDLE_DATE=""
CIRCUITPYTHON_MAJOR=10

usage() {
    cat <<'EOF'
Build the minimal CircuitPython library bundle used by dc-metro.

Usage:
  scripts/build_lib_bundle.sh [options]

Options:
  --bundle-date YYYYMMDD  Use a specific Adafruit bundle release instead of latest.
  --circuitpython-major N Build for CircuitPython major version N (default: 10).
  --output PATH           Write the zip to PATH (default: repository lib.zip).
  --asset-archive PATH    Read project assets from PATH (default: repository lib.zip).
  -h, --help              Show this help.

The default invocation replaces lib.zip after the new archive passes validation.
EOF
}

while (($#)); do
    case "$1" in
        --bundle-date)
            if (($# < 2)); then
                echo "Error: --bundle-date requires a value." >&2
                exit 2
            fi
            BUNDLE_DATE=$2
            shift 2
            ;;
        --output)
            if (($# < 2)); then
                echo "Error: --output requires a value." >&2
                exit 2
            fi
            OUTPUT_PATH=$2
            shift 2
            ;;
        --circuitpython-major)
            if (($# < 2)); then
                echo "Error: --circuitpython-major requires a value." >&2
                exit 2
            fi
            CIRCUITPYTHON_MAJOR=$2
            shift 2
            ;;
        --asset-archive)
            if (($# < 2)); then
                echo "Error: --asset-archive requires a value." >&2
                exit 2
            fi
            ASSET_ARCHIVE=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for required_command in curl unzip zip; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        echo "Error: required command not found: $required_command" >&2
        exit 1
    fi
done

if [[ ! -f "$ASSET_ARCHIVE" ]]; then
    echo "Error: asset archive not found: $ASSET_ARCHIVE" >&2
    exit 1
fi

if [[ ! "$CIRCUITPYTHON_MAJOR" =~ ^[0-9]+$ ]]; then
    echo "Error: CircuitPython major version must be a number; got: $CIRCUITPYTHON_MAJOR" >&2
    exit 1
fi

if [[ -z "$BUNDLE_DATE" ]]; then
    echo "Finding the latest Adafruit CircuitPython bundle release..."
    RELEASE_URL=$(curl -fsSL -o /dev/null -w '%{url_effective}' \
        https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/latest)
    BUNDLE_DATE=${RELEASE_URL##*/}
fi

if [[ ! "$BUNDLE_DATE" =~ ^[0-9]{8}$ ]]; then
    echo "Error: bundle date must have YYYYMMDD format; got: $BUNDLE_DATE" >&2
    exit 1
fi

WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/dc-metro-lib.XXXXXX")
trap 'rm -rf -- "$WORK_DIR"' EXIT

BUNDLE_NAME="adafruit-circuitpython-bundle-$CIRCUITPYTHON_MAJOR.x-mpy-$BUNDLE_DATE"
BUNDLE_ARCHIVE="$WORK_DIR/$BUNDLE_NAME.zip"
BUNDLE_LIB="$WORK_DIR/$BUNDLE_NAME/lib"
STAGE_DIR="$WORK_DIR/stage"
ASSET_DIR="$WORK_DIR/assets"

BUNDLE_URL="https://github.com/adafruit/Adafruit_CircuitPython_Bundle/releases/download/$BUNDLE_DATE/$BUNDLE_NAME.zip"

echo "Downloading CircuitPython $CIRCUITPYTHON_MAJOR.x bundle $BUNDLE_DATE..."
curl -fL --retry 3 --retry-delay 2 -o "$BUNDLE_ARCHIVE" "$BUNDLE_URL"
unzip -q "$BUNDLE_ARCHIVE" -d "$WORK_DIR"

# These project assets do not come from the Adafruit library bundle. Extract
# them before replacing lib.zip so the default in-place workflow is safe.
unzip -q "$ASSET_ARCHIVE" lib/5x7.bdf lib/LICENSE -d "$ASSET_DIR"

mkdir -p \
    "$STAGE_DIR/lib/adafruit_bitmap_font" \
    "$STAGE_DIR/lib/adafruit_display_shapes" \
    "$STAGE_DIR/lib/adafruit_display_text" \
    "$STAGE_DIR/lib/adafruit_matrixportal"

required_bundle_files=(
    adafruit_connection_manager.mpy
    adafruit_requests.mpy
    neopixel.mpy
    adafruit_bitmap_font/__init__.py
    adafruit_bitmap_font/bitmap_font.mpy
    adafruit_bitmap_font/bdf.mpy
    adafruit_bitmap_font/glyph_cache.mpy
    adafruit_display_shapes/__init__.py
    adafruit_display_shapes/rect.mpy
    adafruit_display_text/__init__.mpy
    adafruit_display_text/label.mpy
    adafruit_matrixportal/__init__.py
    adafruit_matrixportal/matrix.mpy
)

for relative_path in "${required_bundle_files[@]}"; do
    if [[ ! -f "$BUNDLE_LIB/$relative_path" ]]; then
        echo "Error: required library is missing from the downloaded bundle: $relative_path" >&2
        exit 1
    fi
    cp "$BUNDLE_LIB/$relative_path" "$STAGE_DIR/lib/$relative_path"
done

cp "$ASSET_DIR/lib/5x7.bdf" "$ASSET_DIR/lib/LICENSE" "$STAGE_DIR/lib/"

CANDIDATE_ARCHIVE="$WORK_DIR/lib.zip"
(
    cd "$STAGE_DIR"
    zip -qr "$CANDIDATE_ARCHIVE" lib
)

unzip -tq "$CANDIDATE_ARCHIVE" >/dev/null
mkdir -p "$(dirname -- "$OUTPUT_PATH")"
mv -f -- "$CANDIDATE_ARCHIVE" "$OUTPUT_PATH"

ARCHIVE_BYTES=$(stat -c '%s' "$OUTPUT_PATH")
UNPACKED_BYTES=$(find "$STAGE_DIR/lib" -type f -printf '%s\n' | awk '{ total += $1 } END { print total + 0 }')
FILE_COUNT=$(find "$STAGE_DIR/lib" -type f | wc -l)

echo "Created $OUTPUT_PATH"
echo "Adafruit bundle: $BUNDLE_DATE (CircuitPython $CIRCUITPYTHON_MAJOR.x MPY)"
echo "Contents: $FILE_COUNT files, $UNPACKED_BYTES bytes unpacked, $ARCHIVE_BYTES bytes zipped"
