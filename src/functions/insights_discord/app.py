"""insightsDiscord Lambda: deliver insight notifications to Discord.

SNS-triggered, out-of-VPC. Reads the webhook URL from SSM SecureString
``/bdo/${Stage}/discord-webhook`` via Powertools parameters (cached). POSTs a
formatted message to the Discord webhook using stdlib ``urllib.request``.

This handler NEVER raises -- any failure is logged and a
``DiscordDeliveryFailures`` metric is emitted, but the function returns
successfully to avoid infinite SNS retries.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any
from urllib.error import URLError

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities import parameters

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")

_STAGE = os.environ.get("STAGE", "dev")
_SSM_PARAM_NAME = f"/bdo/{_STAGE}/discord-webhook"


def _get_webhook_url() -> str:
    """Retrieve Discord webhook URL from SSM SecureString (cached)."""
    value: str | None = parameters.get_parameter(_SSM_PARAM_NAME, decrypt=True)
    if not value:
        raise ValueError("Discord webhook URL is empty")
    return value


def _build_discord_payload(message: dict[str, Any]) -> dict[str, Any]:
    """Build a Discord webhook JSON payload from the SNS message body."""
    headline: str = message.get("headline", "New market insight available")
    region: str = message.get("region", "unknown")
    period: str = message.get("period", "daily")
    target_date: str = message.get("target_date", "")
    model_id: str = message.get("model_id", "")

    description_parts: list[str] = []
    if target_date:
        description_parts.append(f"**Date:** {target_date}")
    description_parts.append(f"**Region:** {region} | **Period:** {period}")
    if model_id:
        description_parts.append(f"**Model:** {model_id}")

    return {
        "embeds": [
            {
                "title": headline,
                "description": "\n".join(description_parts),
                "color": 3447003,  # Discord blue
            }
        ],
    }


def _post_to_discord(webhook_url: str, payload: dict[str, Any]) -> None:
    """POST the JSON payload to the Discord webhook URL."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)  # noqa: S310


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process SNS event and deliver notification to Discord."""
    try:
        record = event["Records"][0]
        sns_message_raw: str = record["Sns"]["Message"]
        message: dict[str, Any] = json.loads(sns_message_raw)

        webhook_url = _get_webhook_url()
        payload = _build_discord_payload(message)
        _post_to_discord(webhook_url, payload)

        logger.info("Discord notification delivered", extra={"region": message.get("region")})
    except (URLError, OSError) as exc:
        metrics.add_metric(name="DiscordDeliveryFailures", unit=MetricUnit.Count, value=1)
        logger.warning("Discord POST failed", extra={"error": str(exc)})
    except Exception as exc:
        metrics.add_metric(name="DiscordDeliveryFailures", unit=MetricUnit.Count, value=1)
        logger.warning("Discord delivery failed", extra={"error": str(exc)})

    return {"status": "ok"}
