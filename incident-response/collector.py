"""Bounded and sanitized incident telemetry collector.

Queries Prometheus, Loki, and Tempo to gather diagnostic context for an alert.
Enforces:
- Structured log parsing from Loki using {service_name="tasklane-api"}.
- Route-specific filtering for /api/tasks with status code 500-599.
- Trace ID extraction and normalization (32-character hex).
- Tempo SERVER kind, ERROR status, and HTTP 5xx metadata validation.
- Explicit detection of missing evidence (never silently claiming success).
- Credential and token redaction from diagnostic outputs.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from config import COLLECTOR_TIMEOUT_SECONDS, LOKI_SERVICE_NAME

# Regular expressions for credential sanitization
CREDENTIAL_PATTERNS = [
    (re.compile(r"postgres(ql)?://[^\s@]+@[^\s/]+/[^\s]+", re.IGNORECASE), "postgresql://[REDACTED]@[REDACTED]/[REDACTED]"),
    (re.compile(r"(bearer\s+)[a-zA-Z0-9_\-\.]{15,}", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(password[\"']?\s*[:=]\s*[\"']?)[^\"'\s,]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(secret[\"']?\s*[:=]\s*[\"']?)[^\"'\s,]+", re.IGNORECASE), r"\1[REDACTED]"),
]

# Regex for extracting 32-character hex trace ID
TRACE_ID_PATTERN = re.compile(r"(?:trace_id[=\":\s]+|trace[=\":\s]+)([0-9a-fA-F]{32})")


def sanitize_text(text: str) -> str:
    """Sanitize sensitive credentials and tokens from text."""
    if not text:
        return ""
    sanitized = text
    for pattern, replacement in CREDENTIAL_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def normalize_trace_id(val: Any) -> Optional[str]:
    """Normalize hex or base64 binary trace ID into a 32-character lowercase hex string."""
    if not val:
        return None
    s = str(val).strip()
    if len(s) == 32 and all(c in "0123456789abcdefABCDEF" for c in s):
        clean = s.lower()
        if clean != "0" * 32:
            return clean
    try:
        import base64
        raw = base64.b64decode(s)
        h = raw.hex()
        if len(h) == 32 and h != "0" * 32:
            return h.lower()
    except Exception:
        pass
    return None


def fetch_json(url: str, timeout: float = COLLECTOR_TIMEOUT_SECONDS) -> Optional[Dict[str, Any]]:
    """Safely fetch JSON from a URL with timeout and exception containment."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None


def query_prometheus_metric(prom_url: str, metric_query: str) -> Optional[float]:
    """Query Prometheus for a single scalar or instant vector metric value."""
    encoded = urllib.parse.quote(metric_query)
    url = f"{prom_url.rstrip('/')}/api/v1/query?query={encoded}"
    data = fetch_json(url)
    if not data or data.get("status") != "success":
        return None
    results = data.get("data", {}).get("result", [])
    if not results:
        return None
    try:
        val = results[0].get("value", [None, None])[1]
        return float(val) if val is not None else None
    except (IndexError, ValueError, TypeError):
        return None


