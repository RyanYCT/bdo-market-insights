"""Config-as-data across the two sanctioned locations — and no third.

Deploy-time config is version-controlled in ``samconfig.toml`` and changed via a
pull request opened with ``gh``; operational config lives in SSM Parameter Store
under repo-scoped paths with audited writes. There is no third store here: no
GitHub Environment or Environment-secret work (that is the ``GitHubExecutor``'s
remit) and no runtime flag store (deferred by the design).

Two types carry the behaviour, mirroring ``executors/sam.py``, ``git.py`` and
``github.py``:

- ``ConfigStore`` — the Protocol the design documents and ``Dispatcher``
  type-hints against. It is the interface; it prescribes no invocation.
- ``SsmSamconfigStore`` — the concrete adapter that satisfies it over the two
  real locations. Both of its collaborators are injected: an SSM client for the
  Parameter Store side, and the shared ``run_command`` runner for the ``git`` /
  ``gh`` side. So a merged read can be exercised against ``moto`` and a config PR
  against a recorded command log, with no live AWS and no live repository.

``run_step`` switches on ``step.op`` and reads typed values out of
``step.params``; it never parses ``step.command``, which is the display rendering
only.

Four things about this adapter are load-bearing and easy to undo by accident:

**SSM is reached through boto3, not through the AWS CLI.** This executor is
deliberately *not* a ``run_command`` client for AWS (``_process`` says so too):
the SSM operations are one API call each, and the ``aws ssm …`` line a plan
renders is the faithful, copy-pasteable *rendering* of that call rather than the
thing that runs. boto3 also brings its own retry behaviour, so none is
hand-rolled here.

**Values are masked by type or by name, in the model.** A value whose backing
parameter is a ``SecureString``, or whose key name contains ``secret`` /
``password`` / ``token`` / ``key`` (case-insensitive), is held as a
``SecretStr`` — the same device plan previews use (Requirement 3.2). Masking in
the model rather than in a renderer is what makes ``--json`` safe: the value is
*absent from the serialized ``ConfigView``*, not merely omitted when printing.
Reads also pass ``WithDecryption=False``, so a ``SecureString``'s plaintext is
never even fetched.

**A refused SSM write happens before AWS is called.** ``put_ssm`` runs
``validate_ssm_path`` first, so a non-repo-scoped path — or one whose ``<stage>``
segment is not an environment defined in ``samconfig.toml`` — fails with exit
``2`` having made no API call at all, leaving any existing value untouched
(Requirements 9.1, 3.5). A ``PutParameter`` that *does* run and then fails has by
definition written nothing, so the boto3 error is surfaced as a named failure —
no traceback — saying the prior value is unchanged. Note what travels where
(Requirement 9.2): this executor writes *values* into SSM, while what reaches
CloudFormation is only key **paths** (ADR-0024). The two are not interchangeable.

**A samconfig edit preserves the file.** ``samconfig.toml`` is heavily commented
— the ``language_extensions`` explanation and the per-stage ``s3_prefix`` warning
are load-bearing operational knowledge — and a ``tomllib`` read plus a re-dump
would silently delete every one of those comments. So the edit goes through
``tomlkit``, which round-trips comments, key order and formatting, and the change
is proposed as a pull request rather than applied (Requirements 3.3, 3.6).
``BdoRegions`` is the single active-region toggle and lives only here, inside the
stage's ``parameter_overrides`` (ADR-0036, Requirement 9.3).
"""

from __future__ import annotations

import datetime as dt
import getpass
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, assert_never, cast

import boto3
import tomlkit
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import SecretStr
from tomlkit import TOMLDocument
from tomlkit.items import Table

from bdo_deploy.core.errors import UsageError
from bdo_deploy.core.executors._process import CommandRunner, run_command
from bdo_deploy.core.executors.base import StepExecutor, str_param
from bdo_deploy.core.models import (
    CommandResult,
    ConfigDiff,
    ConfigView,
    Op,
    PlanStep,
    PrRef,
)
from bdo_deploy.core.validation import (
    SAMCONFIG_PATH,
    SSM_ROOT_SEGMENT,
    validate_ssm_path,
    validate_stage,
)

