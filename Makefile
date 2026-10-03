APISIX_VERSION ?= 3.19.0

.PHONY: schema generate test test-fast package

## Fetch the schema snapshot from a real APISIX (needs Docker).
schema:
	tools/fetch-schema.sh $(APISIX_VERSION)

## Regenerate src/ and COVERAGE.md from the snapshot.
generate:
	python3 tools/generate.py schema/apisix-$(APISIX_VERSION).json

## All checks, including APISIX in Docker and the docs conformance run.
test:
	tools/test.sh

## Pkl-only checks (no Docker).
test-fast:
	tools/test.sh --no-docker

## Build the Pkl package into build/package.
package:
	pkl project package src --output-path build/package
