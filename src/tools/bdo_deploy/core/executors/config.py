"""Config-as-data across the two sanctioned locations — and no third.

Deploy-time config is version-controlled in ``samconfig.toml`` and changed via a
PR opened with ``gh``; operational config lives in SSM Parameter Store under
repo-scoped paths with audited writes.

Skeleton only: ``read_merged`` / ``open_config_pr`` / ``put_ssm`` land in task 3.4.
"""

from __future__ import annotations

from typing import Protocol

from bdo_deploy.core.executors import StepExecutor


class ConfigStore(StepExecutor, Protocol):
    """Protocol for the config-as-data adapter (methods defined in task 3.4)."""