GIT: Final = "git"
GH: Final = "gh"
"""The two CLIs the tracked-file path uses; SSM uses boto3, never a CLI."""

SAMCONFIG_FILE: Final = "samconfig.toml"
"""The tracked file a deploy-time config change edits, relative to the repo root."""

SECURE_STRING: Final = "SecureString"
"""The SSM parameter type whose value is masked whatever its name (Req. 3.2)."""

SECRET_NAME_SUBSTRINGS: Final = ("secret", "password", "token", "key")
"""A key name containing any of these (case-insensitive) is masked (Req. 3.2)."""

MASK: Final = "***"
"""What a masked value is reported as in a ``ConfigDiff``, which is a plain
``str`` field: a SecureString diff says *that* it changed without reproducing
either version of the value."""

PARAMETER_OVERRIDES_KEY: Final = "parameter_overrides"
"""The ``[<stage>.deploy.parameters]`` entry holding the CloudFormation parameter
set as one space-separated ``Key=Value`` string — where ``BdoRegions`` lives."""

STAGE_PARAM: Final = "stage"
SSM_PATH_PARAM: Final = "ssm_path"
PATH_PARAM: Final = "path"
VALUE_PARAM: Final = "value"
KEY_PARAM: Final = "key"
BRANCH_PARAM: Final = "branch"
BASE_PARAM: Final = "base"
TITLE_PARAM: Final = "title"

_URL_PATTERN: Final = re.compile(r"https://\S+")
"""How the opened PR's URL is picked out of ``gh pr create``'s output."""


class SsmClient(Protocol):
    """The three SSM calls this executor makes, and nothing else.

    A local Protocol rather than a generated client type: it documents the exact
    API surface the control plane touches (two reads and one write), it keeps the
    client injectable so ``moto`` can stand in, and it means no other SSM
    operation can be reached from here by accident.
    """

    def get_parameters_by_path(
        self,
        *,
        Path: str,  # noqa: N803  # boto3's parameter names are PascalCase
        Recursive: bool,  # noqa: N803
        WithDecryption: bool,  # noqa: N803
        NextToken: str = ...,  # noqa: N803
    ) -> dict[str, Any]: ...

    def get_parameter(
        self,
        *,
        Name: str,  # noqa: N803
        WithDecryption: bool,  # noqa: N803
    ) -> dict[str, Any]: ...

    def put_parameter(
        self,
        *,
        Name: str,  # noqa: N803
        Value: str,  # noqa: N803
        Type: str,  # noqa: N803
        Overwrite: bool,  # noqa: N803
        Description: str = ...,  # noqa: N803
    ) -> dict[str, Any]: ...


class ConfigStore(StepExecutor, Protocol):
    """Protocol for the config-as-data adapter — the interface, not an invocation."""

    def read_merged(self, stage: str) -> ConfigView:
        """Merged read of ``samconfig.toml`` + SSM for ``config show``.

        Mutates nothing in either location (Requirement 3.1) and masks every
        value that must not be rendered (Requirement 3.2).
        """
        ...

    def open_config_pr(
        self,
        stage: str,
        changes: list[ConfigDiff],
        *,
        branch: str | None = None,
        base: str | None = None,
        title: str | None = None,
    ) -> PrRef:
        """Edit the tracked file on a branch and open a pull request via ``gh``.

        Deploy-time config changes — ``BdoRegions`` among them — flow through
        review rather than being flipped in place (Requirements 3.3, 3.6).

        ``branch`` / ``base`` / ``title`` are the strings the plan previewed;
        omitting them derives the same shape locally, which is what a direct
        (non-planned) call gets.
        """
        ...

    def put_ssm(self, path: str, value: str) -> ConfigDiff:
        """``PutParameter`` with an audit record, returning the change it made.

        Rejects any name that is not a repo-scoped
        ``/bdo-market-insights/<stage>/<category>/<key>`` path *before* calling
        AWS (Requirements 3.4, 9.1).
        """
        ...


