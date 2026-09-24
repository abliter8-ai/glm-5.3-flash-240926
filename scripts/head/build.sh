#!/usr/bin/env bash
# IP-327: build the candidate image from the 0f49cfd tree on __HEAD_HOST__ while v3 serves (mirrors IP-314 build_candidate.sh).
set -euo pipefail
TREE="$HOME/glm53-exl3-overlay-0f49cfd"
LOG="$HOME/ip327/logs"
TAG="glm53-exl3-local:0f49cfd"
mkdir -p "$LOG"
cd "$TREE"
free -m > "$LOG/build-memory-before.txt"
MEM_KB=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
[ "$MEM_KB" -gt 7340032 ] || { echo "Insufficient live-build headroom: ${MEM_KB} KiB"; exit 3; }
# Keep the 2.6 GB transplant set out of the build context (the Dockerfile never copies it).
mv ablit/transplant "$HOME/ip327/transplant-aside"
trap 'mv "$HOME/ip327/transplant-aside" "$TREE/ablit/transplant"' EXIT
# Same formula as start.sh overlay_recipe_hash(), from this tree's absolute path.
SCRIPT_DIR="$TREE"
STAMP=$({
    printf '%s\n' "$SCRIPT_DIR/Dockerfile"
    find "$SCRIPT_DIR/overlay" "$SCRIPT_DIR/files" "$SCRIPT_DIR/tests" "$SCRIPT_DIR/ablit" -type f \
        ! -path '*/__pycache__/*' ! -path '*/.pytest_cache/*' ! -path '*/ablit/transplant/*' \
        ! -path '*/files/nfs-server/*' ! -path '*/files/nfs-share.sh' ! -name '*.pyc' 2>/dev/null
} | LC_ALL=C sort | xargs -d '\n' -r sha256sum | sha256sum | awk '{print $1}')
printf '%s\n' "$STAMP" > "$LOG/candidate-stamp.txt"
date -u +%FT%TZ > "$LOG/build.start"
docker build --memory=4g --memory-swap=4g --build-arg "GLM53_RECIPE_STAMP=$STAMP" -t "$TAG" . > "$LOG/build.log" 2>&1
date -u +%FT%TZ > "$LOG/build.end"
docker image inspect "$TAG" --format '{{.Id}} {{index .Config.Labels "glm53.recipe.stamp"}}' > "$LOG/candidate-image.txt"
free -m > "$LOG/build-memory-after.txt"
touch "$LOG/build.done"
cat "$LOG/candidate-image.txt"