def query_loki_task_5xx_logs(
    loki_url: str,
    service_name: str = LOKI_SERVICE_NAME,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Query Loki using {service_name=...} and parse structured JSON logs for task HTTP 5xx responses.
    
    Filters for:
    - Service name matching service_name (tasklane-api).
    - Route matching /api/tasks or containing /tasks.
    - HTTP status code in range 500-599.
    - Valid 32-hex trace ID.
    """
    query = f'{{service_name="{service_name}"}}'
    encoded = urllib.parse.quote(query)
    url = f"{loki_url.rstrip('/')}/loki/api/v1/query_range?query={encoded}&limit={limit}"
    data = fetch_json(url)
    if not data or data.get("status") != "success":
        return []

    results = data.get("data", {}).get("result", [])
    matched_events: List[Dict[str, Any]] = []

    for stream in results:
        values = stream.get("values", [])
        for entry in values:
            if not isinstance(entry, list) or len(entry) < 2:
                continue
            raw_line = entry[1]
            try:
                event = json.loads(raw_line)
            except (ValueError, TypeError):
                continue

            if not isinstance(event, dict):
                continue

            # Verify route relates to tasks
            route = str(event.get("route", ""))
            if "/tasks" not in route:
                continue

            # Verify status code is 5xx
            try:
                status_code = int(event.get("status_code", 0))
            except (ValueError, TypeError):
                continue

            if not (500 <= status_code <= 599):
                continue

            # Extract trace ID
            tid = normalize_trace_id(event.get("trace_id"))
            if not tid:
                # Fallback to regex
                match = TRACE_ID_PATTERN.search(raw_line)
                if match:
                    tid = normalize_trace_id(match.group(1))

            sanitized_event = {
                "route": route,
                "method": event.get("method"),
                "status_code": status_code,
                "trace_id": tid,
                "duration_ms": event.get("duration_ms"),
            }
            matched_events.append(sanitized_event)

    return matched_events


def query_tempo_span_summary(
    tempo_url: str,
    trace_id: str,
    expected_status_range: Tuple[int, int] = (500, 599),
) -> Optional[Dict[str, Any]]:
    """Fetch and validate span metadata from Tempo for the given trace ID.
    
    Requires:
    - SERVER span kind (2 or SPAN_KIND_SERVER).
    - ERROR status (code 2 or STATUS_CODE_ERROR).
    - Matching HTTP 5xx response status code attribute.
    """
    clean_tid = normalize_trace_id(trace_id)
    if not clean_tid:
        return None

    url = f"{tempo_url.rstrip('/')}/api/traces/{clean_tid}"
    data = fetch_json(url)
    if not data or not isinstance(data, dict):
        return None

    batches = data.get("batches") or data.get("resourceSpans")
    if isinstance(data.get("trace"), dict):
        batches = data["trace"].get("resourceSpans") or data["trace"].get("batches") or batches
    if not isinstance(batches, list):
        return None

    for batch in batches:
        if not isinstance(batch, dict):
            continue
        scope_spans = batch.get("scopeSpans") or batch.get("instrumentationLibrarySpans") or []
        for scope_span in scope_spans:
            if not isinstance(scope_span, dict):
                continue
            for span in scope_span.get("spans", []):
                if not isinstance(span, dict):
                    continue

                # 1. Trace ID check
                span_tid = normalize_trace_id(span.get("traceId"))
                if span_tid != clean_tid:
                    continue

                # 2. Kind check: SERVER (2)
                kind = span.get("kind")
                if kind not in (2, "2", "SPAN_KIND_SERVER", "server", "Server"):
                    continue

                # 3. Status check: ERROR (code 2)
                status_obj = span.get("status", {})
                code = status_obj.get("code") if isinstance(status_obj, dict) else None
                if code not in (2, "2", "STATUS_CODE_ERROR", "ERROR", "error"):
                    continue

                # 4. HTTP response status code check
                matched_status = None
                attrs = span.get("attributes", [])
                if isinstance(attrs, list):
                    for attr in attrs:
                        if not isinstance(attr, dict):
                            continue
                        if attr.get("key") in ("http.response.status_code", "http.status_code"):
                            val_obj = attr.get("value", {})
                            val = val_obj.get("intValue") or val_obj.get("value") if isinstance(val_obj, dict) else val_obj
                            try:
                                num = int(val)
                                if expected_status_range[0] <= num <= expected_status_range[1]:
                                    matched_status = num
                                    break
                            except (ValueError, TypeError):
                                pass
                elif isinstance(attrs, dict):
                    val = attrs.get("http.response.status_code") or attrs.get("http.status_code")
                    try:
                        num = int(val)
                        if expected_status_range[0] <= num <= expected_status_range[1]:
                            matched_status = num
                    except (ValueError, TypeError):
                        pass

                if matched_status is not None:
                    return {
                        "trace_id": clean_tid,
                        "span_name": span.get("name", ""),
                        "status_code": code,
                        "http_status": matched_status,
                        "kind": kind,
                    }

    return None


def collect_incident_context(
    prom_url: str,
    loki_url: str,
    tempo_url: str,
    alert_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Aggregate bounded, sanitized incident context across Prometheus, Loki, and Tempo.
    
    Explicitly tracks missing evidence: if Loki task 5xx logs or Tempo error spans are missing,
    has_correlated_evidence is set to False with missing_evidence listed.
    """
    alertname = "Unknown"
    alerts = alert_payload.get("alerts", [])
    if alerts and isinstance(alerts[0], dict):
        alertname = alerts[0].get("labels", {}).get("alertname", "Unknown")

    # 1. Prometheus metric error rate
    error_count = query_prometheus_metric(prom_url, "tasklane_task_5xx_errors_total")
    rolling_rate = query_prometheus_metric(prom_url, "sum(increase(tasklane_task_5xx_errors_total[2m]))")

    # 2. Loki structured logs for task 5xx
    task_5xx_events = query_loki_task_5xx_logs(loki_url, service_name=LOKI_SERVICE_NAME, limit=50)

    correlated_trace_id = None
    for event in task_5xx_events:
        tid = event.get("trace_id")
        if tid:
            correlated_trace_id = tid
            break

    # 3. Tempo trace metadata validation
    tempo_meta = None
    if correlated_trace_id:
        tempo_meta = query_tempo_span_summary(tempo_url, correlated_trace_id)

    # 4. Rigorous correlation evidence verification
    missing_evidence = []
    if error_count is None or error_count <= 0:
        missing_evidence.append("prometheus_task_5xx_metric")
    if not task_5xx_events or not correlated_trace_id:
        missing_evidence.append("loki_structured_task_5xx_log")
    if not tempo_meta:
        missing_evidence.append("tempo_server_error_span")

    has_correlated_evidence = len(missing_evidence) == 0

    return {
        "alertname": alertname,
        "error_count": error_count,
        "rolling_5xx_rate": rolling_rate,
        "task_5xx_events": task_5xx_events[:5],  # bounded to 5 events
        "trace_id": correlated_trace_id,
        "tempo_summary": tempo_meta,
        "has_correlated_evidence": has_correlated_evidence,
        "missing_evidence": missing_evidence,
        "sanitized": True,
    }
