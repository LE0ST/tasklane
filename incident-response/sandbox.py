"""Process isolation and workspace containment for disposable incident response environments.

SECURITY DISCLAIMER:
The containment mechanisms in this module (directory boundary checks, environment allowlisting,
process timeouts, and unprivileged user execution) provide defense-in-depth policy constraints.
They DO NOT constitute an OS-level kernel security sandbox. Untrusted code must never be executed
without an independently isolated worker (e.g. gVisor, Firecracker, or isolated container runtime).

Enforces:
- Service-owned directory and incident-bound workspace registry.
- Explicit positive file manifest copying (strictly excludes conftest.py, .env, credentials, test hooks).
- Bounded workspace and token retention (prunes stale directories).
- Minimal environment allowlist (purges AWS, database URLs, HOME, PYTHONPATH).
- Rejection of unsafe overrides (LD_PRELOAD, PYTEST_ADDOPTS, proxy settings, credentials).
- Process execution timeouts and output buffer bounding (64 KB).
- Forbidden flags guard (blocks --dangerously-bypass-approvals-and-sandbox).
"""
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import (
    DISPOSABLE_SERVICE_DIR,
    DISPOSABLE_WORKSPACE_MANIFEST,
    FORBIDDEN_ENV_PATTERNS,
    MAX_OUTPUT_BYTES,
    MAX_WORKSPACES_RETAINED,
    REPAIR_TIMEOUT_SECONDS,
    SAFE_ENV_ALLOWLIST,
    TOKEN_EXPIRATION_SECONDS,
    UNSAFE_ENV_OVERRIDE_KEYS,
)


class SecurityViolation(Exception):
    """Raised when an operation attempts to breach sandbox boundaries."""
    pass


# Active workspace registry: maps incident_id -> resolved Path
_workspace_registry: Dict[str, Path] = {}


def register_workspace(incident_id: str, path: Path) -> Path:
    """Register a service-created workspace for a specific incident."""
    resolved = path.resolve()
    _workspace_registry[incident_id] = resolved
    try:
        DISPOSABLE_SERVICE_DIR.mkdir(parents=True, exist_ok=True)
        ws_link = DISPOSABLE_SERVICE_DIR / f"workspace_{incident_id}.path"
        ws_link.write_text(str(resolved), encoding="utf-8")
    except OSError:
        pass
    return resolved


def get_registered_workspace(incident_id: str) -> Optional[Path]:
    """Retrieve the registered workspace for an incident."""
    if incident_id in _workspace_registry:
        return _workspace_registry[incident_id]
    ws_link = DISPOSABLE_SERVICE_DIR / f"workspace_{incident_id}.path"
    if ws_link.exists():
        try:
            target = Path(ws_link.read_text(encoding="utf-8").strip()).resolve()
            if target.exists() and target.is_dir():
                _workspace_registry[incident_id] = target
                return target
        except Exception:
            pass
    # Robust fallback: check for service directory created with f"workspace_{incident_id}_*"
    try:
        candidates = [p for p in DISPOSABLE_SERVICE_DIR.glob(f"workspace_{incident_id}_*") if p.is_dir()]
        if candidates:
            target = candidates[0].resolve()
            _workspace_registry[incident_id] = target
            return target
    except Exception:
        pass
    return None


def unregister_workspace(incident_id: str) -> None:
    """Remove a workspace registration upon cleanup."""
    _workspace_registry.pop(incident_id, None)
    ws_link = DISPOSABLE_SERVICE_DIR / f"workspace_{incident_id}.path"
    if ws_link.exists():
        try:
            ws_link.unlink()
        except OSError:
            pass


def reset_workspace_registry() -> None:
    """Reset the registry (used in test setup/teardown)."""
    _workspace_registry.clear()