class SsmSamconfigStore:
    """``ConfigStore`` implemented over SSM (boto3) and ``samconfig.toml`` (tomlkit + ``gh``).

    Named for the two locations it is allowed to touch, so a third store cannot
    be added here without the class name becoming a lie.

    Satisfies the Protocol **structurally** rather than by inheritance — the
    ``TYPE_CHECKING`` binding at the end of this module is what makes mypy prove
    it — so ``Dispatcher``'s ``ConfigStore``-typed parameter accepts it without
    ``dispatch.py`` knowing this class exists.

    The SSM client is created lazily: constructing one at import time would need
    credentials and a region merely to *plan*, and planning is pure.
    """

    def __init__(
        self,
        *,
        client: SsmClient | None = None,
        runner: CommandRunner = run_command,
        samconfig_path: Path | None = None,
    ) -> None:
        self._client = client
        self._run = runner
        self._samconfig_path = samconfig_path or SAMCONFIG_PATH

    @property
    def client(self) -> SsmClient:
        """The SSM client, created on first use and reused thereafter."""
        if self._client is None:
            created: Any = boto3.client("ssm")
            self._client = cast("SsmClient", created)
        return self._client

    # -- typed domain methods ----------------------------------------------

    def read_merged(self, stage: str) -> ConfigView:
        """Read both locations for ``stage`` and return the masked merged view.

        Two reads, no writes: the samconfig side is a file read, the SSM side is
        ``GetParametersByPath`` under the stage's repo-scoped prefix, and neither
        location is mutated (Requirement 3.1). ``stage`` is validated first, so an
        undefined stage is exit ``2`` rather than an empty view that looks like a
        stage with no config.
        """
        validate_stage(stage)
        samconfig = _samconfig_values(self._samconfig_path, stage)
        parameters = self._read_ssm_parameters(_stage_prefix(stage))
        masked: list[str] = []
        return ConfigView(
            stage=stage,
            samconfig={
                key: _mask(key, value, masked=masked, secure=False)
                for key, value in samconfig.items()
            },
            ssm={
                name: _mask(name, value, masked=masked, secure=parameter_type == SECURE_STRING)
                for name, (value, parameter_type) in parameters.items()
            },
            masked=masked,
        )

    def put_ssm(self, path: str, value: str) -> ConfigDiff:
        """Write ``value`` at ``path`` with an audit record, returning the diff.

        ``path`` is validated **before** anything is sent to AWS, so a rejected
        path writes no parameter and leaves any existing value unchanged
        (Requirements 9.1, 3.5). The prior value is then read so the returned
        diff's ``before``/``after`` are the real ones rather than a placeholder,
        and the prior *type* is reused so an existing ``SecureString`` is not
        silently downgraded to a plain ``String`` by a write.

        The returned diff masks its values under the same two conditions a merged
        read does — a ``SecureString`` parameter, or a secret-shaped key name
        (Requirement 3.2) — because ``ConfigDiff`` fields are plain ``str`` and
        travel into ``Result``: an unmasked one would print under ``--json`` even
        though the plan that produced it carried the value as a ``SecretStr``.

        The audit record is the parameter's ``Description``: who wrote it, when,
        and with what — visible to anyone reading the parameter, alongside (not
        instead of) the CloudTrail entry AWS records for the API call itself.

        Raises ``UsageError`` when the write fails, naming the path and saying
        the prior value is unchanged: a ``PutParameter`` that raised has written
        nothing, so nothing has to be rolled back.
        """
        validate_ssm_path(path)
        before, parameter_type = self._read_prior(path)
        # Masked by type *or* by name, exactly as a merged read is: a first write
        # to a secret-shaped path has no prior parameter to be a ``SecureString``
        # yet, so type alone would let the plaintext of a brand-new
        # ``.../db/password`` back out through the returned diff — and from there
        # into ``Result.model_dump_json()``, which is the one place Requirement
        # 3.2's mask has to survive to be worth anything.
        secure = parameter_type == SECURE_STRING or is_secret_name(path)
        try:
            self.client.put_parameter(
                Name=path,
                Value=value,
                Type=parameter_type,
                Overwrite=True,
                Description=_audit_description(),
            )
        except (BotoCoreError, ClientError) as exc:
            raise UsageError(
                field=PATH_PARAM,
                value=path,
                problem=(
                    f"the SSM write to {path} failed ({_aws_message(exc)}), so nothing "
                    "was written and the prior value at that path is unchanged"
                ),
                hint="check the write is permitted for this account, then re-run",
            ) from exc
        return ConfigDiff(
            source="ssm",
            key=path,
            before=MASK if (secure and before is not None) else before,
            after=MASK if secure else value,
        )

    def open_config_pr(
        self,
        stage: str,
        changes: list[ConfigDiff],
        *,
        branch: str | None = None,
        base: str | None = None,
        title: str | None = None,
    ) -> PrRef:
        """Apply ``changes`` to ``samconfig.toml`` on a branch and open a PR.

        The tracked file is **never** left modified on the operator's branch
        (Requirement 3.3): the edit is made on a scratch branch, committed,
        pushed and proposed. If any step fails, the file is restored, the
        operator is put back on the branch they started on, and the scratch
        branch is deleted — so a failed attempt leaves the working tree clean and
        the checkout where it was.

        A dirty tree is refused up front rather than worked around: switching
        branches with uncommitted work is how an operator's changes get swept
        into a config PR or stranded on a scratch branch.
        """
        validate_stage(stage)
        applicable = [change for change in changes if change.source == "samconfig"]
        if not applicable:
            raise UsageError(
                field="changes",
                value=[change.key for change in changes],
                problem="no samconfig.toml change was given, so no pull request was opened",
                hint="an SSM path is written with put_ssm, not proposed as a pull request",
            )
        default_branch, default_base, default_title = _pr_metadata(stage, applicable)
        pr_branch = branch if branch is not None else default_branch
        pr_base = base if base is not None else default_base
        pr_title = title if title is not None else default_title
        self._require_clean_tree()
        original = self._current_branch()
        created = self._run([GIT, "checkout", "-b", pr_branch])
        if not created.ok:
            raise _step_failure(f"the {pr_branch} branch could not be created", created)
        try:
            _apply_samconfig_changes(self._samconfig_path, stage, applicable)
            self._commit_and_push(pr_branch, pr_title)
            url = self._create_pull_request(branch=pr_branch, base=pr_base, title=pr_title)
        except BaseException:
            self._restore(original, pr_branch)
            raise
        self._checkout(original)
        return PrRef(branch=pr_branch, base=pr_base, title=pr_title, url=url)

    # -- the StepExecutor seam ---------------------------------------------

    def run_step(self, step: PlanStep) -> CommandResult:
        """Dispatch ``step``'s op onto the domain method that performs it.

        Switches on ``step.op`` and reads typed values out of ``step.params``; it
        **never parses ``step.command``**, which is the display rendering only.
        The match covers every ``Op`` member — the config ops onto a method, every
        other op onto a named refusal — so ``assert_never`` makes adding an op
        without handling it a type error rather than a silent fallthrough.
        """
        match step.op:
            case Op.CONFIG_SHOW:
                return self._show(str_param(step, STAGE_PARAM))
            case Op.SSM_PUT:
                return self._put(
                    str_param(step, PATH_PARAM),
                    _secret_param(step, VALUE_PARAM),
                )
            case Op.SAMCONFIG_PR:
                return self._pr(step)
            case (
                Op.SAM_BUILD
                | Op.SAM_DEPLOY
                | Op.SAM_SYNC
                | Op.SAM_PIPELINE_BOOTSTRAP
                | Op.GITHUB_RUN_WORKFLOW
                | Op.GITHUB_ENVIRONMENT_SET
                | Op.GITHUB_SECRET_SET
                | Op.GIT_TAG
                | Op.GIT_PUSH
            ):
                raise UsageError(
                    field="op",
                    value=step.op.value,
                    problem=f"{step.op.value!r} is not a config operation",
                    hint=f"route it to the {step.executor!r} executor instead",
                )
            case _:  # pragma: no cover - exhaustive over Op
                assert_never(step.op)

    def _show(self, stage: str) -> CommandResult:
        """Render the merged view as the step's output; changes nothing."""
        return CommandResult(ok=True, output=_render(self.read_merged(stage)))

    def _put(self, path: str, value: str) -> CommandResult:
        """Perform the audited SSM write and report it as the step's one change."""
        diff = self.put_ssm(path, value)
        return CommandResult(
            ok=True,
            output=f"wrote {path} in SSM Parameter Store (audited)",
            changes=[diff],
        )

    def _pr(self, step: PlanStep) -> CommandResult:
        """Open the samconfig pull request the step describes.

        The diff's ``before`` is read from the tracked file so the reported change
        is the real one, and the PR's branch / base / title come from ``params``
        — the same strings the plan previewed, so the preview and the opened pull
        request cannot disagree.
        """
        stage = str_param(step, STAGE_PARAM)
        key = str_param(step, KEY_PARAM)
        change = ConfigDiff(
            source="samconfig",
            key=key,
            before=_samconfig_values(self._samconfig_path, stage).get(key),
            after=str_param(step, VALUE_PARAM),
        )
        opened = self.open_config_pr(
            stage,
            [change],
            branch=str_param(step, BRANCH_PARAM),
            base=str_param(step, BASE_PARAM),
            title=str_param(step, TITLE_PARAM),
        )
        located = f": {opened.url}" if opened.url is not None else ""
        return CommandResult(
            ok=True,
            output=(
                f"opened a pull request from {opened.branch} into {opened.base} "
                f"setting {key} in samconfig.toml{located}"
            ),
            changes=[change],
        )

    # -- SSM reads ----------------------------------------------------------

    def _read_ssm_parameters(self, prefix: str) -> dict[str, tuple[str, str]]:
        """Every parameter under ``prefix``, as ``name -> (value, type)``.

        ``WithDecryption=False`` on purpose: a ``SecureString``'s value is masked
        in the view regardless, so decrypting it would fetch a plaintext secret
        that nothing is allowed to render. Paging is followed to the end so a
        stage with many parameters is not silently truncated — that is pagination,
        not a hand-rolled retry; boto3 owns retries.
        """
        parameters: dict[str, tuple[str, str]] = {}
        token: str | None = None
        while True:
            page = (
                self.client.get_parameters_by_path(
                    Path=prefix, Recursive=True, WithDecryption=False
                )
                if token is None
                else self.client.get_parameters_by_path(
                    Path=prefix, Recursive=True, WithDecryption=False, NextToken=token
                )
            )
            for parameter in page.get("Parameters", []):
                name = parameter.get("Name")
                if isinstance(name, str):
                    parameters[name] = (
                        str(parameter.get("Value", "")),
                        str(parameter.get("Type", "String")),
                    )
            next_token = page.get("NextToken")
            if not isinstance(next_token, str) or not next_token:
                return parameters
            token = next_token

    def _read_prior(self, path: str) -> tuple[str | None, str]:
        """The current value and type at ``path``; ``(None, "String")`` if absent.

        An absent parameter is a normal first write, not an error, so
        ``ParameterNotFound`` yields a ``None`` "before" and the default type. Any
        other read failure also yields ``None`` rather than blocking the write:
        the write is what the operator asked for, and a diff that cannot state
        the prior value is better than refusing a legitimate write because the
        read was denied.
        """
        try:
            response = self.client.get_parameter(Name=path, WithDecryption=False)
        except (BotoCoreError, ClientError):
            return None, "String"
        parameter = response.get("Parameter", {})
        value = parameter.get("Value")
        parameter_type = parameter.get("Type", "String")
        return (
            value if isinstance(value, str) else None,
            parameter_type if isinstance(parameter_type, str) else "String",
        )

    # -- the git / gh side of a config PR -----------------------------------

    def _require_clean_tree(self) -> None:
        """Refuse to start when the working tree has changes of its own."""
        status = self._run([GIT, "status", "--porcelain"])
        if not status.ok:
            raise _step_failure("the working tree state could not be read", status)
        if status.output.strip():
            raise UsageError(
                field="worktree",
                value="dirty",
                problem=(
                    "the working tree is not clean, so no config branch was created and "
                    "samconfig.toml was not touched"
                ),
                hint="commit or stash the changes first, then re-run",
            )

    def _current_branch(self) -> str | None:
        """The branch to return to afterwards, or ``None`` on a detached HEAD."""
        branch = self._run([GIT, "branch", "--show-current"])
        current = branch.output.strip()
        return current if branch.ok and current else None

    def _commit_and_push(self, branch: str, title: str) -> None:
        """Commit the edited tracked file and publish the branch."""
        staged = self._run([GIT, "add", SAMCONFIG_FILE])
        if not staged.ok:
            raise _step_failure(f"{SAMCONFIG_FILE} could not be staged", staged)
        committed = self._run([GIT, "commit", "-m", title])
        if not committed.ok:
            raise _step_failure("the config change could not be committed", committed)
        pushed = self._run([GIT, "push", "--set-upstream", "origin", branch])
        if not pushed.ok:
            raise _step_failure(f"the {branch} branch could not be pushed", pushed)

    def _create_pull_request(self, *, branch: str, base: str, title: str) -> str | None:
        """Open the pull request and return its URL if ``gh`` printed one.

        The body is fixed and short: the diff is the proposal, and this executor
        has nothing to add to it beyond why the change arrived as a PR at all.
        """
        created = self._run(
            [
                GH,
                "pr",
                "create",
                "--base",
                base,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                (
                    "Opened by the deploy control plane: deploy-time configuration lives in "
                    "samconfig.toml and changes through review, never in place."
                ),
            ]
        )
        if not created.ok:
            raise _step_failure("the pull request could not be opened", created)
        found = _URL_PATTERN.search(created.output)
        return found.group(0) if found is not None else None

    def _restore(self, original: str | None, branch: str) -> None:
        """Undo the attempt: discard the edit, go back, drop the scratch branch.

        Every step is attempted even if an earlier one failed, and none of them
        can mask the original failure — the caller re-raises it — because leaving
        an operator on a scratch branch with a modified tracked file is worse than
        a cleanup whose own output is unread.
        """
        self._run([GIT, "checkout", "--", SAMCONFIG_FILE])
        self._checkout(original)
        self._run([GIT, "branch", "--delete", "--force", branch])

    def _checkout(self, branch: str | None) -> None:
        """Return to ``branch``; a detached HEAD start has nothing to return to."""
        if branch is not None:
            self._run([GIT, "checkout", branch])


