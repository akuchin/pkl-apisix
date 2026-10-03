#!/usr/bin/env bash
# Start APISIX in standalone mode with every plugin enabled and save GET /v1/schema.
# Usage: tools/fetch-schema.sh <apisix-version>     (e.g. 3.19.0)
set -euo pipefail

VERSION="${1:?usage: $0 <apisix-version>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/.cache/apisix-${VERSION}"
WORK="${ROOT}/.cache/fetch-${VERSION}"
OUT="${ROOT}/schema/apisix-${VERSION}.json"
NAME="pkl-apisix-schema-${VERSION}"

[ -d "${SRC}" ] || git clone -q --depth 1 --branch "${VERSION}" https://github.com/apache/apisix.git "${SRC}"
mkdir -p "${WORK}" "$(dirname "${OUT}")"

"${ROOT}/tools/apisix-config.sh" "${VERSION}" > "${WORK}/config.yaml"
printf 'routes: []\n#END\n' > "${WORK}/apisix.yaml"

docker rm -f "${NAME}" >/dev/null 2>&1 || true
docker run -d --name "${NAME}" -p 127.0.0.1::9090 \
  -v "${WORK}/config.yaml:/usr/local/apisix/conf/config.yaml:ro" \
  -v "${WORK}/apisix.yaml:/usr/local/apisix/conf/apisix.yaml:ro" \
  "apache/apisix:${VERSION}-debian" >/dev/null
trap 'docker rm -f "${NAME}" >/dev/null 2>&1 || true' EXIT

sleep 2
PORT="$(docker port "${NAME}" 9090/tcp 2>/dev/null | head -1 | sed 's/.*://')"
[ -n "${PORT}" ] || { docker logs "${NAME}" 2>&1 | grep -E '\[(error|emerg)\]' | head -10; exit 1; }
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/v1/schema" -o "${OUT}.tmp" 2>/dev/null; then break; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "${NAME}" 2>/dev/null)" != "true" ]; then
    docker logs "${NAME}" 2>&1 | tail -30; exit 1
  fi
  sleep 1
done
[ -s "${OUT}.tmp" ] || { docker logs "${NAME}" 2>&1 | tail -30; exit 1; }

# Schemas that /v1/schema does not return: credential, secret managers, resource list.
docker run --rm --entrypoint /usr/local/openresty/bin/resty \
  -v "${ROOT}/tools/dump_extra_schemas.lua:/tmp/dump.lua:ro" "apache/apisix:${VERSION}-debian" \
  -I /usr/local/apisix -I /usr/local/apisix/deps/share/lua/5.1 /tmp/dump.lua 2>/dev/null \
  | tail -1 > "${WORK}/extra.json"
python3 - "${OUT}.tmp" "${WORK}/extra.json" <<'PY'
import json, sys
schema, extra = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
schema["main"]["credential"] = extra["credential"]
schema["secrets"] = extra["secrets"]
schema["resources"] = sorted(set(extra["resources"]))
json.dump(schema, open(sys.argv[1], "w"))
PY

# Merge schemas of plugins that cannot start here (see tools/schema-exclude.txt).
python3 - "${OUT}.tmp" "${ROOT}/tools/static-schemas" <<'PY'
import json, os, sys
path, static_dir = sys.argv[1], sys.argv[2]
schema = json.load(open(path))
for file in sorted(os.listdir(static_dir)):
    extra = json.load(open(os.path.join(static_dir, file)))
    schema["plugins"][file.removesuffix(".json")] = extra["plugin"]
    schema["main"]["ssl"]["properties"].update(extra.get("ssl_properties", {}))
json.dump(schema, open(path, "w"))
PY
python3 -m json.tool --sort-keys "${OUT}.tmp" > "${OUT}" && rm "${OUT}.tmp"
if docker logs "${NAME}" 2>&1 | grep -E '\[error\]' | head -5 | grep .; then
  echo "APISIX logged errors, see above"; exit 1
fi
python3 - "${OUT}" "${WORK}/plugins.txt" "${ROOT}/tools/schema-exclude.txt" <<'PY'
import json, sys
schema = json.load(open(sys.argv[1]))
wanted = [l.strip() for l in open(sys.argv[2]) if l.strip()]
excluded = {l.split()[0] for l in open(sys.argv[3]) if l.strip() and not l.startswith("#")}
got = set(schema.get("plugins", {}))
missing = [p for p in wanted if p not in got and p not in excluded]
print(f"saved {sys.argv[1]}: {len(got)} http plugins, {len(schema.get('stream-plugins', schema.get('stream_plugins', {})))} stream plugins, "
      f"main objects: {len(schema['main'])}, secret managers: {', '.join(schema['secrets'])}, "
      f"resources: {', '.join(schema['resources'])}")
if missing:
    print("NOT LOADED:", ", ".join(missing)); sys.exit(1)
PY