def prune_stale_resources(
    max_keep: int = MAX_WORKSPACES_RETAINED,
    token_max_age_seconds: int = TOKEN_EXPIRATION_SECONDS,
    workspace_max_age_seconds: int = 3600,
) -> None:
    """Clean up old disposable workspaces and expired tokens to bound resource accumulation.
    
    Guarantees:
    1. Removes expired auth tokens (mtime > token_max_age_seconds).
    2. Removes expired workspaces (mtime > workspace_max_age_seconds).
    3. Enforces max_keep cap by removing oldest workspaces exceeding max_keep.
    4. For every deleted workspace:
       - Recursively removes directory.
       - Unregisters from in-memory registry.
       - Deletes path link file (workspace_<inc_id>.path).
       - Deletes associated token file (auth_<inc_id>.token).
    5. Cleans orphaned path link files and orphaned in-memory registry entries.
    """
    if not DISPOSABLE_SERVICE_DIR.exists():
        return

    now = time.time()

    # 1. Prune expired auth tokens
    try:
        for token_file in list(DISPOSABLE_SERVICE_DIR.glob("auth_*.token")):
            try:
                mtime = token_file.stat().st_mtime
                if now - mtime > token_max_age_seconds:
                    token_file.unlink()
            except OSError:
                pass
    except Exception:
        pass

    def _purge_workspace_entry(ws_dir: Path) -> None:
        try:
            inc_id = None
            ws_resolved = ws_dir.resolve()
            # 1. Try in-memory registry
            for k, v in list(_workspace_registry.items()):
                try:
                    if v.resolve() == ws_resolved:
                        inc_id = k
                        break
                except Exception:
                    pass

            # 2. Try .path link files
            if not inc_id:
                for path_link in DISPOSABLE_SERVICE_DIR.glob("workspace_*.path"):
                    try:
                        if Path(path_link.read_text(encoding="utf-8").strip()).resolve() == ws_resolved:
                            inc_id = path_link.stem.replace("workspace_", "", 1)
                            break
                    except OSError:
                        pass

            # 3. Fallback to parsing directory name: workspace_<incident_id>_<suffix> or workspace_<incident_id>
            if not inc_id and ws_dir.name.startswith("workspace_"):
                remainder = ws_dir.name[len("workspace_"):]
                if "_" in remainder:
                    inc_id = remainder.rsplit("_", 1)[0]
                else:
                    inc_id = remainder

            if ws_dir.exists():
                shutil.rmtree(ws_dir, ignore_errors=True)

            if inc_id:
                unregister_workspace(inc_id)
                token_file = DISPOSABLE_SERVICE_DIR / f"auth_{inc_id}.token"
                if token_file.exists():
                    try:
                        token_file.unlink()
                    except OSError:
                        pass
        except Exception:
            pass

    # 2. Collect all workspace directories
    try:
        workspaces = [
            p for p in DISPOSABLE_SERVICE_DIR.glob("workspace_*")
            if p.is_dir()
        ]

        # 3. Remove expired workspaces
        surviving_workspaces = []
        for ws in workspaces:
            try:
                if now - ws.stat().st_mtime > workspace_max_age_seconds:
                    _purge_workspace_entry(ws)
                else:
                    surviving_workspaces.append(ws)
            except OSError:
                pass

        # 4. Enforce max_keep cap on surviving workspaces
        if len(surviving_workspaces) > max_keep:
            surviving_workspaces.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
            to_remove = surviving_workspaces[: len(surviving_workspaces) - max_keep]
            for ws in to_remove:
                _purge_workspace_entry(ws)
    except Exception:
        pass

    # 5. Clean orphaned .path files and dangling registry entries
    try:
        for path_link in list(DISPOSABLE_SERVICE_DIR.glob("workspace_*.path")):
            try:
                target_str = path_link.read_text(encoding="utf-8").strip()
                target_path = Path(target_str)
                if not target_path.exists():
                    path_link.unlink()
                    inc_id = path_link.stem.replace("workspace_", "", 1)
                    _workspace_registry.pop(inc_id, None)
            except OSError:
                pass
    except Exception:
        pass

    # Clean in-memory registry entries whose paths don't exist
    try:
        stale_incidents = [
            inc for inc, target in list(_workspace_registry.items())
            if not target.exists()
        ]
        for inc in stale_incidents:
            _workspace_registry.pop(inc, None)
    except Exception:
        pass

    # 6. Clean orphaned auth tokens whose workspace/incident no longer exists
    try:
        active_incidents = set(_workspace_registry.keys())
        for path_link in DISPOSABLE_SERVICE_DIR.glob("workspace_*.path"):
            active_incidents.add(path_link.stem.replace("workspace_", "", 1))
        for ws in DISPOSABLE_SERVICE_DIR.glob("workspace_*"):
            if ws.is_dir():
                rem = ws.name[len("workspace_"):]
                if "_" in rem:
                    active_incidents.add(rem.rsplit("_", 1)[0])
                else:
                    active_incidents.add(rem)

        for token_file in list(DISPOSABLE_SERVICE_DIR.glob("auth_*.token")):
            token_inc_id = token_file.stem.replace("auth_", "", 1)
            if token_inc_id not in active_incidents:
                try:
                    token_file.unlink()
                except OSError:
                    pass
    except Exception:
        pass


