#!/usr/bin/env python3
"""
Extract Admin API examples (`curl .../apisix/admin/... -d '{...}'`) from the APISIX docs.

Usage:
  python3 tools/docs_examples.py <apisix-version>

Writes build/examples/examples.json: one entry per PUT/POST example with a JSON body:
  {"n", "doc", "line", "resource", "id", "plugin", "body"}
"""

from __future__ import annotations

import glob
import json
import os
import re
import shlex
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESOURCES = {
    "routes", "services", "upstreams", "consumers", "ssls", "global_rules", "plugin_configs",
    "plugin_metadata", "consumer_groups", "protos", "stream_routes", "secrets",
}


def commands(text: str):
    """Yield (line number, command text) for every `curl` command, across line continuations."""
    for m in re.finditer(r"(?m)^[ \t]*curl\b", text):
        i, quote, start = m.start(), None, m.start()
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "\n":
                j = i - 1
                while j > start and text[j] in " \t":
                    j -= 1
                if text[j] != "\\":
                    break
            i += 1
        yield text.count("\n", 0, start) + 1, text[start:i]


def parse(cmd: str):
    try:
        tokens = shlex.split(cmd.replace("\\\n", " "), comments=False)
    except ValueError:
        return None
    method, url, data = "GET", None, None
    it = iter(range(len(tokens)))
    for i in it:
        t = tokens[i]
        if t in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1].upper()
        elif t in ("-d", "--data", "--data-raw", "--data-binary") and i + 1 < len(tokens):
            data = tokens[i + 1]
            if method == "GET":
                method = "POST"
        elif "/apisix/admin/" in t:
            url = t
    return method, url, data


def resource_of(url: str):
    path = url.split("/apisix/admin/", 1)[1].split("?", 1)[0].strip("/")
    parts = [p for p in path.split("/") if p]
    if not parts or parts[0] not in RESOURCES:
        return None
    kind, rest = parts[0], parts[1:]
    if kind == "consumers" and len(rest) >= 3 and rest[1] == "credentials":
        return "credentials", f"{rest[0]}/credentials/{rest[2]}", None
    if kind == "consumers" and len(rest) == 2 and rest[1] == "credentials":
        return "credentials", f"{rest[0]}/credentials/", None
    if kind == "plugin_metadata":
        return (kind, None, rest[0]) if rest else None
    if kind == "secrets":
        return (kind, f"{rest[0]}/{rest[1]}", rest[0]) if len(rest) >= 2 else None
    return kind, (rest[0] if rest else None), None


def main():
    version = sys.argv[1]
    docs = os.path.join(ROOT, ".cache", f"apisix-{version}", "docs", "en", "latest")
    examples, skipped = [], {"no_json": 0, "patch": 0, "not_a_resource": 0}
    for path in sorted(glob.glob(os.path.join(docs, "**", "*.md"), recursive=True)):
        text = open(path, encoding="utf-8").read()
        for line, cmd in commands(text):
            parsed = parse(cmd)
            if not parsed:
                skipped["no_json"] += 1
                continue
            method, url, data = parsed
            if not url or data is None or method not in ("PUT", "POST", "PATCH"):
                continue
            if method == "PATCH":
                skipped["patch"] += 1  # partial updates are not complete resources
                continue
            res = resource_of(url)
            if not res:
                skipped["not_a_resource"] += 1
                continue
            try:
                body = json.loads(data)
            except json.JSONDecodeError:
                skipped["no_json"] += 1
                continue
            if not isinstance(body, dict):
                skipped["no_json"] += 1
                continue
            kind, rid, plugin = res
            examples.append({
                "n": len(examples) + 1,
                "doc": os.path.relpath(path, docs),
                "line": line,
                "resource": kind,
                "id": rid,
                "plugin": plugin,
                "body": body,
            })
    out = os.path.join(ROOT, "build", "examples")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "examples.json"), "w") as f:
        json.dump(examples, f, indent=1)
    with open(os.path.join(out, "skipped.json"), "w") as f:
        json.dump(skipped, f)
    by_kind = {}
    for e in examples:
        by_kind[e["resource"]] = by_kind.get(e["resource"], 0) + 1
    print(f"{len(examples)} examples from {len({e['doc'] for e in examples})} docs: {by_kind}; skipped {skipped}")


if __name__ == "__main__":
    main()