# -- masking -----------------------------------------------------------------


def _mask(
    key: str,
    value: str,
    *,
    masked: list[str],
    secure: bool,
) -> str | SecretStr:
    """Return ``value``, wrapped in ``SecretStr`` when it must not be rendered.

    Masked when the backing parameter is a ``SecureString`` or the key's name
    contains one of the secret-shaped substrings, case-insensitively — the two
    conditions Requirement 3.2 names. ``masked`` collects the keys that were
    masked, so the view can say which values it is withholding.
    """
    if secure or is_secret_name(key):
        masked.append(key)
        return SecretStr(value)
    return value


def is_secret_name(key: str) -> bool:
    """Whether ``key``'s name marks it secret-shaped (case-insensitive).

    Public because it is the criterion in **two** places: masking a value read
    here (Requirement 3.2) and refusing a deploy-time ``config set`` in
    ``core.dispatch`` (Requirement 3.8). The dispatcher imports this rather than
    re-testing ``SECRET_NAME_SUBSTRINGS`` itself, so the masking criterion and the
    refusal criterion cannot drift apart.
    """
    lowered = key.lower()
    return any(substring in lowered for substring in SECRET_NAME_SUBSTRINGS)


def _render(view: ConfigView) -> str:
    """Render a ``ConfigView`` as the plain text a ``config show`` step reports.

    ``SecretStr``'s own ``str()`` is what renders a masked value, so a value the
    model masked cannot be printed here even by mistake.
    """
    lines = [f"stage: {view.stage}", f"{SAMCONFIG_FILE} [{view.stage}.deploy.parameters]:"]
    lines += [f"  {key} = {value}" for key, value in sorted(view.samconfig.items())] or [
        "  (none)"
    ]
    lines.append(f"SSM {_stage_prefix(view.stage)}:")
    lines += [f"  {name} = {value}" for name, value in sorted(view.ssm.items())] or ["  (none)"]
    if view.masked:
        lines.append(f"masked ({len(view.masked)}): {', '.join(sorted(view.masked))}")
    return "\n".join(lines)


