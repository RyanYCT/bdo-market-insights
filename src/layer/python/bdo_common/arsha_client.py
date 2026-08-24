"""HTTP client and response normalizer for the arsha.io market API."""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, NamedTuple

from bdo_common.models import CatalogEntry, MarketListItem, Record

logger = logging.getLogger(__name__)

_MAX_BATCH_SIZE = 50

#: ``util/db`` and ``GetWorldMarketList`` are low-traffic, uncached endpoints
#: that intermittently return 5xx / time out (worse during arsha's bursts).
#: Transient failures are retried with full-jitter exponential backoff. The
#: offline catalog builder passes a larger ``max_attempts`` than this ETL default
#: because it is not Lambda-bound.
_UTIL_DB_MAX_ATTEMPTS = 4
_UTIL_DB_TIMEOUT_SECONDS = 30
_UTIL_DB_BACKOFF_BASE_SECONDS = 2.0
_UTIL_DB_BACKOFF_MAX_SECONDS = 30.0

#: The v2 ``GetWorldMarketSubList`` endpoint (hourly ETL market fetch) is
#: intermittently flaky: arsha's Envoy front returns transient 5xx (observed
#: "upstream connect error ... connection timeout") in bursts that can span
#: several seconds. It is retried with full-jitter exponential backoff and,
#: unlike the catalog sync, *raises* if a batch still fails -- so the ETL
#: fetchData stage fails loudly (and the ExecutionsFailed alarm fires) instead
#: of silently storing nothing. Note the Step Functions FetchData Retry only
#: matches Lambda infrastructure errors (Lambda.* / States.Timeout), NOT this
#: application HTTPError, so this in-process retry is the only defense against a
#: transient arsha outage -- hence the generous budget. Worst case stays inside
#: the fetchData Lambda's 60s timeout: 5 attempts x 7s (35s) + capped backoff
#: (<=1+2+4+8s = 15s) = <=50s.
_SUBLIST_MAX_ATTEMPTS = 5
_SUBLIST_TIMEOUT_SECONDS = 7
_SUBLIST_BACKOFF_BASE_SECONDS = 1.0
_SUBLIST_BACKOFF_MAX_SECONDS = 8.0

#: Resilient (adaptive-bisect) tuning for the ETL fetchData stage. arsha proxies
#: the Imperva-protected upstream market API and fails an *entire* multi-id
#: request if any single item is blocked, so a large batch couples every item to
#: the unluckiest one. The resilient fetch splits a failing batch and retries the
#: halves down to single ids, dropping only the id(s) actually blocked instead of
#: the whole batch. Multi-id nodes get a single attempt (bisecting -- not
#: re-requesting the same coupled batch -- is the recovery); single-id nodes get
#: a light retry before being dropped. The whole operation is bounded by a
#: wall-clock deadline that stays under the 60s fetchData Lambda timeout, so a
#: broad arsha outage fails fast (and loud) rather than exhausting the bisect
#: tree. The deadline is checked before each node issues a request and is set
#: below the 60s fetchData Lambda timeout by more than one single-id leaf's
#: worst-case budget (``_RESILIENT_SINGLE_ATTEMPTS`` x ``_SUBLIST_TIMEOUT_SECONDS``
#: + backoff ~= 15s), so even a leaf that clears the gate just before the
#: deadline finishes with margin instead of tipping into a Lambda self-timeout.
_RESILIENT_MULTI_ATTEMPTS = 1
_RESILIENT_SINGLE_ATTEMPTS = 2
_RESILIENT_DEADLINE_SECONDS = 40.0


