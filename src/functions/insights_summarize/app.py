"""insightsSummarize Lambda: Bedrock Converse call + Narrative validation.

Runs OUTSIDE the VPC (no VpcConfig). Receives the output of insights_compute
(region, period, target_date, digest), calls Bedrock Converse to generate a
narrative, validates the response against the Narrative Pydantic model, and
passes the result to insights_store. On ANY failure (Bedrock, JSON parse,
validation), returns narrative=null to signal fallback.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer
from botocore.config import Config

from bdo_common.insights.models import MarketDigest, Narrative
from bdo_common.insights.prompt import build_converse_request

logger = Logger()
tracer = Tracer()

_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0")
_BEDROCK_CONFIG = Config(retries={"max_attempts": 2, "mode": "standard"})

bedrock_client = boto3.client("bedrock-runtime", config=_BEDROCK_CONFIG)


def _extract_json(text: str) -> str:
    """Extract the JSON object from model output that may include extras.

    Despite the prompt, models sometimes wrap the JSON in ```json ... ``` fences
    or add a sentence of preamble/trailing prose. Return the substring from the
    first ``{`` to the last ``}`` (the Narrative response is a single object);
    fall back to the stripped text if no braces are found.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text.strip()


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Call Bedrock Converse and return validated Narrative or null fallback."""
    region: str = event["region"]
    period: str = event["period"]
    target_date: str = event["target_date"]
    digest = MarketDigest.model_validate(event["digest"])

    try:
        request_kwargs = build_converse_request(digest, _MODEL_ID)
        response = bedrock_client.converse(**request_kwargs)

        # Extract text from the Converse response
        output_message = response["output"]["message"]
        text_content: str = output_message["content"][0]["text"]

        # Parse and validate against Narrative schema (tolerating code fences /
        # prose the model may add around the JSON object).
        raw = json.loads(_extract_json(text_content))
        narrative = Narrative.model_validate(raw)

        logger.info(
            "insightsSummarize succeeded",
            extra={"region": region, "period": period, "model_id": _MODEL_ID},
        )
        return {
            "region": region,
            "period": period,
            "target_date": target_date,
            "digest": event["digest"],
            "narrative": narrative.model_dump(mode="json"),
            "model_id": _MODEL_ID,
        }
    except Exception:
        logger.warning(
            "insightsSummarize failed; signalling fallback",
            exc_info=True,
            extra={"region": region, "period": period},
        )
        return {
            "region": region,
            "period": period,
            "target_date": target_date,
            "digest": event["digest"],
            "narrative": None,
            "model_id": "deterministic-v1",
        }
