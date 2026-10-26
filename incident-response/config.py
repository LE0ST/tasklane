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
LOKI_SERVICE_NAME = os.environ.get("LOKI_SERVICE_NAME", "tasklane-api")

# Alert filtering
EXPECTED_ALERT_NAME = "TaskLane5xxRate"
MAX_PAYLOAD_BYTES = 65536   # 64 KB max alert webhook payload
MAX_OUTPUT_BYTES = 65536    # 64 KB max subprocess capture buffer

# Timeouts and limits
DIAGNOSIS_TIMEOUT_SECONDS = int(os.environ.get("DIAGNOSIS_TIMEOUT_SECONDS", "30"))
REPAIR_TIMEOUT_SECONDS = int(os.environ.get("REPAIR_TIMEOUT_SECONDS", "60"))
COLLECTOR_TIMEOUT_SECONDS = float(os.environ.get("COLLECTOR_TIMEOUT_SECONDS", "2.0"))

# Private service-owned disposable directory
_configured_base = os.environ.get("TASKLANE_DISPOSABLE_BASE_DIR")
if _configured_base:
    DISPOSABLE_SERVICE_DIR = Path(_configured_base).resolve()
else:
    DISPOSABLE_SERVICE_DIR = Path(tempfile.gettempdir()).resolve() / "tasklane_oncall_service"

# Strict minimal environment variable allowlist for isolated child processes.
# Explicitly rejects AWS, database URLs, tokens, HOME, PYTHONPATH, etc.
SAFE_ENV_ALLOWLIST = {
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "LANG",
    "LC_ALL",
}

# Credential patterns to purge if encountered in extra_env or parameters
FORBIDDEN_ENV_PATTERNS = {
    "AWS",
    "DATABASE",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "KEY",
    "AUTH",
    "PYTHONPATH",
    "HOME",
    "RENDER",
    "NEON",
    "GITHUB",
    "GH",
}
