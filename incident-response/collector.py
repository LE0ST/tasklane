"""Bounded and sanitized incident telemetry collector.

Queries Prometheus, Loki, and Tempo to gather diagnostic context for an alert.
Redacts credentials, tokens, and database connection strings.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from config import COLLECTOR_TIMEOUT_SECONDS

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


def extract_trace_id_from_log(log_line: str) -> Optional[str]:
    """Extract a 32-character hex trace ID from a log line."""
    if not log_line:
        return None
    match = TRACE_ID_PATTERN.search(log_line)
    if match:
        return match.group(1).lower()
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


def query_loki_recent_errors(loki_url: str, service: str = "tasklane-api", limit: int = 5) -> List[str]:
    """Query Loki for recent 5xx error logs for the specified service."""
    query = f'{{service="{service}"}} |= "50"'
    encoded = urllib.parse.quote(query)
    url = f"{loki_url.rstrip('/')}/loki/api/v1/query_range?query={encoded}&limit={limit}"
    data = fetch_json(url)
    if not data or data.get("status") != "success":
        return []
    results = data.get("data", {}).get("result", [])
    logs: List[str] = []
    for stream in results:
        values = stream.get("values", [])
        for entry in values:
            if isinstance(entry, list) and len(entry) >= 2:
                raw_line = entry[1]
                logs.append(sanitize_text(raw_line))
    return logs


def query_tempo_span_summary(tempo_url: str, trace_id: str) -> Optional[Dict[str, Any]]:
    """Fetch span metadata from Tempo for the given trace ID without body payloads."""
    if not trace_id:
        return None
    url = f"{tempo_url.rstrip('/')}/api/traces/{trace_id}"
    data = fetch_json(url)
    if not data:
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
                # Found candidate span
                span_name = span.get("name", "")
                status_code = span.get("status", {}).get("code")
                return {
                    "trace_id": trace_id,
                    "span_name": span_name,
                    "status_code": status_code,
                    "kind": span.get("kind"),
                }
    return None


def collect_incident_context(
    prom_url: str,
    loki_url: str,
    tempo_url: str,
    alert_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Aggregate bounded, sanitized incident context across Prometheus, Loki, and Tempo."""
    alertname = "Unknown"
    alerts = alert_payload.get("alerts", [])
    if alerts and isinstance(alerts[0], dict):
        alertname = alerts[0].get("labels", {}).get("alertname", "Unknown")

    # 1. Prometheus metric error rate
    error_count = query_prometheus_metric(prom_url, "tasklane_task_5xx_errors_total")
    rolling_rate = query_prometheus_metric(prom_url, "sum(increase(tasklane_task_5xx_errors_total[2m]))")

    # 2. Loki logs and trace ID correlation
    recent_logs = query_loki_recent_errors(loki_url, limit=5)
    correlated_trace_id = None
    for log_line in recent_logs:
        tid = extract_trace_id_from_log(log_line)
        if tid:
            correlated_trace_id = tid
            break

    # 3. Tempo trace metadata
    tempo_meta = None
    if correlated_trace_id:
        tempo_meta = query_tempo_span_summary(tempo_url, correlated_trace_id)

    return {
        "alertname": alertname,
        "error_count": error_count,
        "rolling_5xx_rate": rolling_rate,
        "recent_logs": recent_logs,
        "trace_id": correlated_trace_id,
        "tempo_summary": tempo_meta,
        "sanitized": True,
    }
