# pkl-apisix

Typed [Pkl](https://pkl-lang.org) modules for [Apache APISIX](https://apisix.apache.org) configuration.
Write your APISIX configuration in Pkl, get type errors at build time, and render the YAML that APISIX reads.

The types come from the schema of a real APISIX instance, not from hand-written definitions.
This version covers APISIX 3.19.0.

## What you get

- All 14 resource kinds of the standalone `apisix.yaml`: routes, services, upstreams, consumers, credentials,
  consumer groups, SSLs (Secure Sockets Layer certificates), global rules, plugin configs, plugin metadata,
  protos, stream routes, secrets (Vault, AWS, GCP) and the plugin list.
- All 122 HTTP plugins and all 6 stream plugins, with consumer and metadata schemas.
- Checks at build time:
  - value types, enums, ranges, string lengths and regex patterns;
  - list sizes and unique items;
  - all 55 cross-field rules of the schema (for example "set `uri` or `uris`, not both");
  - misspelled property names;
  - references (`upstream_id`, `service_id`, `plugin_config_id`, `group_id`) to resources in the same file.
- APISIX defaults in the doc comments. Unset properties are not rendered, so APISIX applies its own defaults.

## Example

```pkl
amends "package://github.com/akuchin/pkl-apisix/releases/download/apisix@0.1.0/apisix@0.1.0#/Standalone.pkl"

upstreams {
  new { id = "httpbin"; nodes = new Mapping { ["httpbin.org:80"] = 1 } }
}

routes {
  new {
    id = "get"
    uri = "/get"
    upstream_id = "httpbin"
    plugins {
      `proxy-rewrite` { uri = "/anything"; headers { set { ["X-Api-Version"] = "1" } } }
      `limit-count` { count = 100; time_window = 60 }
    }
  }
}
```

Run `pkl eval routes.pkl` to get `apisix.yaml`, including the `#END` marker.

For more examples, see [tests/cases](tests/cases).

## Use

### Objects that accept extra keys

Some APISIX objects accept keys that their schema does not name.
An example is the `options` object of the AI plugins.
These classes extend `Base.OpenObject`.
Put the extra keys in `extra`:

```pkl
`ai-proxy` {
  provider = "openai"
  options {
    model = "gpt-4o"
    extra { ["max_tokens"] = 512 }
  }
}
```

A misspelled named property is still an error.
`Standalone.pkl` merges `extra` into the output.
If you render with your own renderer, add `Base.converters` to it.

### Union types

Some properties accept more than one form.
For example, `upstream.nodes` is a list of nodes or a map of `"host:port"` to weight.
The amend syntax (`nodes { ... }`) creates the default form, here the list.
For the other form, write the type: `nodes = new Mapping { ["host:80"] = 1 }`.

### Custom plugins

Extend `Plugins.Plugins` to add types for your own plugins:

```pkl
import "package://.../apisix@0.1.0#/Plugins.pkl"

class MyPlugins extends Plugins.Plugins {
  `header-guard`: HeaderGuard?
}
```

Then set `plugins = new MyPlugins { ... }` on a route.
See [tests/pkl-only/custom-plugin.pkl](tests/pkl-only/custom-plugin.pkl).

### References to resources in other files

`Standalone.pkl` checks that each referenced id is defined in the same file.
If the referenced resources are in a different file, set `checkReferences = false`.

## How the package is made and tested

1. `tools/fetch-schema.sh` starts APISIX in Docker with every plugin enabled.
   It saves the schema from the Control API (`GET /v1/schema`) to `schema/apisix-<version>.json`.
   Some schemas are not in that response: credential, the secret managers and the `gm` plugin.
   The script adds them from the APISIX source.
2. `tools/generate.py` converts the JSON schema to Pkl modules in `src/`.
   It also writes [COVERAGE.md](COVERAGE.md).
3. `tools/test.sh` runs these checks:
   - every regex of the schema compiles in Pkl;
   - Pkl evaluates the cases in `tests/cases`, and APISIX accepts the output;
   - Pkl rejects the cases in `tests/negative` with the expected message;
   - the docs conformance test.

The docs conformance test takes every Admin API example from the APISIX docs: 627 examples from 156 pages.
It type-checks each example with Pkl and loads all examples into a real APISIX.
Then it compares the two results.
For 608 examples, both accept and the Pkl output is identical to the example.
For 0 examples, APISIX accepts and Pkl rejects.
See [CONFORMANCE.md](CONFORMANCE.md) for the full result.

### Limits

- Pkl does not check custom Lua checks in plugin code.
  An example is "`session.secret` is necessary if `bearer_only` is false" in `openid-connect`.
  APISIX checks them when it loads the file.
- Some regex patterns only name keys, for example `aws` or `header`.
  These are typed properties, so a key that only contains the name is not accepted.
- `route.vars` is `Listing<Any>`, because its schema is an untyped array.

## Develop

You need Pkl 0.32 or later, Python 3 with PyYAML, and Docker.

| Command | Action |
|---|---|
| `make schema APISIX_VERSION=3.19.0` | Fetch the schema snapshot from APISIX |
| `make generate` | Generate `src/` and `COVERAGE.md` from the snapshot |
| `make test` | Run all checks, including APISIX in Docker |
| `make test-fast` | Run the Pkl checks only |
| `make package` | Build the Pkl package in `build/package` |

To support a new APISIX version:

1. Run `make schema APISIX_VERSION=<version>`.
2. Run `make generate APISIX_VERSION=<version>`.
3. Run `make test`.
4. Examine the changes in `src/`, `COVERAGE.md` and `CONFORMANCE.md`.

Do not edit the generated files in `src/`.
`Standalone.pkl`, `Base.pkl` and `PklProject` are hand-written.

## License

Apache License 2.0. The schema snapshot comes from Apache APISIX, which also uses the Apache License 2.0.