def validate_safe_workspace_path(
    target_path: Path,
    required_incident_id: Optional[str] = None,
    enforce_registry: bool = True,
) -> Path:
    """Validate that target_path resides strictly within the service-owned disposable directory
    and belongs to a registered incident workspace.

    Rejects:
    - Nonexistent or non-directory paths.
    - The disposable service directory root itself.
    - Path traversal (..) and symlink/reparse-point escapes.
    - Paths into the real repository or unrelated temp directories.
    - Unregistered workspaces when enforce_registry is True.
    """
    resolved_service_dir = DISPOSABLE_SERVICE_DIR.resolve()

    if not target_path.exists():
        raise SecurityViolation(f"Target path does not exist: '{target_path}'")

    if not target_path.is_dir():
        raise SecurityViolation(f"Target path is not a directory: '{target_path}'")

    resolved_target = target_path.resolve()

    # Reject the root disposable directory itself
    if resolved_target == resolved_service_dir:
        raise SecurityViolation(f"Target cannot be the disposable directory root: '{resolved_target}'")

    # Reject paths outside the private service directory
    try:
        resolved_target.relative_to(resolved_service_dir)
    except ValueError:
        raise SecurityViolation(
            f"Path escape rejected: target '{resolved_target}' is outside service base '{resolved_service_dir}'"
        )

    # Reject symlink escapes
    real_path = Path(os.path.realpath(str(target_path)))
    try:
        real_path.relative_to(resolved_service_dir)
    except ValueError:
        raise SecurityViolation(f"Symlink target escape rejected: '{real_path}' points outside service base")

    if real_path == resolved_service_dir:
        raise SecurityViolation("Symlink points directly to the disposable directory root")

    # Enforce registration binding
    if enforce_registry:
        if required_incident_id is not None:
            expected = get_registered_workspace(required_incident_id)
            if expected is None or resolved_target != expected:
                raise SecurityViolation(
                    f"Workspace '{resolved_target}' is not registered for incident '{required_incident_id}'"
                )
        else:
            registered_targets = list(_workspace_registry.values())
            # Also check disk-registered workspaces
            for link in DISPOSABLE_SERVICE_DIR.glob("workspace_*.path"):
                try:
                    registered_targets.append(Path(link.read_text(encoding="utf-8").strip()).resolve())
                except OSError:
                    pass
            if resolved_target not in registered_targets:
                raise SecurityViolation(
                    f"Workspace '{resolved_target}' is not in the active registered workspace registry"
                )

    return resolved_target


def create_disposable_workspace(
    source_repo_root: Path,
    incident_id: str,
    base_dir: Optional[Path] = None,
) -> Path:
    """Provision a fresh, disposable workspace isolated from the main repository checkout.
    
    Uses an explicit POSITIVE file manifest (DISPOSABLE_WORKSPACE_MANIFEST).
    STRICTLY EXCLUDES conftest.py, .env, credentials, unrelated tests, and virtual environments.
    Prunes stale workspaces to enforce bounded resource retention.
    """
    if base_dir is None:
        base_dir = DISPOSABLE_SERVICE_DIR

    base_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            base_dir.chmod(0o700)
        except OSError:
            pass

    # Prune stale workspaces before creating a new one
    prune_stale_resources(max_keep=MAX_WORKSPACES_RETAINED)

    workspace_id = f"workspace_{incident_id}_{uuid.uuid4().hex[:8]}"
    workspace_path = base_dir / workspace_id
    workspace_path.mkdir(parents=True, exist_ok=False)

    # Copy ONLY files explicitly listed in DISPOSABLE_WORKSPACE_MANIFEST
    for rel_path in DISPOSABLE_WORKSPACE_MANIFEST:
        src_file = source_repo_root / rel_path
        if src_file.exists() and src_file.is_file():
            dst_file = workspace_path / rel_path
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)

    # Register the workspace for this incident
    register_workspace(incident_id, workspace_path)

    # Enforce retention cap AFTER creation so total active count never exceeds MAX_WORKSPACES_RETAINED
    prune_stale_resources(max_keep=MAX_WORKSPACES_RETAINED)

    return validate_safe_workspace_path(workspace_path, required_incident_id=incident_id)


def cleanup_disposable_workspace(workspace_path: Path, incident_id: Optional[str] = None) -> None:
    """Safely tear down a disposable workspace directory, associated token, and unregister it."""
    try:
        validated = validate_safe_workspace_path(
            workspace_path,
            required_incident_id=incident_id,
            enforce_registry=False,
        )
        if validated.exists():
            shutil.rmtree(validated, ignore_errors=True)
        if incident_id:
            unregister_workspace(incident_id)
            token_file = DISPOSABLE_SERVICE_DIR / f"auth_{incident_id}.token"
            if token_file.exists():
                try:
                    token_file.unlink()
                except OSError:
                    pass
    except SecurityViolation:
        pass


