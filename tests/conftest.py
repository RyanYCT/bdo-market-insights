"""Shared pytest fixtures and environment setup.

Sets Powertools/AWS environment defaults before any handler module is
imported, and provides helpers for loading ``src/functions/*/app.py`` modules
(which share the file name ``app.py`` and so cannot be imported by name).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
from collections.abc import Callable
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

# Disable X-Ray tracing and pin a region so Powertools + boto3 import cleanly
# outside Lambda. Use setdefault so a real environment can override.
os.environ.setdefault("POWERTOOLS_TRACE_DISABLED", "1")
os.environ.setdefault("POWERTOOLS_SERVICE_NAME", "bdo-market-test")
os.environ.setdefault("POWERTOOLS_METRICS_NAMESPACE", "BdoMarket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def load_handler() -> Callable[[str], ModuleType]:
    """Return a loader for ``src/functions/<name>/app.py`` by directory name."""

    def _load(name: str) -> ModuleType:
        path = _REPO_ROOT / "src" / "functions" / name / "app.py"
        spec = importlib.util.spec_from_file_location(f"fn_{name}_app", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load handler module at {path}")
        module = importlib.util.module_from_spec(spec)
        # Register before executing so Pydantic can resolve handler models'
        # postponed annotations (PEP 563) via ``sys.modules[cls.__module__]`` --
        # required by the API handlers' request-body models (e.g. ItemCreate).
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    return _load


@pytest.fixture
def lambda_context() -> Any:
    """A minimal Lambda context object for Powertools' inject_lambda_context."""
    return SimpleNamespace(
        function_name="test-fn",
        function_version="$LATEST",
        invoked_function_arn="arn:aws:lambda:us-east-1:123456789012:function:test-fn",
        memory_limit_in_mb=128,
        aws_request_id="test-request-id",
    )
