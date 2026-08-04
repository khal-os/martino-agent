#!/usr/bin/env bash
# Bumps the agent version in src/agent_app/_version.py — the single source
# everything reads (package, pyproject/hatch, /health, trace Resource).
# After bumping, push the new version to the platform with
# scripts/khal_register_agent.sh (the Agent Catalog is the source of truth
# for the deployed app version).
#
# Usage: scripts/bump_version.sh <major|minor|patch>
set -euo pipefail

PART="${1:?usage: bump_version.sh <major|minor|patch>}"
[[ "$PART" =~ ^(major|minor|patch)$ ]] \
  || { echo "ERROR: '$PART' — expected major, minor or patch"; exit 1; }

FILE="$(cd "$(dirname "$0")/.." && pwd)/src/agent_app/_version.py"

OLD=$(python3 -c "import re,sys;print(re.search(r'__version__ = \"([^\"]+)\"', open(sys.argv[1]).read()).group(1))" "$FILE")
NEW=$(python3 -c "
import sys
major, minor, patch = (int(x) for x in sys.argv[1].split('.'))
part = sys.argv[2]
if part == 'major': major, minor, patch = major + 1, 0, 0
elif part == 'minor': minor, patch = minor + 1, 0
else: patch += 1
print(f'{major}.{minor}.{patch}')
" "$OLD" "$PART")

python3 -c "
import re, sys
path, new = sys.argv[1], sys.argv[2]
src = open(path).read()
open(path, 'w').write(re.sub(r'__version__ = \"[^\"]+\"', f'__version__ = \"{new}\"', src))
" "$FILE" "$NEW"

echo "agent version: $OLD → $NEW ($FILE)"
echo "Next: re-register so the platform sees it — scripts/khal_register_agent.sh"
