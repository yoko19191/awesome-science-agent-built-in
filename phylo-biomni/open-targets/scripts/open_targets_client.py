#!/usr/bin/env python3
"""Production Open Targets GraphQL client with an immutable operation ledger.

Every GraphQL request is routed through one helper (``request``) that appends an
immutable operation record to the ledger BEFORE returning or raising.  Failed
attempts are preserved; a successful retry supplements rather than replaces them.

Design goals mapped to the structural-repair brief:
  OT-01  failure-preserving request ledger
  OT-02  safe quick start (no bare HTTP POST calls; all via the helper)
  OT-05  release-bound provenance (meta.apiVersion captured at start)
  OT-06  bounded pagination with completeness labels
  OT-09  explicit API/bulk boundary with a typed handoff
  OT-12  audit-preserving recovery (retry_of / recovered_by links)

Stdlib only (urllib) so the skill runs with no pip install beyond what the
platform already ships.  ``requests`` is NOT required.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

DEFAULT_URL = "https://api.platform.opentargets.org/api/v4/graphql"

# --- budgets (OT-09) ---------------------------------------------------------

DEFAULT_BUDGETS = {
    "max_entities": 50,
    "max_pages": 10,
    "max_rows": 500,
    "max_bytes": 50_000_000,
    "max_elapsed_seconds": 600,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _redact_variables(variables: dict | None) -> dict:
    """Return a copy of variables safe to persist (no credentials expected, but
    keep the mechanism so future auth-bearing variables are not leaked)."""
    if not variables:
        return {}
    redacted = {}
    for k, v in variables.items():
        if isinstance(k, str) and any(s in k.lower() for s in ("token", "key", "secret", "auth", "password")):
            redacted[k] = "[REDACTED]"
        else:
            redacted[k] = v
    return redacted


class GraphQLError(Exception):
    """Raised when the response body carries GraphQL ``errors`` (HTTP 200 body error)."""

    def __init__(self, errors: list[dict], operation: str = ""):
        self.errors = errors
        self.operation = operation
        msgs = "; ".join(str(e.get("message", "")) for e in errors)
        super().__init__(f"GraphQL errors in {operation!r}: {msgs}")


class TransportError(Exception):
    """Raised on network/HTTP-transport failures (timeout, non-200, malformed JSON)."""


class BudgetExceeded(Exception):
    """Raised when a request would exceed configured entity/page/row/byte/time budgets."""

    def __init__(self, handoff: dict):
        self.handoff = handoff
        super().__init__(handoff.get("reason", "budget exceeded"))


# --- operation ledger (OT-01, OT-12) -----------------------------------------

@dataclass
class OperationRecord:
    """One immutable attempt.  Appended before the helper returns or raises."""
    attempt_id: str
    operation: str
    query_hash: str
    request_hash: str
    response_hash: str
    response_bytes: int
    response_body_state: str
    variables_redacted: dict
    start_utc: str
    end_utc: str
    http_status: int | None
    graphql_errors: list[dict] | None
    row_count: int | None
    page_index: int | None
    cursor: str | None
    total_count: int | None
    release: dict | None
    terminal_state: str          # success | transport_error | graphql_error | budget_exceeded
    retry_of: str | None         # attempt_id of the predecessor this retries
    recovered_by: str | None     # attempt_id of the successor that recovered this
    error_message: str | None

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "operation": self.operation,
            "query_hash": self.query_hash,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
            "response_bytes": self.response_bytes,
            "response_body_state": self.response_body_state,
            "variables_redacted": self.variables_redacted,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "http_status": self.http_status,
            "graphql_errors": self.graphql_errors,
            "row_count": self.row_count,
            "page_index": self.page_index,
            "cursor": self.cursor,
            "total_count": self.total_count,
            "release": self.release,
            "terminal_state": self.terminal_state,
            "retry_of": self.retry_of,
            "recovered_by": self.recovered_by,
            "error_message": self.error_message,
        }


class OperationLedger:
    """Append-only ledger of every GraphQL attempt, including failures."""

    def __init__(self) -> None:
        self._records: list[OperationRecord] = []
        self._counter = 0
        self._by_id: dict[str, OperationRecord] = {}
        self._release: dict | None = None

    def _next_id(self) -> str:
        self._counter += 1
        return f"op_{self._counter:04d}"

    def set_release(self, release: dict) -> None:
        self._release = release

    @property
    def release(self) -> dict | None:
        return self._release

    def begin(self, operation: str, query: str, variables: dict | None) -> tuple[str, str, str]:
        attempt_id = self._next_id()
        query_hash = _sha256_text(query)
        start = _utc_now_iso()
        return attempt_id, query_hash, start

    def append(self, record: OperationRecord) -> None:
        self._records.append(record)
        self._by_id[record.attempt_id] = record
        # Wire recovery links: if this record retries a predecessor, mark the
        # predecessor as recovered_by this attempt (OT-12).
        if record.retry_of and record.terminal_state == "success":
            pred = self._by_id.get(record.retry_of)
            if pred and pred.recovered_by is None:
                pred.recovered_by = record.attempt_id

    def mark_retry(self, failed_id: str, successor_id: str) -> None:
        """Explicitly link a successor as the recovery of a failed attempt."""
        pred = self._by_id.get(failed_id)
        if pred and pred.recovered_by is None:
            pred.recovered_by = successor_id

    @property
    def records(self) -> list[OperationRecord]:
        return list(self._records)

    def to_list(self) -> list[dict]:
        return [r.to_dict() for r in self._records]

    def failure_count(self) -> int:
        return sum(1 for r in self._records if r.terminal_state != "success")

    def success_count(self) -> int:
        return sum(1 for r in self._records if r.terminal_state == "success")

    def count_by_state(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self._records:
            counts[r.terminal_state] = counts.get(r.terminal_state, 0) + 1
        return counts


# --- the production client ---------------------------------------------------

class OpenTargetsClient:
    """One production client.  All network access goes through ``request``."""

    def __init__(self, url: str = DEFAULT_URL, *, budgets: dict | None = None,
                 connect_timeout: int = 15, read_timeout: int = 30,
                 ledger: OperationLedger | None = None,
                 allowed_hosts: tuple[str, ...] = ("api.platform.opentargets.org",),
                 source_mode: str = "live") -> None:
        self.url = url
        self.budgets = {**DEFAULT_BUDGETS, **(budgets or {})}
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.ledger = ledger or OperationLedger()
        self._bytes_seen = 0
        self.allowed_hosts = tuple(host.lower() for host in allowed_hosts)
        if source_mode not in {"live", "versioned_snapshot", "user_upload", "fixture"}:
            raise ValueError(f"unsupported source_mode: {source_mode!r}")
        self.source_mode = source_mode
        self._validate_url(url)

    def _validate_url(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in self.allowed_hosts:
            raise TransportError(
                f"URL is outside the HTTPS host allowlist: {parsed.scheme}://{parsed.hostname}"
            )

    def _read_bounded(self, response) -> bytes:
        """Read at most the configured response budget plus one sentinel byte."""
        remaining = self.budgets["max_bytes"] - self._bytes_seen
        if remaining < 0:
            raise BudgetExceeded({"reason": "response-byte budget already exhausted"})
        raw = response.read(remaining + 1)
        return raw

    # -- the single network helper (OT-01, OT-02) -----------------------------

    def request(self, operation: str, query: str, variables: dict | None = None,
                *, retry_of: str | None = None) -> dict:
        """Execute one GraphQL request and return ``data``.

        Raises ``TransportError`` on network/HTTP failures or malformed JSON.
        Raises ``GraphQLError`` on HTTP 200 with body-level ``errors``.
        In every case an immutable ``OperationRecord`` is appended first.
        """
        attempt_id, query_hash, start = self.ledger.begin(operation, query, variables)
        http_status: int | None = None
        graphql_errors: list[dict] | None = None
        row_count = None
        page_index = None
        cursor = None
        total_count = None
        terminal = "transport_error"
        error_message = None
        payload: dict = {}
        body = json.dumps(
            {"query": query, "variables": variables or {}},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_hash = hashlib.sha256(body).hexdigest()
        raw = b""
        response_body_state = "not_received"

        if retry_of is not None:
            predecessor = self.ledger._by_id.get(retry_of)
            if predecessor is None:
                raise ValueError(f"retry_of references unknown attempt {retry_of!r}")
            if predecessor.terminal_state == "success":
                raise ValueError("a successful operation cannot be retried")
            if predecessor.operation != operation:
                raise ValueError("a retry must preserve the operation name")

        try:
            req = urllib.request.Request(
                self.url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.read_timeout) as resp:
                    http_status = resp.status
                    final_url = resp.geturl() if hasattr(resp, "geturl") else self.url
                    self._validate_url(final_url)
                    raw = self._read_bounded(resp)
                    response_body_state = "received"
                    remaining = self.budgets["max_bytes"] - self._bytes_seen
                    if len(raw) > remaining:
                        terminal = "budget_exceeded"
                        error_message = f"response exceeds remaining byte budget ({remaining} bytes)"
                        raise BudgetExceeded({"reason": error_message})
                    self._bytes_seen += len(raw)
            except urllib.error.HTTPError as exc:
                http_status = exc.code
                remaining = self.budgets["max_bytes"] - self._bytes_seen
                raw = exc.read(max(0, remaining) + 1)
                response_body_state = "received" if raw else "empty"
                if len(raw) > remaining:
                    terminal = "budget_exceeded"
                    error_message = "HTTP error response exceeded response-byte budget"
                    raise BudgetExceeded({"reason": error_message}) from exc
                self._bytes_seen += len(raw)
                # Try to parse the error body for GraphQL errors
                try:
                    payload = json.loads(raw)
                    graphql_errors = payload.get("errors")
                except (ValueError, OSError):
                    error_message = exc.reason
                raise TransportError(f"HTTP {http_status}: {exc.reason}") from exc
            except urllib.error.URLError as exc:
                error_message = str(exc.reason)
                raise TransportError(f"URL error: {exc.reason}") from exc
            except TimeoutError as exc:
                error_message = "timeout"
                raise TransportError("timeout") from exc

            try:
                payload = json.loads(raw)
            except ValueError as exc:
                error_message = f"malformed JSON: {exc}"
                raise TransportError(error_message) from exc

            # OT-02 / OT-03: reject HTTP 200 body errors
            graphql_errors = payload.get("errors")
            if graphql_errors:
                terminal = "graphql_error"
                error_message = "; ".join(str(e.get("message", "")) for e in graphql_errors)
                record = OperationRecord(
                    attempt_id=attempt_id, operation=operation, query_hash=query_hash,
                    request_hash=request_hash,
                    response_hash=hashlib.sha256(raw).hexdigest(),
                    response_bytes=len(raw), response_body_state=response_body_state,
                    variables_redacted=_redact_variables(variables), start_utc=start,
                    end_utc=_utc_now_iso(), http_status=http_status,
                    graphql_errors=graphql_errors, row_count=row_count, page_index=page_index,
                    cursor=cursor, total_count=total_count, release=self.ledger.release,
                    terminal_state=terminal, retry_of=retry_of, recovered_by=None,
                    error_message=error_message,
                )
                self.ledger.append(record)
                raise GraphQLError(graphql_errors, operation)

            data = payload.get("data") or {}
            row_count, page_index, cursor, total_count = _extract_counts(operation, data)
            terminal = "success"
            return data

        except BudgetExceeded as exc:
            terminal = "budget_exceeded"
            error_message = str(exc)
            raise
        except (TransportError, GraphQLError):
            raise
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            terminal = "transport_error"
            raise TransportError(error_message) from exc
        finally:
            # The ledger append for the success path (error paths already appended
            # or will be appended here).  We must not double-append.
            if terminal == "success":
                record = OperationRecord(
                    attempt_id=attempt_id, operation=operation, query_hash=query_hash,
                    request_hash=request_hash,
                    response_hash=hashlib.sha256(raw).hexdigest(),
                    response_bytes=len(raw), response_body_state=response_body_state,
                    variables_redacted=_redact_variables(variables), start_utc=start,
                    end_utc=_utc_now_iso(), http_status=http_status,
                    graphql_errors=None, row_count=row_count, page_index=page_index,
                    cursor=cursor, total_count=total_count, release=self.ledger.release,
                    terminal_state=terminal, retry_of=retry_of, recovered_by=None,
                    error_message=None,
                )
                self.ledger.append(record)
            elif terminal in ("transport_error", "budget_exceeded"):
                # Only append if not already appended (GraphQLError path appends itself)
                if not self.ledger._by_id.get(attempt_id):
                    record = OperationRecord(
                        attempt_id=attempt_id, operation=operation, query_hash=query_hash,
                        request_hash=request_hash,
                        response_hash=hashlib.sha256(raw).hexdigest(),
                        response_bytes=len(raw), response_body_state=response_body_state,
                        variables_redacted=_redact_variables(variables), start_utc=start,
                        end_utc=_utc_now_iso(), http_status=http_status,
                        graphql_errors=graphql_errors, row_count=row_count, page_index=page_index,
                        cursor=cursor, total_count=total_count, release=self.ledger.release,
                        terminal_state=terminal, retry_of=retry_of, recovered_by=None,
                        error_message=error_message,
                    )
                    self.ledger.append(record)

    # -- release capture (OT-05) -----------------------------------------------

    def capture_release(self) -> dict:
        """Capture meta.apiVersion and dataVersion once at run start."""
        from queries import REGISTRY
        meta_q = REGISTRY["meta"]["query"]
        data = self.request("meta", meta_q)
        meta = data.get("meta") or {}
        release = {
            "dataVersion": meta.get("dataVersion") or {},
            "apiVersion": meta.get("apiVersion") or {},
            "product": meta.get("product"),
            "access_utc": _utc_now_iso(),
        }
        self.ledger.set_release(release)
        return release

    # -- bounded paginator (OT-06) ---------------------------------------------

    def paginate(self, operation: str, query: str, variables: dict,
                 *, page_mode: str = "index",
                 extract_path: tuple[str, ...] = (),
                 max_pages: int | None = None,
                 max_rows: int | None = None) -> dict:
        """Iterate a paginated field within budgets.

        ``page_mode`` is "index" (page:{index,size}) or "cursor" (size+cursor).
        Returns a dict with rows, counts, truncation, and completeness label.
        Raises ``BudgetExceeded`` with a typed handoff when over budget (OT-09).
        """
        mp = max_pages or self.budgets["max_pages"]
        mr = max_rows or self.budgets["max_rows"]
        all_rows: list[dict] = []
        total_count: int | None = None
        page_index = 0
        cursor = variables.get("cursor")
        truncated = False

        for page in range(mp):
            vars_page = dict(variables)
            if page_mode == "index":
                vars_page["page"] = {"index": page, "size": variables.get("size", 25)}
            elif page_mode == "cursor" and cursor:
                vars_page["cursor"] = cursor
            elif page_mode == "cursor":
                vars_page.pop("cursor", None)

            data = self.request(operation, query, vars_page)
            rows, count, next_cursor = _extract_paginated(operation, data, extract_path, page_mode)
            if total_count is None:
                total_count = count
            all_rows.extend(rows)

            if page_mode == "cursor":
                cursor = next_cursor
                if not cursor:
                    break
            else:
                if len(all_rows) >= (total_count or 0):
                    break

            if len(all_rows) >= mr:
                truncated = True
                break

        else:
            truncated = True

        exhausted = not truncated and (total_count is not None and len(all_rows) >= total_count)
        completeness = "exhaustive" if exhausted else "bounded_sample"

        if truncated and total_count and total_count > len(all_rows):
            handoff = _bulk_handoff(operation, total_count, len(all_rows), self.ledger.release)
            # Do NOT raise for a bounded sample that the caller requested; only
            # raise when the caller asked for more than the budget allows.
            # The caller decides via the request planner.

        return {
            "rows": all_rows,
            "returned": len(all_rows),
            "total_if_reported": total_count,
            "pages_fetched": page + 1,
            "truncated": truncated,
            "completeness": completeness,
            "next_cursor": cursor if page_mode == "cursor" else None,
        }

    # -- request planner (OT-09) -----------------------------------------------

    def plan_request(self, operation: str, requested_size: int,
                     *, n_entities: int = 1) -> dict:
        """Decide whether to use the API or hand off to bulk data.

        Returns a typed decision: {"route": "api"} or {"route": "bulk_handoff", ...}.
        """
        estimated_rows = requested_size * n_entities
        estimated_bytes = estimated_rows * 2000  # rough avg per row
        over = []
        if n_entities > self.budgets["max_entities"]:
            over.append("entities")
        if estimated_rows > self.budgets["max_rows"]:
            over.append("rows")
        if estimated_bytes > self.budgets["max_bytes"]:
            over.append("bytes")

        if over:
            return {
                "route": "bulk_handoff",
                "reason": f"estimated workload exceeds budget: {', '.join(over)}",
                "estimated_rows": estimated_rows,
                "estimated_bytes": estimated_bytes,
                "budgets": dict(self.budgets),
                "official_routes": [
                    "https://platform-docs.opentargets.org/data-access/datasets",
                ],
                "downstream_filtering": (
                    "Filter the bulk association/evidence parquet by disease_id and/or "
                    "ensembl_id after download; do not loop the API per entity."
                ),
                "release": self.ledger.release,
            }
        return {"route": "api", "estimated_rows": estimated_rows}


# --- helpers ------------------------------------------------------------------

def _extract_counts(operation: str, data: dict) -> tuple[int | None, int | None, str | None, int | None]:
    """Best-effort extraction of row/page/cursor/total from a response."""
    row_count = None
    page_index = None
    cursor = None
    total_count = None
    # Walk common shapes
    def _walk(d):
        nonlocal row_count, page_index, cursor, total_count
        if not isinstance(d, dict):
            return
        if "count" in d and isinstance(d["count"], int):
            total_count = d["count"]
        if "rows" in d and isinstance(d["rows"], list):
            row_count = len(d["rows"])
        if "cursor" in d and isinstance(d["cursor"], str):
            cursor = d["cursor"]
    _walk(data)
    # Recurse one level
    for v in data.values():
        if isinstance(v, dict):
            _walk(v)
            for vv in v.values():
                if isinstance(vv, dict):
                    _walk(vv)
    return row_count, page_index, cursor, total_count


def _extract_paginated(operation: str, data: dict, extract_path: tuple[str, ...],
                       page_mode: str) -> tuple[list[dict], int | None, str | None]:
    """Extract rows, count, and next cursor from a paginated response."""
    node = data
    for key in extract_path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            break
    if not isinstance(node, dict):
        return [], None, None
    rows = node.get("rows") or []
    count = node.get("count")
    cursor = node.get("cursor") if page_mode == "cursor" else None
    return rows, count, cursor


def _bulk_handoff(operation: str, total: int, fetched: int, release: dict | None) -> dict:
    return {
        "route": "bulk_handoff",
        "reason": f"requested {total} rows for {operation}; exceeds API budget ({fetched} fetched)",
        "total": total,
        "fetched": fetched,
        "official_routes": [
            "https://platform-docs.opentargets.org/data-access/datasets",
        ],
        "downstream_filtering": (
            "Download the association or evidence parquet from the Open Targets FTP/AWS "
            "Open Data bucket and filter by disease_id / ensembl_id locally."
        ),
        "release": release,
    }


def save_ledger(ledger: OperationLedger, path: str, *, source_mode: str) -> str:
    """Persist the operation ledger as JSON."""
    out = {
        "schema": "phylo-ot-operation-ledger/2",
        "source_mode": source_mode,
        "release": ledger.release,
        "summary": {
            "total_attempts": len(ledger.records),
            "successes": ledger.success_count(),
            "failures": ledger.failure_count(),
            "by_state": ledger.count_by_state(),
        },
        "operations": ledger.to_list(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, sort_keys=True)
    return path
