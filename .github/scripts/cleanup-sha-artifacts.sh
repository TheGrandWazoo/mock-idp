#!/usr/bin/env bash
# cleanup-sha-artifacts.sh VERSION [CURRENT_SHA_TAG]
#
# Deletes GitHub Draft releases and GHCR image versions whose tag matches
# VERSION-sha-* for a given milestone. Called in two modes:
#
#   sha build:   cleanup-sha-artifacts.sh 0.5.6 0.5.6-sha-abc1234
#                Deletes all 0.5.6-sha-* artifacts except the current one.
#
#   release tag: cleanup-sha-artifacts.sh 0.5.6
#                Deletes ALL 0.5.6-sha-* artifacts (milestone is closing).
#
# Requires: GH_TOKEN env var, gh CLI, python3

set -euo pipefail

VERSION="${1:?VERSION argument required}"
CURRENT_SHA_TAG="${2:-}"

OWNER="${OWNER:-thegrandwazoo}"
PACKAGE="${PACKAGE:-mock-idp}"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Cleaning up sha artifacts for v${VERSION} (current: ${CURRENT_SHA_TAG:-none})"

# ── Write helper scripts (quoted heredoc — no shell expansion inside) ──────

cat > "$TMPDIR/find_releases.py" << 'PYEOF'
import json, sys
releases_file, version, current = sys.argv[1], sys.argv[2], sys.argv[3]
prefix = f"v{version}-sha-"
for r in json.load(open(releases_file)):
    tag = r["tagName"]
    if r["isDraft"] and tag.startswith(prefix) and tag != (f"v{current}" if current else ""):
        print(tag)
PYEOF

cat > "$TMPDIR/find_versions.py" << 'PYEOF'
import json, sys
versions_file, version, current = sys.argv[1], sys.argv[2], sys.argv[3]
prefix = f"{version}-sha-"
for v in json.load(open(versions_file)):
    tags = v.get("metadata", {}).get("container", {}).get("tags", [])
    if any(t.startswith(prefix) and t != current for t in tags):
        print(v["id"], " ".join(tags))
PYEOF

# ── GitHub Draft Releases ──────────────────────────────────────────────────

echo "--> Fetching draft releases..."
gh release list --json tagName,isDraft --limit 100 > "$TMPDIR/releases.json"

STALE_RELEASES=$(python3 "$TMPDIR/find_releases.py" \
    "$TMPDIR/releases.json" "$VERSION" "$CURRENT_SHA_TAG")

if [ -z "$STALE_RELEASES" ]; then
    echo "    no stale draft releases found"
else
    while IFS= read -r tag; do
        echo "    deleting draft release: $tag"
        gh release delete "$tag" --yes \
            || echo "    warning: release delete failed for $tag, continuing"
        gh api -X DELETE "repos/${OWNER}/${PACKAGE}/git/refs/tags/${tag}" 2>/dev/null \
            && echo "    deleted git tag: $tag" \
            || echo "    git tag $tag already absent"
    done <<< "$STALE_RELEASES"
fi

# ── GHCR Image Versions ────────────────────────────────────────────────────

echo "--> Fetching GHCR image versions..."
gh api "/users/${OWNER}/packages/container/${PACKAGE}/versions?per_page=100" \
    > "$TMPDIR/versions.json"

STALE_VERSIONS=$(python3 "$TMPDIR/find_versions.py" \
    "$TMPDIR/versions.json" "$VERSION" "$CURRENT_SHA_TAG")

if [ -z "$STALE_VERSIONS" ]; then
    echo "    no stale GHCR image versions found"
else
    while IFS= read -r line; do
        vid="${line%% *}"
        tags="${line#* }"
        echo "    deleting GHCR image version $vid (tags: $tags)"
        gh api -X DELETE "/users/${OWNER}/packages/container/${PACKAGE}/versions/${vid}" \
            || echo "    warning: GHCR delete failed for version $vid, continuing"
    done <<< "$STALE_VERSIONS"
fi

echo "==> Cleanup complete."
