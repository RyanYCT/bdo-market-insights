# ADR-0041: Typer for the CLI front-end, Textual for the TUI front-end

## Status

Accepted. Implements Requirements 1.1 and 1.3 of
`.kiro/specs/deploy-control-plane/`.

## Context

The control plane (ADR-0039) is thin by construction: both front-ends collect
intent into a typed `Command`, hand it to one `Dispatcher`, and render the
`Result`. So the framework question is narrower than a general CLI/TUI
comparison — neither front-end holds routing, validation or capability logic,
and whatever is chosen has to earn its place on argument declaration and
rendering alone. The spec flagged one alternative per side for this ADR:
stdlib `argparse` as the zero-dependency CLI, and `questionary + rich` as the
lighter prompt-driven TUI.

## Decision

**Typer for the CLI (`src/tools/bdo_deploy/cli.py`).** Because the front-end
is thin, a type-annotated function signature *is* the parser: `deploy(stage,
target, sync, json_output, yes, dry_run, watch)` declares the surface once,
and there is no separate argument schema to keep in step with the `Command`
model it builds. `Capability` and `Target` are `StrEnum`s, which Typer turns
into validated choices directly — `--target` accepts `local`/`ci` and nothing
else without a `choices=` list restating the enum. The three contract flags of
Requirement 1.1 are declared once as shared `Annotated` option types
(`JsonFlag`, `YesFlag`, `DryRunFlag`, plus `StageOption`/`WatchFlag`) and
reused by every subcommand, so `--json` means the same thing, with the same
help text, everywhere it appears.

`argparse` would have worked — nothing here needs a third-party parser. The
honest cost is boilerplate (a subparser, an `add_argument` per flag per
capability) and, more to the point, a *second place option metadata lives*:
the enum's members and the flag's help would exist both on the model and in
the parser setup, which is the same drift shape ADR-0039 was written to avoid.

**Textual for the TUI (`src/tools/bdo_deploy/tui.py`).** Requirement 1.3 is
not "ask the operator some questions"; it is *render the `Plan` and wait for
an explicit decision*, on something the operator can read in full before
deciding. That is a screen, not a prompt: `_ConfirmScreen` displays the plan
the core refused and the human either confirms or cancels. The guided flow is
then a stack of screens (`_CapabilityScreen` → `_IntentScreen` →
`_ConfirmScreen` → `_ResultScreen`), which is Textual's model rather than
something built on top of it. It is also testable headlessly:
`tests/unit/test_deploy_tui.py` drives real widgets through `App.run_test()`
with no terminal, which is how the confirmation step is asserted as a *step* —
that a mutating plan is rendered and nothing executed, and that confirming
executes the very plan that was shown.

`questionary + rich` is lighter and would have covered intent collection well.
A prompt sequence is a poor fit for "review this whole plan, then decide": the
plan scrolls past as output rather than sitting on a screen, and a prompt
library's confirmation is a boolean question, not a reviewable surface.
Testing the confirmation step as a step would have meant scripting stdin
instead of clicking a widget.

**Both are dev/ops-group dependencies only.** `typer` and `textual` sit in the
dev group of `pyproject.toml`, never `[project.dependencies]`, and the layer
build globs `src/layer/python/` — so the package placement recorded in
ADR-0039 is what keeps them out of the Lambda layer (Requirement 9.4).
`cli.py` also imports `run_tui` *lazily*, inside the mode-selection callback,
so CLI mode does not pay the Textual import at all.

## Consequences

- (+) The argument surface has one definition. Adding a capability is a
  function with annotated parameters; the enum it validates against is the
  enum the core switches on, not a copy.
- (+) The confirmation step is a screen an operator reads before deciding, and
  the test suite drives it as one — headlessly, against real widgets, with no
  terminal and no mocked prompt layer.
- (+) CLI mode carries no Textual cost: the lazy import means an agent-only
  install that lacks it still works, and a piped invocation never loads it.
- (−) Two frameworks is two dependency surfaces to track and two rendering
  idioms to learn. Anyone touching both front-ends in one change works in
  both.
- (−) Typer vendors Click privately — `typer._click` in the installed version,
  with no importable top-level `click` — so `main()` catches `SystemExit` to
  turn Typer's own outcomes (`--help`, an unknown flag, a subcommand's
  `typer.Exit`) into a returned `int`, rather than importing Click's exception
  types. That works and is stable in practice, but it is a coupling to
  behaviour adjacent to Typer's internals, and an upgrade that changes how
  outcomes are signalled would land here first.
- (−) Typer forces colour when `GITHUB_ACTIONS` (or `FORCE_COLOR`,
  `PY_COLORS`) is set, via `typer.rich_utils.FORCE_TERMINAL` decided at
  *import*. This already bit us: help-text assertions passed locally and
  failed on the runner, because the escape codes land *inside* the phrases —
  `--watch` is emitted as `ESC[1;36m-ESC[0mESC[1;36m-watch` and is not a
  substring of the output. The tests now force colour on unconditionally and
  strip ANSI plus wrapping before asserting, at two pinned widths either side
  of any plausible terminal, so the failure cannot come back only on CI.
  Anything asserting on rendered help pays this tax.
- (−) Textual moves comparatively fast for an ops tool whose TUI is explicitly
  a convenience skin. A breaking upgrade would cost time on the least
  load-bearing part of the system.
- (−) The TUI is not the system of record — GitHub's own Actions UI is. Its
  value is convenience, which makes it the first thing to drop if it becomes a
  maintenance burden. That is the intended answer to a costly Textual upgrade,
  not a reason to invest in insulating against one.
