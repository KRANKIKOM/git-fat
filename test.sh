#!/bin/bash -ex
# Build the Docker image and run all tests inside it.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${GIT_FAT_IMAGE:-git-fat:latest}"

docker build -t "${IMAGE}" "${SCRIPT_DIR}"

docker run --rm \
    --entrypoint bash \
    -v "${SCRIPT_DIR}/test-in-container.sh:/test-in-container.sh:ro" \
    -v "${SCRIPT_DIR}/test-example-repo.sh:/test-example-repo.sh:ro" \
    -v "/Users/patrick/dev/ib/ib-main/data/repositories/af5b67d7-50a3-4ee0-a10b-bf6599f13140:/example-repo:ro" \
    "${IMAGE}" \
    -c '
        set -e
        bash -ex /test-in-container.sh
        bash -ex /test-example-repo.sh /example-repo
    '

echo "All Docker tests passed."
