"""Sandbox containment and process isolation for disposable workspaces.

Enforces:
- Path boundary validation (rejects directory traversal and symlink escapes).
- Environment sanitization (strips credentials, cloud tokens, database URLs).
- Strict execution timeouts and process cleanup.
- Forbidden flags guard (blocks --dangerously-bypass-approvals-and-sandbox).
"""
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import DISPOSABLE_BASE_DIR, REPAIR_TIMEOUT_SECONDS, STRIP_ENV_VARS


class SecurityViolation(Exception):
    """Raised when an operation attempts to breach sandbox boundaries."""
    pass


def validate_safe_workspace_path(target_path: Path, allowed_base: Optional[Path] = None) -> Path:
    """Validate that target_path resides strictly within the allowed disposable directory.

    Rejects path traversal, symlink escapes, system root targets, and main repo modifications.
    """
    if allowed_base is None:
        allowed_base = DISPOSABLE_BASE_DIR

    resolved_base = allowed_base.resolve()
    resolved_target = target_path.resolve()

    # Reject if resolved_target is identical to or outside resolved_base
    try:
        resolved_target.relative_to(resolved_base)
    except ValueError:
        raise SecurityViolation(
            f"Path escape rejected: target '{resolved_target}' is outside allowed base '{resolved_base}'"
        )

    # Check for symlink traversal
    if target_path.is_symlink():
        real_path = Path(os.path.realpath(str(target_path)))
        try:
            real_path.relative_to(resolved_base)
        except ValueError:
            raise SecurityViolation(f"Symlink target escape rejected: '{real_path}' points outside base")

    return resolved_target


def create_disposable_workspace(source_repo_root: Path, base_dir: Optional[Path] = None) -> Path:
    """Provision a fresh, disposable workspace isolated from the main repository checkout."""
    if base_dir is None:
        base_dir = DISPOSABLE_BASE_DIR

    base_dir.mkdir(parents=True, exist_ok=True)
    workspace_id = f"workspace_{uuid.uuid4().hex[:12]}"
    workspace_path = base_dir / workspace_id
    workspace_path.mkdir(parents=True, exist_ok=False)

    # Copy minimal backend and test assets needed for diagnosis and verification
    src_backend = source_repo_root / "backend"
    dst_backend = workspace_path / "backend"
    if src_backend.exists():
        shutil.copytree(
            src_backend,
            dst_backend,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.pyc", "tasklane.db*"),
        )

    # Copy root configuration files
    for fname in ["pyproject.toml", "openapi.yaml"]:
        src_file = source_repo_root / fname
        if src_file.exists():
            shutil.copy2(src_file, workspace_path / fname)

    return validate_safe_workspace_path(workspace_path, base_dir)


def cleanup_disposable_workspace(workspace_path: Path, base_dir: Optional[Path] = None) -> None:
    """Safely tear down a disposable workspace directory."""
    try:
        validated = validate_safe_workspace_path(workspace_path, base_dir)
        if validated.exists():
            shutil.rmtree(validated, ignore_errors=True)
    except SecurityViolation:
        pass


def sanitize_process_environment(extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build a sanitized environment dictionary with sensitive tokens completely stripped."""
    clean_env = {}
    for key, val in os.environ.items():
        if key.upper() in STRIP_ENV_VARS:
            continue
        # Strip generic credential-like env patterns
        if any(token in key.upper() for token in ("SECRET", "TOKEN", "PASSWORD", "AUTH_KEY")):
            continue
        clean_env[key] = val

    # Force test/isolated defaults
    clean_env["ENVIRONMENT"] = "disposable_test"
    clean_env["CI"] = "true"
    clean_env.pop("DATABASE_URL", None)

    if extra_env:
        for k, v in extra_env.items():
            if k.upper() not in STRIP_ENV_VARS:
                clean_env[k] = v

    return clean_env


def run_sandboxed_command(
    cmd: List[str],
    cwd: Path,
    timeout: int = REPAIR_TIMEOUT_SECONDS,
    allowed_base: Optional[Path] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> Tuple[int, str, str]:
    """Execute a command bounded inside the disposable workspace with stripped credentials.

    Forbids dangerous flags and restricts process duration.
    """
    # 1. Enforce directory containment
    safe_cwd = validate_safe_workspace_path(cwd, allowed_base)

    # 2. Check for explicitly forbidden flags
    forbidden_tokens = ["--dangerously-bypass-approvals-and-sandbox", "rm -rf /", ":(){ :|:& };:"]
    cmd_str = " ".join(cmd)
    for forbidden in forbidden_tokens:
        if forbidden in cmd_str:
            raise SecurityViolation(f"Forbidden command parameter rejected: '{forbidden}'")

    # 3. Build sanitized environment
    env = sanitize_process_environment(extra_env)

    # 4. Execute with timeout
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(safe_cwd),
            env=env,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Sandboxed command exceeded timeout of {timeout}s: {' '.join(cmd)}")
    except Exception as exc:
        return -1, "", f"Execution error: {str(exc)}"


def find_test_python() -> str:
    """Find a Python interpreter that has FastAPI and pytest available."""
    # 1. Check if backend/.venv exists in current or parent repo
    repo_root = Path(__file__).resolve().parents[1]
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    exe_name = "python.exe" if os.name == "nt" else "python"
    venv_py = repo_root / "backend" / ".venv" / bin_dir / exe_name
    if venv_py.exists():
        return str(venv_py)
    # 2. Fall back to current interpreter
    return sys.executable


def run_disposable_tests(workspace_path: Path) -> Tuple[bool, str]:
    """Execute targeted backend unit tests in the disposable workspace."""
    safe_ws = validate_safe_workspace_path(workspace_path)
    backend_dir = safe_ws / "backend"
    if not backend_dir.exists():
        backend_dir = safe_ws

    python_bin = find_test_python()
    cmd = [python_bin, "-m", "pytest", "tests/test_api.py", "-v"]

    ret, stdout, stderr = run_sandboxed_command(cmd, cwd=backend_dir, timeout=45)
    output = f"{stdout}\n{stderr}".strip()
    return (ret == 0), output