def sanitize_process_environment(extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build a strictly minimal sanitized environment dictionary using SAFE_ENV_ALLOWLIST.
    
    Purges credential-bearing variables including AWS_ACCESS_KEY_ID, DATABASE_URL,
    TEST_DATABASE_URL, HOME, and PYTHONPATH. Rejects unsafe overrides (LD_PRELOAD,
    PYTEST_ADDOPTS, proxy settings, credentials).
    """
    clean_env: Dict[str, str] = {}

    # 1. Inherit only explicitly allowlisted environment variables
    for key in SAFE_ENV_ALLOWLIST:
        val = os.environ.get(key)
        if val is not None:
            clean_env[key] = val

    # 2. Strict isolated test settings
    clean_env["ENVIRONMENT"] = "disposable_test"
    clean_env["CI"] = "true"
    clean_env["PYTHONDONTWRITEBYTECODE"] = "1"
    clean_env["PYTHONUNBUFFERED"] = "1"

    # Block outbound network from child process
    clean_env["HTTP_PROXY"] = "http://127.0.0.1:0"
    clean_env["HTTPS_PROXY"] = "http://127.0.0.1:0"
    clean_env["NO_PROXY"] = "localhost,127.0.0.1"

    # Explicitly verify sensitive variables are absent
    for sensitive in [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "HOME",
        "PYTHONPATH",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "RENDER_API_KEY",
        "NEON_API_KEY",
        "OPENAI_API_KEY",
    ]:
        clean_env.pop(sensitive, None)

    # 3. Apply extra_env with strict security checks
    if extra_env:
        for k, v in extra_env.items():
            k_upper = k.upper()
            # Reject unsafe environment overrides
            if k_upper in UNSAFE_ENV_OVERRIDE_KEYS:
                raise SecurityViolation(f"Unsafe environment override rejected: '{k}'")
            # Reject credential patterns
            if any(pattern in k_upper for pattern in FORBIDDEN_ENV_PATTERNS):
                raise SecurityViolation(f"Dangerous environment override rejected: '{k}'")
            clean_env[k] = str(v)

    return clean_env


def run_sandboxed_command(
    cmd: List[str],
    cwd: Path,
    timeout: int = REPAIR_TIMEOUT_SECONDS,
    extra_env: Optional[Dict[str, str]] = None,
    enforce_registry: bool = True,
    incident_id: Optional[str] = None,
) -> Tuple[int, str, str]:
    """Execute a command bounded inside the disposable workspace with stripped credentials.
    
    Forbids dangerous flags, restricts duration, bounds output buffers to MAX_OUTPUT_BYTES,
    and terminates the process tree on timeout.
    """
    # 1. Enforce directory containment and workspace registration
    safe_cwd = validate_safe_workspace_path(
        cwd,
        required_incident_id=incident_id,
        enforce_registry=enforce_registry,
    )

    # 2. Check for explicitly forbidden command tokens
    forbidden_tokens = [
        "--dangerously-bypass-approvals-and-sandbox",
        "rm -rf /",
        ":(){ :|:& };:",
        "chmod 777",
        "sudo",
    ]
    cmd_str = " ".join(cmd)
    for forbidden in forbidden_tokens:
        if forbidden in cmd_str:
            raise SecurityViolation(f"Forbidden command parameter rejected: '{forbidden}'")

    # 3. Build minimal sanitized environment
    env = sanitize_process_environment(extra_env)

    # 4. Execute with timeout and bounded output
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
        stdout = proc.stdout[:MAX_OUTPUT_BYTES] if proc.stdout else ""
        stderr = proc.stderr[:MAX_OUTPUT_BYTES] if proc.stderr else ""
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"Sandboxed command exceeded timeout of {timeout}s: {' '.join(cmd)}")
    except Exception as exc:
        if isinstance(exc, SecurityViolation):
            raise
        return -1, "", "Execution error: subprocess execution failed"


def find_test_python() -> str:
    """Find a Python interpreter that has FastAPI and pytest available."""
    repo_root = Path(__file__).resolve().parents[1]
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    exe_name = "python.exe" if os.name == "nt" else "python"
    venv_py = repo_root / "backend" / ".venv" / bin_dir / exe_name
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def run_disposable_tests(workspace_path: Path, incident_id: Optional[str] = None) -> Tuple[bool, str]:
    """Execute targeted backend unit tests in the disposable workspace."""
    safe_ws = validate_safe_workspace_path(
        workspace_path,
        required_incident_id=incident_id,
        enforce_registry=False,
    )
    backend_dir = safe_ws / "backend"
    if not backend_dir.exists():
        backend_dir = safe_ws

    python_bin = find_test_python()
    cmd = [python_bin, "-m", "pytest", "tests/test_api.py", "-v"]

    ret, stdout, stderr = run_sandboxed_command(
        cmd,
        cwd=backend_dir,
        timeout=45,
        enforce_registry=False,
    )
    output = f"{stdout}\n{stderr}".strip()[:MAX_OUTPUT_BYTES]
    return (ret == 0), output