def _stage_prefix(stage: str) -> str:
    """The repo-scoped SSM path prefix holding ``stage``'s operational config."""
    return f"/{SSM_ROOT_SEGMENT}/{stage}/"


# -- samconfig.toml reading and editing --------------------------------------


def _samconfig_values(path: Path, stage: str) -> dict[str, str]:
    """``[<stage>.deploy.parameters]`` as flat strings, overrides expanded.

    ``parameter_overrides`` is one space-separated ``Key=Value`` string, so its
    CloudFormation parameters are lifted out as individual keys — otherwise
    ``BdoRegions``, the single active-region toggle, would not appear in a merged
    read at all (ADR-0036, Requirement 9.3). The raw string is kept too, since it
    is the entry a reviewer sees in the diff.
    """
    document = tomlkit.parse(_read_samconfig(path))
    table = _deploy_parameters(document, stage)
    values: dict[str, str] = {}
    for key, value in table.items():
        values[str(key)] = _as_toml_text(value)
    overrides = values.get(PARAMETER_OVERRIDES_KEY)
    if overrides is not None:
        values.update(_parse_overrides(overrides))
    return values


def _apply_samconfig_changes(path: Path, stage: str, changes: list[ConfigDiff]) -> None:
    """Write ``changes`` into ``samconfig.toml``, preserving comments and layout.

    ``tomlkit`` parses to a document that remembers its own formatting, so only
    the edited value changes: every comment in the file — including the
    ``language_extensions`` rationale and the per-stage ``s3_prefix`` warning —
    survives the write, which a ``tomllib`` read plus a re-dump would not.

    A key that is part of the stage's CloudFormation parameter set is edited
    *inside* ``parameter_overrides`` rather than added as a sibling entry, since
    that string is where SAM reads it from.
    """
    text = _read_samconfig(path)
    document = tomlkit.parse(text)
    table = _deploy_parameters(document, stage)
    overrides = table.get(PARAMETER_OVERRIDES_KEY)
    parameters = _parse_overrides(_as_toml_text(overrides)) if overrides is not None else {}
    for change in changes:
        if change.after is None:
            raise UsageError(
                field=f"changes.{change.key}",
                value=None,
                problem=f"the samconfig.toml change for {change.key} has no value to set",
            )
        if change.key in parameters:
            parameters[change.key] = change.after
            table[PARAMETER_OVERRIDES_KEY] = _render_overrides(parameters)
        else:
            table[change.key] = change.after
    path.write_text(tomlkit.dumps(document), encoding="utf-8")


