#!/usr/bin/env bash
# Load an apisix.yaml into a real APISIX (standalone, every plugin enabled) and fail on rejected entries.
# Usage: tools/apisix-check.sh <apisix-version> <apisix.yaml>
set -euo pipefail

VERSION="${1:?usage: $0 <apisix-version> <apisix.yaml>}"
FILE="$(cd "$(dirname "${2:?missing apisix.yaml}")" && pwd)/$(basename "$2")"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${ROOT}/.cache/check-${VERSION}"
NAME="pkl-apisix-check-$$"
mkdir -p "${WORK}"
"${ROOT}/tools/apisix-config.sh" "${VERSION}" > "${WORK}/config.yaml"

docker run -d --name "${NAME}" -p 127.0.0.1::9090 \
  -v "${WORK}/config.yaml:/usr/local/apisix/conf/config.yaml:ro" \
  -v "${FILE}:/usr/local/apisix/conf/apisix.yaml:ro" \
  "apache/apisix:${VERSION}-debian" >/dev/null
trap 'docker rm -f "${NAME}" >/dev/null 2>&1 || true' EXIT

sleep 2
PORT="$(docker port "${NAME}" 9090/tcp 2>/dev/null | head -1 | sed 's/.*://')"
[ -n "${PORT}" ] || { docker logs "${NAME}" 2>&1 | grep -E '\[(error|emerg)\]' | head -20; exit 1; }
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:${PORT}/v1/healthcheck" >/dev/null 2>&1 && break
  sleep 1
done
sleep 3  # let every worker load the file

# Shared sections (services, upstreams, ssls, ...) are also loaded by the stream subsystem, which
# only knows stream plugins and the base SSL schema; its complaints about HTTP-only settings are noise.
# Stream errors still count for stream_routes.
ERRORS="$(docker logs "${NAME}" 2>&1 \
  | grep -E 'failed to check item data|invalid item data|failed to parse|failed to fetch data|\[emerg\]' \
  | grep -v -E ' stream \[lua\].*key: /(services|upstreams|ssls|plugin_metadata|secrets|plugins)/' \
  | sed -E 's/^.*(failed to check|invalid item|failed to parse|failed to fetch|\[emerg\])/\1/' \
  | sed -E 's/, context: .*$//' | sort -u || true)"
if [ -n "${ERRORS}" ]; then
  echo "APISIX ${VERSION} rejected entries of ${2}:"
  echo "${ERRORS}"
  exit 1
fi
echo "APISIX ${VERSION} accepted ${2}"
