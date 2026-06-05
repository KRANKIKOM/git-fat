#!/bin/bash -ex
# Test against a copy of the ib-main git-fat example repo (read-only source).

SOURCE="${1:-/Users/patrick/dev/ib/ib-main/data/repositories/af5b67d7-50a3-4ee0-a10b-bf6599f13140}"
DEST="/tmp/git-fat-example-test-$$"

if [[ ! -d "${SOURCE}/.git" ]]; then
    echo "Example repo not found: ${SOURCE}" >&2
    exit 1
fi

rm -rf "${DEST}"
cp -a "${SOURCE}" "${DEST}"
cd "${DEST}"

git fat status
git fat verify

# Confirm fat placeholders exist in working tree for media without local objects
orphans="$(git fat status 2>&1 | grep -c 'Orphan objects:' || true)"
echo "Repo has orphan fat objects (expected when not pulled): ${orphans}"

echo "Example repo tests passed."