def _read_samconfig(path: Path) -> str:
    """Read the tracked file, or fail naming it rather than raising ``OSError``."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(
            field="samconfig",
            value=str(path),
            problem=f"{SAMCONFIG_FILE} could not be read ({exc.strerror or exc})",
            hint="the deploy control plane must run from a checkout of this repository",
        ) from exc


def _deploy_parameters(document: TOMLDocument, stage: str) -> Table:
    """``[<stage>.deploy.parameters]``, or a named failure if it is absent.

    ``validate_stage`` has already established that ``stage`` is a table in the
    file, so a missing ``deploy.parameters`` sub-table means the file is shaped
    unexpectedly — worth saying so rather than editing a table invented here.
    """
    node: Any = document
    for segment in (stage, "deploy", "parameters"):
        if not isinstance(node, dict) or segment not in node:
            raise UsageError(
                field="samconfig",
                value=f"[{stage}.deploy.parameters]",
                problem=(
                    f"{SAMCONFIG_FILE} has no [{stage}.deploy.parameters] table, "
                    "so there is nothing to read or change for that stage"
                ),
            )
        node = node[segment]
    return cast("Table", node)


def _as_toml_text(value: object) -> str:
    """A TOML value as the text a merged read renders.

    Booleans are lower-cased back to their TOML spelling so a read reproduces
    what the file says (``cached = true``), not Python's ``True``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse_overrides(overrides: str) -> dict[str, str]:
    """Split a ``parameter_overrides`` string into its ``Key=Value`` pairs.

    Order is preserved (a ``dict`` is insertion-ordered) so re-rendering the
    string after an edit keeps the parameters where the file had them.
    """
    parameters: dict[str, str] = {}
    for entry in overrides.split():
        key, separator, value = entry.partition("=")
        if separator and key:
            parameters[key] = value
    return parameters