def _is_retryable_fetch_error(exc: Exception) -> bool:
    """True for transient errors worth retrying (timeouts, 5xx, 429).

    A 4xx client error (other than 429) is deterministic, so it is not retried.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return True


def _sublist_backoff_seconds(attempt: int) -> float:
    """Full-jitter exponential backoff (seconds) for SubList retries.

    Returns a random sleep in ``[0, min(cap, base * 2 ** (attempt - 1))]``
    (``attempt`` is 1-indexed). Full jitter decorrelates retries from arsha's
    transient outage windows; the cap bounds the total wall-clock against the
    fetchData Lambda's 60s timeout.
    """
    ceiling = min(
        _SUBLIST_BACKOFF_MAX_SECONDS,
        _SUBLIST_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
    )
    return random.uniform(0, ceiling)  # noqa: S311  # nosec B311 - not cryptographic


def _util_db_backoff_seconds(attempt: int) -> float:
    """Full-jitter exponential backoff (seconds) for util/db + GetWorldMarketList.

    Same full-jitter exponential shape as the SubList retry, with its own base and
    cap: a random sleep in ``[0, min(cap, base * 2 ** (attempt - 1))]``.
    """
    ceiling = min(
        _UTIL_DB_BACKOFF_MAX_SECONDS,
        _UTIL_DB_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
    )
    return random.uniform(0, ceiling)  # noqa: S311  # nosec B311 - not cryptographic


_MAX_URL_LENGTH = 1900

# arsha.io item dicts are identified by the presence of these keys. Anything
# else encountered while flattening (empty dicts, error envelopes) is ignored.
_IDENTITY_KEYS = ("id", "sid")

#: arsha.io ``lang`` query-param codes -> human-readable label. Used by the
#: item-catalog sync (``util/db?lang=``) and any language-aware request. The
#: ``grade`` returned by ``util/db`` is language-independent; ``lang`` only
#: changes the localized item ``name``.
SUPPORTED_LANGS: dict[str, str] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "ru": "Russian",
    "es": "Spanish (EU)",
    "sp": "Portuguese (RedFox)",
    "pt": "Portuguese",
    "jp": "Japanese",
    "kr": "Korean",
    "th": "Thai",
    "tr": "Turkish",
    "tw": "Chinese (Taiwan)",
    "cn": "Chinese (Mainland)",
}

#: Default language when a caller omits ``lang`` (matches the project default).
DEFAULT_LANG = "en"


def _iter_item_dicts(node: Any) -> Iterator[dict[str, Any]]:
    """Recursively yield item dicts from arsha's polymorphic JSON.

    arsha.io returns one of several shapes depending on how many items and
    enhancement levels are requested:

    * a single object              -> one non-enhanceable item
    * a list of objects            -> one enhanceable item, or many sid=0 items
    * a list of lists of objects   -> many enhanceable items
    * any mixture of the above

    Walking the structure recursively flattens every shape: dicts are item
    rows, lists are containers to descend into, scalars are ignored.
    """
    if isinstance(node, dict):
        yield node
    elif isinstance(node, list):
        for element in node:
            yield from _iter_item_dicts(element)


def _parse_record(obj: dict[str, Any]) -> Record | None:
    """Map a single arsha.io item dict onto a Record, or None if not parseable.

    Dicts that lack the identity keys (e.g. ``{}`` or an error envelope) are
    not item rows and return None silently. Item rows that are present but
    malformed are skipped with a warning.
    """
    if not all(key in obj for key in _IDENTITY_KEYS):
        return None
    try:
        return Record(
            item_id=int(obj["id"]),
            sid=int(obj["sid"]),
            name=str(obj["name"]),
            base_price=int(obj["basePrice"]),
            current_stock=int(obj["currentStock"]),
            total_trades=int(obj["totalTrades"]),
            last_sold_price=int(obj["lastSoldPrice"]),
            last_sold_at=datetime.fromtimestamp(int(obj["lastSoldTime"]), tz=UTC),
            max_enhance=int(obj["maxEnhance"]),
            price_min=int(obj["priceMin"]),
            price_max=int(obj["priceMax"]),
        )
    except (KeyError, ValueError, TypeError, OverflowError, OSError) as exc:
        logger.warning("Skipping malformed arsha item %r: %s", obj, exc)
        return None


def normalize_response(raw: Any) -> list[Record]:
    """Flatten an arsha.io GetWorldMarketSubList response into Record objects.

    Handles all polymorphic shapes (single/multi item,
    enhanceable/non-enhanceable, and mixed) by recursively flattening the JSON
    into item dicts. Non-item dicts and malformed rows are skipped.
    """
    return [record for obj in _iter_item_dicts(raw) if (record := _parse_record(obj)) is not None]


def _parse_catalog_entry(obj: dict[str, Any]) -> CatalogEntry | None:
    """Map one arsha.io ``util/db`` row onto a CatalogEntry, or None if unusable.

    Rows lacking an ``id`` are not items and return None silently; rows that are
    present but malformed (bad id/name/grade) are skipped with a warning.
    """
    if "id" not in obj:
        return None
    try:
        grade = obj.get("grade")
        return CatalogEntry(
            item_id=int(obj["id"]),
            name=str(obj["name"]),
            grade=int(grade) if grade is not None else None,
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Skipping malformed arsha catalog row %r: %s", obj, exc)
        return None


def normalize_item_db(raw: Any) -> list[CatalogEntry]:
    """Flatten an arsha.io ``util/db`` response into CatalogEntry objects.

    The endpoint returns a flat list of ``{id, name, grade}`` dicts. A non-list
    payload (e.g. an error envelope) yields an empty list; non-dict elements and
    malformed rows are skipped.
    """
    if not isinstance(raw, list):
        return []
    return [
        entry
        for obj in raw
        if isinstance(obj, dict) and (entry := _parse_catalog_entry(obj)) is not None
    ]


def _parse_market_list_entry(obj: dict[str, Any]) -> MarketListItem | None:
    """Map one arsha.io ``GetWorldMarketList`` row onto a MarketListItem, or None.

    Rows missing the id or category keys are skipped silently; present-but-
    malformed rows are skipped with a warning.
    """
    if "id" not in obj or "mainCategory" not in obj or "subCategory" not in obj:
        return None
    try:
        return MarketListItem(
            item_id=int(obj["id"]),
            name=str(obj.get("name", "")),
            main_category=int(obj["mainCategory"]),
            sub_category=int(obj["subCategory"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("Skipping malformed arsha market-list row %r: %s", obj, exc)
        return None


def normalize_market_list(raw: Any) -> list[MarketListItem]:
    """Flatten an arsha.io ``GetWorldMarketList`` response into MarketListItems.

    The endpoint returns a flat list of item dicts, each with its category
    codes. A non-list payload yields an empty list; malformed rows are skipped.
    """
    if not isinstance(raw, list):
        return []
    return [
        entry
        for obj in raw
        if isinstance(obj, dict) and (entry := _parse_market_list_entry(obj)) is not None
    ]


class FetchOutcome(NamedTuple):
    """Outcome of a resilient fetch.

    ``payloads`` holds one raw arsha JSON payload per successful (sub-)request;
    ``failed_ids`` lists item IDs that could not be fetched (dropped after the
    retry/bisect budget or the deadline). An empty ``failed_ids`` means every
    requested item was fetched.
    """

    payloads: list[Any]
    failed_ids: list[int]


class ArshaClient:
    """HTTP client for the arsha.io market data API."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.arsha.io/v2",
        region: str = "tw",
        util_base_url: str = "https://api.arsha.io/util",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._region = region
        self._util_base_url = util_base_url.rstrip("/")

    def _build_url(self, ids: list[int]) -> str:
        """Build the GetWorldMarketSubList URL for a batch of item IDs."""
        csv_ids = ",".join(str(i) for i in ids)
        return f"{self._base_url}/{self._region}/GetWorldMarketSubList?id={csv_ids}"

    def _split_batch_by_url_length(self, ids: list[int]) -> list[list[int]]:
        """Split a batch further if the resulting URL exceeds the max length."""
        url = self._build_url(ids)
        if len(url) <= _MAX_URL_LENGTH:
            return [ids]

        mid = len(ids) // 2
        left = ids[:mid]
        right = ids[mid:]

        result: list[list[int]] = []
        if left:
            result.extend(self._split_batch_by_url_length(left))
        if right:
            result.extend(self._split_batch_by_url_length(right))
        return result

    def _plan_batches(self, item_ids: list[int]) -> list[list[int]]:
        """Group IDs into <= 50-ID batches, splitting any over-long URL."""
        batches: list[list[int]] = []
        for i in range(0, len(item_ids), _MAX_BATCH_SIZE):
            chunk = item_ids[i : i + _MAX_BATCH_SIZE]
            batches.extend(self._split_batch_by_url_length(chunk))
        return batches

    def _fetch_batch_with_retry(self, url: str, max_attempts: int = _SUBLIST_MAX_ATTEMPTS) -> Any:
        """GET and JSON-decode one SubList batch, retrying transient failures.

        Retries 5xx/429/timeouts with full-jitter exponential backoff (the
        market endpoint is intermittently flaky, failing in short bursts); a
        non-retryable 4xx or an exhausted retry budget re-raises. ``max_attempts``
        lets the resilient bisect path use a smaller per-node budget than the
        default full retry.
        """
        for attempt in range(1, max_attempts + 1):
            try:
                # URL is built internally and is always https://api.arsha.io/...
                with urllib.request.urlopen(  # noqa: S310  # nosec B310
                    url, timeout=_SUBLIST_TIMEOUT_SECONDS
                ) as resp:
                    return json.loads(resp.read().decode())
            except Exception as exc:
                if not _is_retryable_fetch_error(exc) or attempt == max_attempts:
                    logger.error(
                        "GetWorldMarketSubList fetch failed for %s after %d attempt(s): %s",
                        url,
                        attempt,
                        exc,
                    )
                    raise
                logger.warning(
                    "GetWorldMarketSubList attempt %d/%d failed for %s: %s; retrying",
                    attempt,
                    max_attempts,
                    url,
                    exc,
                )
                time.sleep(_sublist_backoff_seconds(attempt))
        raise RuntimeError("unreachable: SubList retry loop exhausted")  # pragma: no cover

    def fetch_raw(self, item_ids: list[int]) -> list[Any]:
        """Fetch raw arsha.io JSON payloads (one per HTTP request), unparsed.

        Batches into groups of <= 50 IDs, further splitting if the URL exceeds
        1900 characters. Each batch is retried on transient arsha failures
        (5xx/429/timeout); if a batch still fails the error propagates
        (all-or-nothing). Used by the single-item registration path
        (:meth:`fetch_sub_list`); the hourly ETL uses :meth:`fetch_raw_resilient`
        instead, which tolerates a few individually blocked items. Parsing is
        deferred to ``cleanData`` so retries never re-parse.
        """
        if not item_ids:
            return []

        payloads: list[Any] = []
        for batch in self._plan_batches(item_ids):
            payloads.append(self._fetch_batch_with_retry(self._build_url(batch)))
        return payloads

    def _fetch_with_bisect(self, ids: list[int], deadline: float) -> FetchOutcome:
        """Fetch ``ids``, splitting on failure to isolate blocked items.

        arsha fails a whole multi-id request if any one item is blocked upstream
        (Imperva), so on failure the batch is halved and each half retried
        recursively. A multi-id node gets a single attempt (splitting is the
        recovery); a single id gets a light retry and, if still failing, is
        reported in ``failed_ids`` rather than raising. Once ``deadline`` (a
        :func:`time.monotonic` value) passes, the remaining ids are reported
        failed without further requests, bounding total wall-clock.
        """
        if time.monotonic() >= deadline:
            logger.error(
                "resilient fetch deadline exceeded; dropping %d item(s): %s", len(ids), ids
            )
            return FetchOutcome([], list(ids))

        attempts = _RESILIENT_SINGLE_ATTEMPTS if len(ids) == 1 else _RESILIENT_MULTI_ATTEMPTS
        try:
            payload = self._fetch_batch_with_retry(self._build_url(ids), max_attempts=attempts)
            return FetchOutcome([payload], [])
        except Exception as exc:
            if len(ids) == 1:
                logger.error("dropping item %d after %d attempt(s): %s", ids[0], attempts, exc)
                return FetchOutcome([], [ids[0]])
            mid = len(ids) // 2
            left = self._fetch_with_bisect(ids[:mid], deadline)
            right = self._fetch_with_bisect(ids[mid:], deadline)
            return FetchOutcome(left.payloads + right.payloads, left.failed_ids + right.failed_ids)

    def fetch_raw_resilient(self, item_ids: list[int]) -> FetchOutcome:
        """Fetch raw payloads, dropping (not raising on) individually blocked ids.

        Like :meth:`fetch_raw` but resilient to arsha's per-item upstream
        (Imperva) blocking: a batch that fails is bisected down to single ids so
        only the item(s) actually blocked are dropped, and they are returned in
        :attr:`FetchOutcome.failed_ids` instead of failing the whole fetch. The
        work is bounded by a wall-clock deadline (< the fetchData Lambda's 60s
        timeout), so a broad arsha outage returns quickly with everything failed
        -- letting the caller decide (by fraction failed) whether to fail loud.
        Parsing is deferred to ``cleanData`` so retries never re-parse.
        """
        if not item_ids:
            return FetchOutcome([], [])

        deadline = time.monotonic() + _RESILIENT_DEADLINE_SECONDS
        payloads: list[Any] = []
        failed: list[int] = []
        for batch in self._plan_batches(item_ids):
            outcome = self._fetch_with_bisect(batch, deadline)
            payloads.extend(outcome.payloads)
            failed.extend(outcome.failed_ids)
        return FetchOutcome(payloads, failed)

    def fetch_sub_list(self, item_ids: list[int]) -> list[Record]:
        """Fetch and normalize market data for the given item IDs.

        Convenience wrapper over :meth:`fetch_raw` + :func:`normalize_response`
        for callers that want parsed records in one call.
        """
        all_records: list[Record] = []
        for payload in self.fetch_raw(item_ids):
            all_records.extend(normalize_response(payload))
        return all_records

    def _build_item_db_url(self, lang: str) -> str:
        """Build the ``util/db`` full-catalog URL for a language."""
        return f"{self._util_base_url}/db?lang={lang}"

    def fetch_item_db(
        self, lang: str = DEFAULT_LANG, *, max_attempts: int = _UTIL_DB_MAX_ATTEMPTS
    ) -> list[CatalogEntry]:
        """Fetch the full BDO item catalog for ``lang`` from arsha.io ``util/db``.

        Returns every known item as a CatalogEntry (id, localized name, grade).
        Raises ValueError for an unsupported ``lang``. Transient failures (5xx,
        timeouts) are retried with full-jitter exponential backoff; if every
        attempt fails the error is logged and an empty list is returned, so a bad
        fetch is a no-op for the (upsert-only) catalog sync rather than a
        destructive event. ``max_attempts`` lets the offline catalog builder use a
        larger budget than the ETL default.
        """
        if lang not in SUPPORTED_LANGS:
            supported = ", ".join(sorted(SUPPORTED_LANGS))
            msg = f"unsupported lang {lang!r}; expected one of: {supported}"
            raise ValueError(msg)
        url = self._build_item_db_url(lang)
        for attempt in range(1, max_attempts + 1):
            try:
                # URL is built internally and is always https://api.arsha.io/...
                with urllib.request.urlopen(  # noqa: S310  # nosec B310
                    url, timeout=_UTIL_DB_TIMEOUT_SECONDS
                ) as resp:
                    raw = json.loads(resp.read().decode())
                return normalize_item_db(raw)
            except Exception as exc:
                if not _is_retryable_fetch_error(exc) or attempt == max_attempts:
                    logger.error(
                        "util/db fetch failed for %s after %d attempt(s): %s", url, attempt, exc
                    )
                    return []
                logger.warning(
                    "util/db fetch attempt %d/%d failed for %s: %s; retrying",
                    attempt,
                    max_attempts,
                    url,
                    exc,
                )
                time.sleep(_util_db_backoff_seconds(attempt))
        return []  # unreachable: the loop returns on success or final failure

    def _build_market_list_url(self, main_category: int, sub_category: int) -> str:
        """Build the GetWorldMarketList URL for one market (main, sub) category."""
        return (
            f"{self._base_url}/{self._region}/GetWorldMarketList"
            f"?mainCategory={main_category}&subCategory={sub_category}"
        )

    def fetch_market_list(
        self,
        main_category: int,
        sub_category: int,
        *,
        max_attempts: int = _UTIL_DB_MAX_ATTEMPTS,
    ) -> list[MarketListItem]:
        """Fetch every item in one market category, with its taxonomy codes.

        ``GetWorldMarketList`` is the only endpoint that returns an item's
        ``mainCategory``/``subCategory``; the catalog (``util/db``) and
        ``GetWorldMarketSubList`` do not. Used by the offline catalog builder to
        enumerate the taxonomy.

        A non-existent ``(main, sub)`` returns HTTP 404 (a non-retryable error),
        which is not a failure but the *category boundary* -- an empty list is
        returned to signal it. Transient failures (5xx, timeouts) are retried
        with full-jitter exponential backoff and, once ``max_attempts`` is
        exhausted, **raise**. Distinguishing an empty boundary (return) from
        arsha being unavailable (raise) is what lets the builder's taxonomy walk
        stop at a real boundary without mistaking a transient outage for one and
        silently truncating the crawl.
        """
        url = self._build_market_list_url(main_category, sub_category)
        for attempt in range(1, max_attempts + 1):
            try:
                # URL is built internally and is always https://api.arsha.io/...
                with urllib.request.urlopen(  # noqa: S310  # nosec B310
                    url, timeout=_UTIL_DB_TIMEOUT_SECONDS
                ) as resp:
                    raw = json.loads(resp.read().decode())
                return normalize_market_list(raw)
            except Exception as exc:
                if not _is_retryable_fetch_error(exc):
                    # Non-retryable (e.g. 404): the category does not exist. This
                    # is the taxonomy boundary, not a failure -> empty result.
                    return []
                if attempt == max_attempts:
                    logger.error(
                        "GetWorldMarketList fetch failed for %s after %d attempt(s): %s",
                        url,
                        attempt,
                        exc,
                    )
                    raise
                logger.warning(
                    "GetWorldMarketList attempt %d/%d failed for %s: %s; retrying",
                    attempt,
                    max_attempts,
                    url,
                    exc,
                )
                time.sleep(_util_db_backoff_seconds(attempt))
        raise RuntimeError(
            "unreachable: GetWorldMarketList retry loop exhausted"
        )  # pragma: no cover
