"""Configuration and constants for TaskLane Issue #4 Incident Response."""
import os
import tempfile
from pathlib import Path

# Server binding
RESPONDER_PORT = int(os.environ.get("RESPONDER_PORT", "8001"))
RESPONDER_HOST = os.environ.get("RESPONDER_HOST", "0.0.0.0")

# Observability endpoints (local stack)
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090")
LOKI_URL = os.environ.get("LOKI_URL", "http://127.0.0.1:3100")
TEMPO_URL = os.environ.get("TEMPO_URL", "http://127.0.0.1:3200")

# Alert filtering
EXPECTED_ALERT_NAME = "TaskLane5xxRate"
MAX_PAYLOAD_BYTES = 65536  # 64 KB

# Timeouts and limits
DIAGNOSIS_TIMEOUT_SECONDS = int(os.environ.get("DIAGNOSIS_TIMEOUT_SECONDS", "30"))
REPAIR_TIMEOUT_SECONDS = int(os.environ.get("REPAIR_TIMEOUT_SECONDS", "60"))
COLLECTOR_TIMEOUT_SECONDS = float(os.environ.get("COLLECTOR_TIMEOUT_SECONDS", "1.0"))

# Disposable workspace containment
_configured_base = os.environ.get("TASKLANE_DISPOSABLE_BASE_DIR")
if _configured_base:
    DISPOSABLE_BASE_DIR = Path(_configured_base).resolve()
else:
    DISPOSABLE_BASE_DIR = Path(tempfile.gettempdir()).resolve()

# Sensitive credentials that must be purged before invoking any subagent or process
STRIP_ENV_VARS = {
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "DATABASE_URL",
    "RENDER_API_KEY",
    "NEON_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
}