def _render_overrides(parameters: dict[str, str]) -> str:
    """Re-render ``parameter_overrides`` in the file's own shape."""
    return " ".join(f"{key}={value}" for key, value in parameters.items())


# -- step / PR plumbing ------------------------------------------------------


def _secret_param(step: PlanStep, name: str) -> str:
    """Recover an operational value that travelled as a ``SecretStr``.

    ``ssm.put`` carries its value masked so no plan preview or ``--json`` dump can
    print it (Requirement 3.7); execution is the one place the plaintext is
    needed, and this is the only place it is unwrapped.
    """
    value = step.params.get(name)
    if not isinstance(value, SecretStr):
        raise UsageError(
            field=f"params.{name}",
            value=None if value is None else "<redacted>",
            problem=f"{step.op.value} needs a SecretStr {name!r} param",
        )
    return value.get_secret_value()


def _pr_metadata(stage: str, changes: list[ConfigDiff]) -> tuple[str, str, str]:
    """Default branch / base / title for a PR opened outside ``run_step``.

    ``run_step`` passes the planner's own strings instead, so the previewed
    ``gh pr create`` line and the opened pull request cannot disagree; these are
    only reached by a direct ``open_config_pr`` call.
    """
    keys = "-".join(change.key for change in changes)
    return f"config/{stage}-{keys}", "main", f"config({stage}): set {keys}"


