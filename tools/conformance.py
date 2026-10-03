#!/usr/bin/env python3
"""
Differential conformance test: APISIX docs examples vs. the Pkl types vs. a real APISIX.

Usage:
  python3 tools/conformance.py <apisix-version>

Needs build/examples/examples.json (tools/docs_examples.py) and build/typeinfo.json (tools/generate.py).

For every example:
  1. Pkl:    emit it as Pkl source that amends src/Standalone.pkl and evaluate it.
  2. APISIX: load all raw examples into APISIX (standalone, every plugin enabled) and collect
             the entries it rejects.
  3. Compare. "APISIX accepts, Pkl rejects" is a gap in the Pkl types and fails the run.
     For examples both accept, the YAML rendered by Pkl must equal the raw example.

Writes CONFORMANCE.md and build/examples/pkl/ex<N>.pkl (+ .yaml / .err).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from generate import ident, pkl_literal  # noqa: E402

OUT = os.path.join(ROOT, "build", "examples")
PKL_DIR = os.path.join(OUT, "pkl")

CLASS_OF = {
    "routes": "Apisix.pkl#Route",
    "services": "Apisix.pkl#Service",
    "upstreams": "Apisix.pkl#Upstream",
    "consumers": "Apisix.pkl#Consumer",
    "credentials": "Apisix.pkl#Credential",
    "ssls": "Apisix.pkl#Ssl",
    "global_rules": "Apisix.pkl#GlobalRule",
    "plugin_configs": "Apisix.pkl#PluginConfig",
    "consumer_groups": "Apisix.pkl#ConsumerGroup",
    "protos": "Apisix.pkl#Proto",
    "stream_routes": "Apisix.pkl#StreamRoute",
}
SECTION = {"credentials": "consumers"}


# ---------------------------------------------------------------- raw items


def raw_item(ex: dict) -> tuple[str, dict]:
    """The example as an apisix.yaml entry, with an id that is unique across all examples."""
    kind, body, n = ex["resource"], dict(ex["body"]), ex["n"]
    if kind == "plugin_metadata":
        body.pop("id", None)
        return "plugin_metadata", {"id": ex["plugin"], **body}
    if kind == "consumers":
        body.pop("id", None)
        body["username"] = f"ex{n}"
        return "consumers", body
    if kind == "credentials":
        user = (ex["id"] or "x/credentials/").split("/", 1)[0]
        body["id"] = f"{user}/credentials/ex{n}"
        return "consumers", body
    if kind == "secrets":
        body["id"] = f"{ex['plugin']}/ex{n}"
        return "secrets", body
    body["id"] = f"ex{n}"
    return kind, body


# ---------------------------------------------------------------- Pkl emitter


class Emitter:
    def __init__(self, typeinfo: dict):
        self.typeinfo = typeinfo
        self.imports: dict[str, str] = {}

    def alias(self, rel: str) -> str:
        a = "m_" + re.sub(r"[^A-Za-z0-9_]", "_", rel.removesuffix(".pkl"))
        self.imports[a] = rel
        return a

    def cls(self, ref: str) -> str:
        rel, name = ref.split("#")
        return f"{self.alias(rel)}.{name}"

    def value(self, v, info: dict | None, ind: str) -> str:
        info = info or {"k": "any"}
        k = info["k"]
        if k == "union":
            info = self.pick(v, info["of"])
            k = info["k"]
        if isinstance(v, dict):
            if k == "class":
                return self.obj(v, info["ref"], ind)
            elem = info.get("of") if k == "mapping" else None
            return self.mapping(v, elem, ind)
        if isinstance(v, list):
            elem = info.get("of") if k == "listing" else None
            inner = ind + "  "
            items = "".join(f"\n{inner}{self.value(e, elem, inner)}" for e in v)
            return f"new Listing {{{items}\n{ind}}}" if v else "new Listing {}"
        lit = pkl_literal(v)
        return lit if lit is not None else "null"

    def pick(self, v, options: list[dict]) -> dict:
        if isinstance(v, dict):
            classes = [o for o in options if o["k"] == "class"]
            for o in classes:
                props = self.typeinfo.get(o["ref"], {})
                if set(v) <= set(props):
                    return o
            for o in options:
                if o["k"] == "mapping":
                    return o
            return classes[0] if classes else {"k": "any"}
        if isinstance(v, list):
            return next((o for o in options if o["k"] == "listing"), {"k": "any"})
        return next((o for o in options if o["k"] in ("scalar", "any")), {"k": "any"})

    def obj(self, v: dict, ref: str, ind: str, extra: str = "") -> str:
        props = self.typeinfo.get(ref, {})
        inner = ind + "  "
        lines = [f"{inner}{extra}"] if extra else []
        extra = {k: x for k, x in v.items() if props.get("$open") and k not in props}
        for key, val in v.items():
            if key in extra:
                continue
            lines.append(f"{inner}{ident(key)} = {self.value(val, props.get(key), inner)}")
        if extra:
            lines.append(f"{inner}extra = {self.mapping(extra, None, inner)}")
        body = "\n".join(lines)
        return f"new {self.cls(ref)} {{\n{body}\n{ind}}}" if lines else f"new {self.cls(ref)} {{}}"

    def mapping(self, v: dict, elem, ind: str) -> str:
        inner = ind + "  "
        items = "".join(f"\n{inner}[{pkl_literal(str(key))}] = {self.value(val, elem, inner)}"
                        for key, val in v.items())
        return f"new Mapping {{{items}\n{ind}}}" if v else "new Mapping {}"


def emit(ex: dict, section: str, item: dict, typeinfo: dict) -> str:
    e = Emitter(typeinfo)
    kind = ex["resource"]
    if kind == "plugin_metadata":
        plugins_ref = "Plugins.pkl#PluginMetadata"
        conf = {k: v for k, v in item.items() if k != "id"}
        field_info = typeinfo.get(plugins_ref, {}).get(ex["plugin"])
        body = (f"plugin_metadata = new {e.cls(plugins_ref)} {{\n"
                f"  {ident(ex['plugin'])} = {e.value(conf, field_info, '  ')}\n}}")
    else:
        if kind == "secrets":
            ref = f"Apisix.pkl#{ex['plugin'].capitalize()}Secret"
        else:
            ref = CLASS_OF[kind]
        body = f"{section} {{\n  {e.obj(item, ref, '  ')}\n}}"
    imports = "\n".join(f'import "../../../src/{rel}" as {a}' for a, rel in sorted(e.imports.items()))
    return (f"// {ex['doc']}:{ex['line']}\n"
            f'amends "../../../src/Standalone.pkl"\n\n{imports}\n\n'
            f"checkReferences = false\n\n{body}\n")


def pkl_eval(n: int) -> tuple[int, bool, str]:
    src = os.path.join(PKL_DIR, f"ex{n}.pkl")
    res = subprocess.run(["pkl", "eval", src], capture_output=True, text=True)
    if res.returncode == 0:
        open(os.path.join(PKL_DIR, f"ex{n}.yaml"), "w").write(res.stdout)
        return n, True, ""
    open(os.path.join(PKL_DIR, f"ex{n}.err"), "w").write(res.stderr)
    lines = [l for l in res.stderr.splitlines() if l.strip() and not l.startswith("––")]
    return n, False, " ".join(lines[:2])[:300]


# ---------------------------------------------------------------- APISIX


def apisix_rejections(version: str, batches: list[dict]) -> dict[tuple[str, str], str]:
    """Load each batch (an apisix.yaml dict) into APISIX; return {(section, key): error}."""
    work = os.path.join(ROOT, ".cache", f"check-{version}")
    os.makedirs(work, exist_ok=True)
    conf = subprocess.run([os.path.join(ROOT, "tools", "apisix-config.sh"), version],
                          capture_output=True, text=True, check=True).stdout
    open(os.path.join(work, "config.yaml"), "w").write(conf)
    rejected = {}
    for i, doc in enumerate(batches):
        path = os.path.join(work, f"examples-{i}.yaml")
        with open(path, "w") as f:
            yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)
            f.write("#END\n")
        name = f"pkl-apisix-conformance-{os.getpid()}-{i}"
        subprocess.run(["docker", "run", "-d", "--name", name,
                        "-v", f"{work}/config.yaml:/usr/local/apisix/conf/config.yaml:ro",
                        "-v", f"{path}:/usr/local/apisix/conf/apisix.yaml:ro",
                        f"apache/apisix:{version}-debian"], check=True, capture_output=True)
        try:
            logs = ""
            for _ in range(60):
                time.sleep(1)
                logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True).stderr
                if "reloaded" in logs and "init_worker" in logs:
                    break
            time.sleep(4)
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True).stderr
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        for line in logs.splitlines():
            if "failed to check" not in line and "invalid item data" not in line:
                continue
            m = re.search(r"key: /([a-z_]+)/(\S+?)(?:,|$)", line)
            if not m:
                continue
            if " stream [lua]" in line and m.group(1) != "stream_routes":
                # Shared sections (services, upstreams, ...) are also loaded by the stream
                # subsystem, which only knows stream plugins; HTTP-only plugins are "unknown" there.
                continue
            msg = re.sub(r"^.*?(failed to check|invalid item)", r"\1", line)
            msg = re.sub(r", (context|client|server|key): .*$", "", msg)
            rejected.setdefault((m.group(1), m.group(2)), msg[:300])
    return rejected


def item_key(section: str, item: dict) -> str:
    return str(item.get("id") or item.get("username"))


# ---------------------------------------------------------------- compare


def normalize(v):
    if isinstance(v, dict):
        return {k: normalize(x) for k, x in v.items()}
    if isinstance(v, list):
        return [normalize(x) for x in v]
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def main():
    version = sys.argv[1]
    examples = json.load(open(os.path.join(OUT, "examples.json")))
    typeinfo = json.load(open(os.path.join(ROOT, "build", "typeinfo.json")))
    os.makedirs(PKL_DIR, exist_ok=True)
    for f in os.listdir(PKL_DIR):
        os.remove(os.path.join(PKL_DIR, f))

    items = {}
    for ex in examples:
        section, item = raw_item(ex)
        items[ex["n"]] = (section, item)
        open(os.path.join(PKL_DIR, f"ex{ex['n']}.pkl"), "w").write(emit(ex, section, item, typeinfo))

    print(f"evaluating {len(examples)} examples with Pkl ...", flush=True)
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        pkl = {n: (ok, err) for n, ok, err in pool.map(pkl_eval, [e["n"] for e in examples])}

    # Batches: plugin_metadata is keyed by plugin name, so repeated plugins go to later batches.
    batches: list[dict] = [defaultdict(list)]
    seen_meta = Counter()
    for ex in examples:
        section, item = items[ex["n"]]
        b = 0
        if section == "plugin_metadata":
            b = seen_meta[item["id"]]
            seen_meta[item["id"]] += 1
        while len(batches) <= b:
            batches.append(defaultdict(list))
        batches[b][section].append(item)
    print(f"loading examples into APISIX {version} ({len(batches)} pass(es)) ...", flush=True)
    rejected = apisix_rejections(version, [dict(b) for b in batches])
    batch_of = {}
    for ex in examples:
        section, item = items[ex["n"]]
        batch_of[ex["n"]] = (section, item_key(section, item))

    rows = defaultdict(list)
    for ex in examples:
        n = ex["n"]
        ok_pkl, err_pkl = pkl[n]
        section, key = batch_of[n]
        err_apisix = rejected.get((section, key))
        if section == "plugin_metadata":
            # Same plugin in several passes: attribute by matching message is not possible; keep per key.
            pass
        where = f"{ex['doc']}:{ex['line']}"
        if ok_pkl and not err_apisix:
            out = yaml.safe_load(open(os.path.join(PKL_DIR, f"ex{n}.yaml")))
            got = next((i for i in out.get(section, []) if item_key(section, i) == key), None)
            if normalize(got) == normalize(items[n][1]):
                rows["agree_accept"].append((n, where, ""))
            else:
                rows["roundtrip"].append((n, where, f"rendered {json.dumps(got)[:200]}"))
        elif not ok_pkl and err_apisix:
            rows["agree_reject"].append((n, where, f"APISIX: {err_apisix}"))
        elif not ok_pkl:
            rows["gap"].append((n, where, err_pkl))
        else:
            rows["looser"].append((n, where, f"APISIX: {err_apisix}"))

    write_report(version, examples, rows)
    summary = {k: len(v) for k, v in rows.items()}
    print(summary)
    sys.exit(1 if rows["gap"] or rows["roundtrip"] else 0)


def write_report(version: str, examples: list, rows: dict):
    total = len(examples)
    lines = [
        f"# Conformance with APISIX {version}",
        "",
        "Generated by `tools/conformance.py`. Do not edit.",
        "",
        f"Every Admin API example (`curl .../apisix/admin/... -d '{{...}}'`, PUT/POST) of the APISIX {version}",
        "docs is checked twice: type-checked by Pkl and loaded into a real APISIX (standalone mode, every",
        "plugin enabled). For examples both accept, the YAML rendered by Pkl must equal the example.",
        "",
        "| Result | Examples |",
        "|---|---|",
        f"| Accepted by both, Pkl output identical | {len(rows['agree_accept'])} |",
        f"| Rejected by both (invalid example in the docs) | {len(rows['agree_reject'])} |",
        f"| Accepted by Pkl, rejected by APISIX (rule Pkl does not check) | {len(rows['looser'])} |",
        f"| **Accepted by APISIX, rejected by Pkl (gap in the types)** | **{len(rows['gap'])}** |",
        f"| **Accepted by both, Pkl output differs** | **{len(rows['roundtrip'])}** |",
        f"| Total | {total} |",
        "",
    ]
    skipped_file = os.path.join(OUT, "skipped.json")
    if os.path.exists(skipped_file):
        sk = json.load(open(skipped_file))
        lines += [
            "Not included: PATCH examples (partial updates, not complete resources): "
            f"{sk.get('patch', 0)}; bodies that are not plain JSON (shell substitution such as "
            f"`$(cat cert.pem)`): {sk.get('no_json', 0)}; non-resource endpoints: {sk.get('not_a_resource', 0)}.",
            "",
        ]
    titles = {
        "gap": "Accepted by APISIX, rejected by Pkl",
        "roundtrip": "Pkl output differs from the example",
        "looser": "Accepted by Pkl, rejected by APISIX",
        "agree_reject": "Rejected by both",
    }
    for key, title in titles.items():
        lines += [f"## {title}", ""]
        if not rows[key]:
            lines += ["None.", ""]
            continue
        lines += [f"- `ex{n}` {where}: {msg}".rstrip(": ") for n, where, msg in rows[key]] + [""]
    open(os.path.join(ROOT, "CONFORMANCE.md"), "w").write("\n".join(lines))


if __name__ == "__main__":
    main()
