#!/usr/bin/env bash
# cleanup-sha-artifacts.sh VERSION [CURRENT_SHA_TAG]
#
# Deletes GitHub Draft releases and GHCR image versions whose tag matches
# VERSION-sha-* for a given milestone. Called in two modes:
#
#   sha build:  cleanup-sha-artifacts.sh 0.5.6 0.5.6-sha-abc1234
#               Deletes all 0.5.6-sha-* artifacts except the current one.
#
#   release tag: cleanup-sha-artifacts.sh 0.5.6
#               Deletes ALL 0.5.6-sha-* artifacts (milestone is closing).
#
# Requires: GH_TOKEN env var, gh CLI, python3

set -euo pipefail

VERSION="${1:?VERSION argument required}"
CURRENT_SHA_TAG="${2:-}"   # empty on release tag builds

OWNER="${OWNER:-thegrandwazoo}"
PACKAGE="${PACKAGE:-mock-idp}"

echo "==> Cleaning up sha artifacts for v${VERSION} (current: ${CURRENT_SHA_TAG:-none})"

# ── GitHub Draft Releases ──────────────────────────────────────────────────

echo "--> Fetching draft releases..."
RELEASES=$(gh release list --json tagName,isDraft --limit 100)

echo "$RELEASES" | python3 - << PYEOF
import sys, json, subprocess, os

data = json.loads("""${RELEASES}""")
version   = "${VERSION}"
current   = "${CURRENT_SHA_TAG}"
prefix    = f"v{version}-sha-"

to_delete = [
    r["tagName"] for r in data
    if r["isDraft"]
    and r["tagName"].startswith(prefix)
    and r["tagName"] != (f"v{current}" if current else "")
]

if not to_delete:
    print("    no stale draft releases found")
    sys.exit(0)

for tag in to_delete:
    print(f"    deleting draft release: {tag}")
    subprocess.run(
        ["gh", "release", "delete", tag, "--cleanup-tag", "--yes"],
        check=True
    )
PYEOF

# ── GHCR Image Versions ────────────────────────────────────────────────────

echo "--> Fetching GHCR image versions..."
VERSIONS=$(gh api "/users/${OWNER}/packages/container/${PACKAGE}/versions?per_page=100")

echo "$VERSIONS" | python3 - << PYEOF
import sys, json, subprocess

data    = json.loads("""${VERSIONS}""")
version = "${VERSION}"
current = "${CURRENT_SHA_TAG}"
prefix  = f"{version}-sha-"

to_delete = []
for v in data:
    tags = v.get("metadata", {}).get("container", {}).get("tags", [])
    if any(t.startswith(prefix) and t != current for t in tags):
        to_delete.append((v["id"], tags))

if not to_delete:
    print("    no stale GHCR image versions found")
    sys.exit(0)

owner   = "${OWNER}"
package = "${PACKAGE}"
for vid, tags in to_delete:
    print(f"    deleting GHCR image {vid} (tags: {tags})")
    subprocess.run(
        ["gh", "api", "-X", "DELETE",
         f"/users/{owner}/packages/container/{package}/versions/{vid}"],
        check=True
    )
PYEOF

echo "==> Cleanup complete."
