#!/bin/sh
# Assemble a self-contained local add-on folder that Home Assistant builds itself (no registry).
# Copy the result into the HA /addons share (Samba or SSH add-on), then in the Add-on Store:
# ⋮ → Check for updates → "Local add-ons" → mFRR Profit Tracker.
set -eu
cd "$(dirname "$0")/.."
OUT="${1:-build/local-addon}/mfrr_tracker"

rm -rf "$OUT"
mkdir -p "$OUT"
cp Dockerfile .dockerignore "$OUT/"
cp mfrr_tracker/DOCS.md mfrr_tracker/README.md mfrr_tracker/CHANGELOG.md "$OUT/"
# Without "image:" the Supervisor builds the Dockerfile in this folder
grep -v -e '^image:' -e '^# Prebuilt by' mfrr_tracker/config.yaml > "$OUT/config.yaml"
rsync -a --exclude node_modules --exclude dist frontend "$OUT/"
rsync -a --exclude data --exclude logs --exclude __pycache__ --exclude '*.db' backend "$OUT/"

echo "Local add-on written to $OUT"
echo "Copy it to /addons/mfrr_tracker on Home Assistant, e.g.: scp -r $OUT root@homeassistant.local:/addons/"
