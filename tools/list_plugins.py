#!/usr/bin/env python3
"""Print every HTTP plugin name in an APISIX source tree; write stream plugins next to the output.

Plugins = the default list in apisix/cli/config.lua, plus every top-level `<name>.lua` module
that is off by default but declares a `priority`, a `name` and a `schema`.
Directories (e.g. serverless/, limit-count/) hold shared helpers and are only plugins when
they are in the default list (proxy-cache).
"""
import os
import re
import sys

src = sys.argv[1]
config = open(os.path.join(src, "apisix", "cli", "config.lua"), encoding="utf-8").read()


def default_list(key: str) -> list[str]:
    m = re.search(r"\n  " + key + r" = \{(.*?)\}", config, re.S)
    # Drop commented-out entries.
    body = "\n".join(l for l in m.group(1).splitlines() if not l.strip().startswith("--"))
    return re.findall(r'"([^"]+)"', body)


def extra_modules(base: str, known: list[str]) -> list[str]:
    extra = []
    for entry in sorted(os.listdir(base)):
        if not entry.endswith(".lua") or entry[:-4] in known:
            continue
        text = open(os.path.join(base, entry), encoding="utf-8").read()
        if all(re.search(p, text) for p in (r"\bpriority\s*=", r"\bname\s*=", r"\bschema\s*=")):
            extra.append(entry[:-4])
    return extra


http = default_list("plugins")
stream = default_list("stream_plugins")
http += extra_modules(os.path.join(src, "apisix", "plugins"), http)
stream += extra_modules(os.path.join(src, "apisix", "stream", "plugins"), stream)

print("\n".join(http))
work = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "..", ".cache",
                    "fetch-" + os.path.basename(src).removeprefix("apisix-"))
os.makedirs(work, exist_ok=True)
with open(os.path.join(work, "stream-plugins.txt"), "w") as f:
    f.write("\n".join(stream) + "\n")
