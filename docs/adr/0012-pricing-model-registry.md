# ADR-0012: Pricing-Model Registry

## Status

Accepted

## Context

BDO enhancement economics differ by item family. The v1 scope ships a single
market-input, probability-weighted accessory model (Model A1, `accessory_v1`),
but future work needs a cron-based Markov model (`accessory_cron_v1`) and
potentially others, without rewriting every caller. The reference data
(probability curves, cron counts, tax constants) must be swappable per model.

## Decision

An `EnhancementModel` protocol + a `model_id` -> model registry; each item
carries `model_id` (default `accessory_v1`); rate data + cron tables in
`rates.json` keyed by `model_id`; `accessory_v1` = Model A1. New models/patches
are added by registering a class + a `rates.json` entry, no caller changes.

## Consequences

- (+) New pricing models are additive: register a class plus a rates entry.
- (+) Callers depend on the protocol, not concrete models; no churn when the
  set of models changes.
- (+) Reference data is data, not code — curves and cron counts are edited in
  `rates.json` without touching `pricing.py`.
- (-) A layer of indirection for the single v1 model (accepted for the
  extensibility it buys when `accessory_cron_v1` lands in Phase 4).
- (-) `model_id` becomes a required item attribute; the default is applied at
  seed time and by the registry.
