"""Environment-variable reader with Powertools parameters cache."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from aws_lambda_powertools.utilities import parameters


@dataclass(frozen=True)
class Settings:
    """Lambda runtime configuration read from environment variables."""

    region: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    dynamodb_table: str
    stage: str
    use_iam_auth: bool


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build settings from ``os.environ`` with sensible defaults."""
    return Settings(
        region=os.environ.get("BDO_REGION", "tw"),
        db_host=os.environ.get("DB_HOST", "localhost"),
        db_port=int(os.environ.get("DB_PORT", "5432")),
        db_name=os.environ.get("DB_NAME", "bdo"),
        db_user=os.environ.get("DB_USER", "lambda_rds_user"),
        dynamodb_table=os.environ.get("DYNAMODB_TABLE", "bdo-dev-items"),
        stage=os.environ.get("STAGE", "dev"),
        use_iam_auth=os.environ.get("USE_IAM_AUTH", "false").lower() in ("1", "true", "yes"),
    )


def get_parameter(name: str) -> str:
    """Retrieve an SSM parameter with a 5-minute TTL cache.

    Wraps ``aws_lambda_powertools.utilities.parameters.get_parameter``
    so callers don't need a direct Powertools dependency.
    """
    value: str | None = parameters.get_parameter(name, max_age=300)
    if value is None:
        msg = f"SSM parameter {name!r} returned None"
        raise ValueError(msg)
    return value
