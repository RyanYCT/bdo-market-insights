"""insightsSummarize Lambda: Bedrock Converse call (headline + overall only).

Runs OUTSIDE the VPC (no VpcConfig). Receives the output of insights_compute
(region, period, target_date, digest), calls Bedrock Converse for the
qualitative headline + overall (a NarrativeSummary), and merges them with
deterministically-rendered per-item bullets into a full Narrative -- so exact
figures never depend on the model (ADR-0016). On an empty digest or ANY failure
(Bedrock, JSON parse, validation), returns narrative=null so insights_store
renders the full deterministic narrative.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from aws_lambda_powertools import Logger, Tracer
from botocore.config import Config

from bdo_common.insights.models import MarketDigest, Narrative, NarrativeSummary
from bdo_common.insights.narrative import render_narrative
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

    # An empty digest carries no facts to narrate. Per ADR-0016 the model may
    # only narrate the figures it is given and must invent nothing -- but with
    # zero entries there is nothing to ground it, and models hallucinate items
    # and prices to fill the void. Short-circuit to the deterministic fallback
    # (narrative=None), which renders an honest "no movements" summary in
    # insights_store. This also avoids a pointless Bedrock call/spend.
    if not digest.entries:
        logger.info(
            "insightsSummarize skipped Bedrock: empty digest, using deterministic fallback",
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

    try:
        request_kwargs = build_converse_request(digest, _MODEL_ID)
        response = bedrock_client.converse(**request_kwargs)

        # Extract text from the Converse response
        output_message = response["output"]["message"]
        text_content: str = output_message["content"][0]["text"]

        # Parse and validate the qualitative headline + overall (tolerating code
        # fences / prose the model may add around the JSON object).
        raw = json.loads(_extract_json(text_content))
        summary = NarrativeSummary.model_validate(raw)

        # Hybrid (ADR-0016): the LLM supplies only headline + overall; the
        # per-item bullets are rendered deterministically, so exact figures
        # never depend on the model.
        narrative = Narrative(
            headline=summary.headline,
            categories=render_narrative(digest).categories,
            overall=summary.overall,
        )

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
