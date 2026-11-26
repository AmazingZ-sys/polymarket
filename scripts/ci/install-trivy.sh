#!/usr/bin/env bash
set -euo pipefail
# Official aquasecurity/trivy immutable v0.76.2 Linux amd64 release asset.
version=0.76.2
digest=bc0fc726e017b8927a62b2d3531d7a84ee01769daf5c99ed866dced54089fdbf
dest=${RUNNER_TEMP:?}/pq-trivy
mkdir -p "$dest"
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location --retry 3 \
  "https://github.com/aquasecurity/trivy/releases/download/v$version/trivy_${version}_Linux-64bit.tar.gz" -o "$dest/archive.tgz"
(cd "$dest" && echo "$digest  archive.tgz" | sha256sum --check --status && tar xzf archive.tgz trivy)
"$dest/trivy" --version
echo "$dest" >> "${GITHUB_PATH:?}"