def _audit_description() -> str:
    """The audit record stored on the parameter: who, when, and with what.

    CloudTrail is the authoritative record of the API call; this makes the same
    facts visible to anyone who simply reads the parameter. ``getpass.getuser``
    can fail on a host with no resolvable user, which is not a reason to refuse a
    write, so it degrades to ``unknown``.
    """
    try:
        operator = getpass.getuser()
    except Exception:  # noqa: BLE001  # pragma: no cover - host without a resolvable user
        operator = "unknown"
    written = dt.datetime.now(tz=dt.UTC).isoformat(timespec="seconds")
    return f"set by {operator} at {written} via bdo-deploy"


def _aws_message(exc: BotoCoreError | ClientError) -> str:
    """The one-line reason a boto3 call failed — never a traceback."""
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


def _step_failure(problem: str, result: CommandResult) -> UsageError:
    """A named failure carrying the tool's verbatim output, never a traceback."""
    return UsageError(
        field="config_pr",
        value=None,
        problem=f"{problem}: {result.output.strip()}",
    )


if TYPE_CHECKING:  # pragma: no cover - a type-check-time assertion, not runtime code
    # ``Dispatcher`` type-hints against the Protocol, so the concrete adapter has
    # to be structurally compatible with it. Binding one to the other here makes
    # mypy fail this module if a method's name or signature ever drifts.
    _satisfies_protocol: ConfigStore = SsmSamconfigStore()


__all__ = [
    "GH",
    "GIT",
    "MASK",
    "SAMCONFIG_FILE",
    "SECRET_NAME_SUBSTRINGS",
    "SECURE_STRING",
    "ConfigStore",
    "SsmClient",
    "SsmSamconfigStore",
    "is_secret_name",
]
