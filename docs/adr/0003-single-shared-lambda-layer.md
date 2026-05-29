# ADR-0003: Single Shared Lambda Layer

## Status

Accepted

## Context

Eight Lambda functions share common code: arsha_client, db, models,
repositories, pricing, analytics, and config modules. Options considered were a
shared Lambda Layer, container images per function, or duplicated code across
function directories.

## Decision

A single shared Lambda Layer named `bdo-common` located at
`src/layer/python/bdo_common/`. All functions declare this layer as a
dependency in `template.yaml`.

## Consequences

- (+) One deployment artifact for shared code; consistent versions across all
  functions.
- (+) Clear dependency boundary between shared library and function handlers.
- (+) Simpler dependency management with a single dependency manifest
  (`pyproject.toml`); the layer build exports it to a `requirements.txt`
  consumed by `sam build`.
- (-) Any layer change triggers redeployment of all 8 functions.
- (-) Layer size limit of 250 MB unzipped must be respected.
