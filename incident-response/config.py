"""Configuration and constants for TaskLane Issue #4 Incident Response."""
import os
import tempfile
from pathlib import Path

# Server binding
RESPONDER_PORT = int(os.environ.get("RESPONDER_PORT", "8001"))
RESPONDER_HOST = os.environ.get("RESPONDER_HOST", "0.0.0.0")

# Security policy: network-facing repair is disabled by default in normal service mode.
# Can be enabled explicitly for local isolated drill/testing via TASKLANE_ALLOW_HTTP_REPAIR=true.
ALLOW_HTTP_REPAIR = os.environ.get("TASKLANE_ALLOW_HTTP_REPAIR", "false").lower() == "true"

# Observability endpoints (local stack)
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090")
LOKI_URL = os.environ.get("LOKI_URL", "http://127.0.0.1:3100")
TEMPO_URL = os.environ.get("TEMPO_URL", "http://127.0.0.1:3200")
LOKI_SERVICE_NAME = os.environ.get("LOKI_SERVICE_NAME", "tasklane-api")

# Alert filtering
EXPECTED_ALERT_NAME = "TaskLane5xxRate"
MAX_PAYLOAD_BYTES = 65536   # 64 KB max alert webhook payload
MAX_OUTPUT_BYTES = 65536    # 64 KB max subprocess capture buffer
MAX_DIFF_BYTES = int(os.environ.get("MAX_DIFF_BYTES", "16384"))  # 16 KB max model diff

# Model settings
DEEPSEEK_API_URL = os.environ.get("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
ALLOWED_DEEPSEEK_HOSTS = {"api.deepseek.com"}

# Fixed sanitized error categories (no raw remote text, URLs, or exception interpolations)
ERROR_UPSTREAM_HTTP = "upstream_http_error"
ERROR_UPSTREAM_CONNECTION = "upstream_connection_error"
ERROR_UPSTREAM_TIMEOUT = "upstream_timeout"
ERROR_UPSTREAM_PROTOCOL = "upstream_protocol_error"
ERROR_UPSTREAM_PAYLOAD_TOO_LARGE = "upstream_payload_too_large"
ERROR_UPSTREAM_INVALID_RESPONSE = "upstream_invalid_response"
ERROR_INSECURE_API_SCHEME = "insecure_api_scheme"
ERROR_UNAUTHORIZED_API_HOST = "unauthorized_api_host"
ERROR_API_KEY_MISSING = "api_key_missing"
ERROR_BLOCKED_MISSING_ISOLATED_WORKER = "blocked_missing_isolated_worker"
ERROR_DIFF_VALIDATION_FAILED = "diff_validation_failed"
ERROR_PATCH_APPLICATION_FAILED = "patch_application_failed"
ERROR_TEST_VERIFICATION_FAILED = "test_verification_failed"

# Timeouts and limits
DIAGNOSIS_TIMEOUT_SECONDS = int(os.environ.get("DIAGNOSIS_TIMEOUT_SECONDS", "30"))
REPAIR_TIMEOUT_SECONDS = int(os.environ.get("REPAIR_TIMEOUT_SECONDS", "60"))
COLLECTOR_TIMEOUT_SECONDS = float(os.environ.get("COLLECTOR_TIMEOUT_SECONDS", "2.0"))

# Bounded resource retention
MAX_INCIDENT_HISTORY = 10
MAX_WORKSPACES_RETAINED = 3
TOKEN_EXPIRATION_SECONDS = 300  # 5 minutes

# Private service-owned disposable directory
_configured_base = os.environ.get("TASKLANE_DISPOSABLE_BASE_DIR")
if _configured_base:
    DISPOSABLE_SERVICE_DIR = Path(_configured_base).resolve()
else:
    DISPOSABLE_SERVICE_DIR = Path(tempfile.gettempdir()).resolve() / "tasklane_oncall_service"

# Strict minimal environment variable allowlist for child processes.
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

# Credential patterns to purge if encountered
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
    "DEEPSEEK",
}

# Unsafe environment override keys that must be rejected if supplied in extra_env
UNSAFE_ENV_OVERRIDE_KEYS = {
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTEST_ADDOPTS",
    "PYTHONSTARTUP",
    "PYTHONBREAKPOINT",
    "PYTHONEXECUTABLE",
    "PYTHONPATH",
    "BASH_ENV",
    "SHELLOPTS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
}

# Explicit positive file manifest for disposable workspaces.
# Strictly excludes conftest.py, .env, credentials, unrelated tests, and binaries.
DISPOSABLE_WORKSPACE_MANIFEST = [
    Path("backend/main.py"),
    Path("backend/models.py"),
    Path("backend/schemas.py"),
    Path("backend/database.py"),
    Path("backend/telemetry.py"),
    Path("backend/incident_drill.py"),
    Path("backend/alembic.ini"),
    Path("backend/alembic/env.py"),
    Path("backend/alembic/script.py.mako"),
    Path("backend/alembic/versions/1ea1353bfe87_create_tasks_table.py"),
    Path("backend/pyproject.toml"),
    Path("backend/tests/test_api.py"),
    Path("pyproject.toml"),
]
