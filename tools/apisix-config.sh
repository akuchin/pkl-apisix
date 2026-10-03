#!/usr/bin/env bash
# Print a standalone config.yaml that enables every HTTP and stream plugin of an APISIX version.
# Usage: tools/apisix-config.sh <apisix-version>
set -euo pipefail

VERSION="${1:?usage: $0 <apisix-version>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/.cache/apisix-${VERSION}"
WORK="${ROOT}/.cache/fetch-${VERSION}"
EXCLUDE="${ROOT}/tools/schema-exclude.txt"

[ -d "${SRC}" ] || git clone -q --depth 1 --branch "${VERSION}" https://github.com/apache/apisix.git "${SRC}"
mkdir -p "${WORK}"
python3 "${ROOT}/tools/list_plugins.py" "${SRC}" > "${WORK}/plugins.txt"

cat <<YAML
deployment:
  role: data_plane
  role_data_plane:
    config_provider: yaml
apisix:
  enable_admin: false
  proxy_mode: http&stream
  stream_proxy:
    tcp:
      - 9100
  control:
    ip: 0.0.0.0
    port: 9090
YAML
echo "plugins:"
grep -v -x -F -f <(grep -v '^#' "${EXCLUDE}" | cut -d' ' -f1) "${WORK}/plugins.txt" | sed 's/^/  - /'
echo "stream_plugins:"
sed 's/^/  - /' "${WORK}/stream-plugins.txt"
