# ADR-0020: dba credentials secret gated on the bastion, generated name, per-session sync

## Status

Superseded by ADR-0027. The `dba` secret and role are removed with the standing
bastion; break-glass access authenticates as the RDS master, and routine DB
access uses the admin-query Lambda (ADR-0026).

## Context

Every Lambda authenticates to RDS with IAM database authentication and holds no
static password (ADR-0008). The one exception is the `dba` login role, which
exists for occasional human access (pgAdmin/psql) and needs an ordinary
password because those clients cannot use short-lived IAM tokens conveniently
(the 15-minute token lifetime is awkward for an interactive session).

That human access path is only reachable through the EICE bastion, which is
itself gated behind the `EnableBastion` SAM parameter (default `false`) and is
meant to be a transient toggle — brought up for a maintenance window and torn
down afterwards (ADR-0009).

Previously `DbaSecret` was created unconditionally with a **fixed name**
(`bdo-<stage>-dba-credentials`). Two problems followed:

- **Cost while idle.** A stored secret bills ~$0.40/month per stage even though
  it is only needed during the rare windows the bastion is up. It showed up as a
  visible line item alongside the RDS-managed master secret.
- **Fixed name + recovery window = re-create collision.** Deleting a secret (via
  a stack delete, or manually) puts it into a deletion recovery window during
  which the name is still reserved. A later attempt to re-create a secret with
  the same fixed name inside that window fails. This is the same class of
  fixed-name-plus-retained-resource collision documented for the icons bucket
  (ADR-0019).

## Decision

Gate the `dba` secret on the bastion and give it a generated name:

- Add a `DeployBastion` condition (`!Equals [!Ref EnableBastion, 'true']`) in
  `infra/data.yaml`, wired from the root template's `EnableBastion` parameter.
- `DbaSecret` and the `DbaSecretArn` output both carry `Condition: DeployBastion`,
  so the secret exists only while the bastion is enabled and is removed when the
  bastion is toggled back off.
- Drop the fixed `Name` so Secrets Manager assigns a **generated** name. Because
  the name is unique per creation, a recovery window from a prior secret can
  never collide with a fresh one on the next bastion bring-up.
- Because a freshly created secret's random password no longer matches the
  existing `dba` role in the database, add `scripts/set_dba_password.py` (exposed
  as `make dba-password STAGE=<dev|prod>`). It reads the master and dba secret
  values via the stack outputs, connects as master over the tunnel, and runs
  `ALTER ROLE dba WITH PASSWORD` to sync the role to the current secret. This is
  run once per bastion session, after the tunnel is up.

Consumers resolve the secret through the `DbaSecretArn` stack output rather than
a hard-coded id, since the name is now generated and the output only exists while
the bastion is up.

## Consequences

- (+) No secret billing while the bastion is down (the common state); the secret
  materializes only for the windows it is actually used.
- (+) No fixed-name recovery-window collision on the next bastion bring-up — the
  generated name is always unique. Secrets sitting in a recovery window are not
  billed, so no force-delete is needed to avoid cost, and the runbook no longer
  carries a `--force-delete-without-recovery` step.
- (+) The `dba` password is regenerated each session, narrowing the window any
  single value is valid.
- (-) One extra manual step per bastion session (`make dba-password`) before the
  role password matches the secret.
- (-) Callers must resolve the secret from the `DbaSecretArn` output instead of a
  memorable fixed name; the runbook's bastion-access and bootstrap steps were
  updated accordingly.
- Nothing in-stack consumes `DbaSecretArn`, so gating the output is safe.

## Notes

The first deploy after this change drops the existing fixed-name
`bdo-<stage>-dba-credentials` secret (it is replaced by a generated-name,
conditional resource). The old secret enters its default recovery window, which
is free; no manual cleanup is required.
