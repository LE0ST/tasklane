"""Isolated Test Worker Controller and Session Lifecycle Manager for TaskLane.

CRITICAL ARCHITECTURAL CONSTRAINTS & SECURITY GUARANTEES:
1. Separation of concerns: Model API calls occur strictly OUTSIDE the worker on the trusted host.
2. Positive manifest only: Worker receives ONLY explicit source files and test fixtures in a dedicated
   ephemeral staging directory. No .git, no .env, no repository root, no Docker socket.
3. Defense-in-depth isolation:
   - Network: --network none (strictly no egress or ingress).
   - User: unprivileged non-root user (uid 10001, gid 10001).
   - Filesystem: read-only root (--read-only) with restricted tmpfs (--tmpfs /tmp:rw,noexec,nosuid,size=64m).
   - Resources: CPU bound (--cpus=1.0), memory bound (--memory=512m, --memory-swap=512m), pids bound (--pids-limit=100).
   - Capabilities: --cap-drop ALL, --security-opt no-new-privileges.
   - Logs: container logging disabled or strictly bounded (--log-driver none).
4. Process separation: Candidate code executes in its own isolated process (e.g. uvicorn);
   the trusted verifier executes in a separate process that does NOT import candidate code.
   The candidate cannot forge success tokens, nonces, or stdout markers.
5. Independent persistence verification: SQLite database file (/tmp/tasklane.db) is queried directly
   to confirm physical persistence, rather than trusting candidate application HTTP responses.
6. Untrusted exit detection: Pytest verification requires a genuine test completion summary and non-zero
   passed tests. Early exits (e.g. os._exit(0)) or empty outputs with return code 0 are rejected as untrusted.
7. Strict output budget: stdout/stderr is monitored in real-time with a hard cap (MAX_OUTPUT_BYTES).
   If exceeded, the container is stopped immediately.
8. Centralized bounded cleanup with retries: Staging directories and containers are tracked and guaranteed
   to be destroyed on success, failure, timeout, or cancellation, with bounded cleanup timeouts and retries.
   Emergency exit handler takes snapshot under lock to eliminate deadlocks.
9. Immutable identity: Containers run using the immutable image ID (sha256 digest), not a floating tag.
"""
import atexit
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config import DISPOSABLE_WORKSPACE_MANIFEST, MAX_OUTPUT_BYTES

WORKER_IMAGE_NAME = "tasklane-isolated-worker:local"
WORKER_TIMEOUT_SECONDS = 45
CLEANUP_TIMEOUT_SECONDS = 5

# Global registry of active sessions keyed by resolved staging path
_ACTIVE_SESSIONS: Dict[Path, "IsolatedWorkerSession"] = {}
_ACTIVE_SESSIONS_LOCK = threading.Lock()


def _emergency_cleanup_on_exit():
    """Ensure any orphaned containers and staging directories are pruned at process termination.
    Takes a snapshot of active sessions under lock and releases the lock before cleanup to prevent deadlocks.
    """
    with _ACTIVE_SESSIONS_LOCK:
        sessions_snapshot = list(_ACTIVE_SESSIONS.values())

    for session in sessions_snapshot:
        try:
            session.cleanup()
        except Exception:
            pass


atexit.register(_emergency_cleanup_on_exit)


class SecurityError(Exception):
    """Raised when security boundaries (symlinks, path escapes, etc.) are violated."""
    pass


class OutputBudgetExceededError(Exception):
    """Raised when container stdout/stderr exceeds the allowed memory budget."""
    pass


class IsolatedWorkerSession:
    """Manages the full lifecycle of an isolated staging directory and associated worker containers.
    Centralizes creation, container tracking, and bounded cleanup across success, error, timeout, and cancellation.
    Eliminates duplicate sessions by reusing existing instances for the same staging directory.
    """

    def __new__(cls, staging_dir: Path):
        resolved = staging_dir.resolve()
        with _ACTIVE_SESSIONS_LOCK:
            if resolved in _ACTIVE_SESSIONS:
                return _ACTIVE_SESSIONS[resolved]
            instance = super().__new__(cls)
            _ACTIVE_SESSIONS[resolved] = instance
            return instance

    def __init__(self, staging_dir: Path):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.staging_dir = staging_dir
        self.active_containers: Set[str] = set()
        self._lock = threading.Lock()
        self._cleaned = False

    @classmethod
    def create(
        cls,
        source_workspace: Path,
        staging_parent: Optional[Path] = None,
    ) -> "IsolatedWorkerSession":
        """Create a clean staging workspace copying only the positive manifest files securely.
        Strictly rejects symlinks, path escapes, and concurrent substitution.
        """
        resolved_src = source_workspace.resolve()
        parent_dir = staging_parent or Path(tempfile.gettempdir()).resolve() / "tasklane_worker_staging"
        parent_dir.mkdir(parents=True, exist_ok=True)

        staging_dir = parent_dir / f"stage_{uuid.uuid4().hex[:12]}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        session = cls(staging_dir)

        try:
            # Copy only positive manifest files with strict security validations
            for rel_path in DISPOSABLE_WORKSPACE_MANIFEST:
                cls._copy_manifest_file_securely(resolved_src, rel_path, staging_dir)

            # Write trusted harness and verifier scripts into staging
            backend_dir = staging_dir / "backend"
            backend_dir.mkdir(parents=True, exist_ok=True)

            harness_path = backend_dir / "_run_functional_harness.py"
            harness_path.write_text(cls._get_harness_script_content(), encoding="utf-8")

            verifier_path = backend_dir / "_trusted_verifier.py"
            verifier_path.write_text(cls._get_verifier_script_content(), encoding="utf-8")

            # Final negative validation on the staging directory
            if (staging_dir / ".git").exists():
                raise SecurityError("Security Violation: .git detected in worker staging")
            if (staging_dir / ".env").exists():
                raise SecurityError("Security Violation: .env detected in worker staging")
            if (staging_dir / "docker.sock").exists():
                raise SecurityError("Security Violation: docker.sock detected in staging")

            return session
        except Exception:
            session.cleanup()
            raise

    @staticmethod
    def _copy_manifest_file_securely(source_root: Path, rel_path: Path, dest_root: Path) -> None:
        """Securely copy a manifest file with symlink, traversal, and TOCTOU race protection."""
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise SecurityError(f"Path escape attempt in manifest path: {rel_path}")

        src_file = source_root / rel_path
        if not src_file.exists():
            return

        # 1. Traversal and symlink validation on all path components
        curr = src_file
        while curr != source_root:
            if curr.is_symlink() or os.path.islink(curr):
                raise SecurityError(f"Symlink rejected in source manifest hierarchy: {curr}")
            curr = curr.parent

        dest_file = dest_root / rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        # Ensure destination directory hierarchy has no symlinks
        curr_dest = dest_file.parent
        while curr_dest != dest_root:
            if curr_dest.is_symlink() or os.path.islink(curr_dest):
                raise SecurityError(f"Symlink rejected in destination hierarchy: {curr_dest}")
            curr_dest = curr_dest.parent

        # 2. Open source with O_NOFOLLOW to prevent concurrent substitution during open
        open_flags_src = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            open_flags_src |= os.O_NOFOLLOW

        try:
            src_fd = os.open(str(src_file), open_flags_src)
        except OSError as exc:
            raise SecurityError(f"Cannot open manifest source without following symlinks: {exc}")

        try:
            # Verify file descriptor points to a regular file
            st = os.fstat(src_fd)
            if not stat.S_ISREG(st.st_mode):
                raise SecurityError(f"Manifest path is not a regular file: {src_file}")

            # Open destination with O_NOFOLLOW and O_CREAT
            open_flags_dest = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                open_flags_dest |= os.O_NOFOLLOW

            dest_fd = os.open(str(dest_file), open_flags_dest, 0o600)
            try:
                while True:
                    chunk = os.read(src_fd, 65536)
                    if not chunk:
                        break
                    os.write(dest_fd, chunk)
            finally:
                os.close(dest_fd)
        finally:
            os.close(src_fd)

        # Post-copy check
        if os.path.islink(dest_file):
            raise SecurityError(f"Symlink detected at destination after copy: {dest_file}")

    def register_container(self, container_name: str) -> None:
        """Register an active container for tracking and bounded cleanup."""
        with self._lock:
            self.active_containers.add(container_name)

    def confirm_container_removed(self, container_name: str, max_retries: int = 3) -> bool:
        """Attempt to stop and remove the container with retries.
        Only unregisters the container once its removal is verified.
        """
        removed = self._remove_docker_container(container_name, max_retries=max_retries)
        if removed:
            with self._lock:
                self.active_containers.discard(container_name)
        return removed

    @classmethod
    def _remove_docker_container(cls, container_name: str, max_retries: int = 3) -> bool:
        """Execute docker rm -f with bounded timeouts and retries on failure or timeout."""
        for attempt in range(max_retries):
            try:
                res = subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True,
                    timeout=CLEANUP_TIMEOUT_SECONDS,
                    check=False,
                )
                if res.returncode == 0:
                    return True
                stderr_text = res.stderr.decode("utf-8", errors="replace") if isinstance(res.stderr, bytes) else str(res.stderr)
                if "No such container" in stderr_text or "not found" in stderr_text.lower():
                    return True
            except (subprocess.TimeoutExpired, OSError):
                pass
            if attempt < max_retries - 1:
                time.sleep(0.3)

        # Final inspect check: verify container no longer exists
        try:
            inspect_res = subprocess.run(
                ["docker", "container", "inspect", container_name],
                capture_output=True,
                timeout=CLEANUP_TIMEOUT_SECONDS,
                check=False,
            )
            return inspect_res.returncode != 0
        except Exception:
            return False

    def cleanup(self) -> None:
        """Centralized bounded cleanup for all associated containers and staging files.
        Executes with bounded timeouts and retries to prevent hanging.
        """
        with self._lock:
            if self._cleaned:
                return
            self._cleaned = True
            containers_to_clean = list(self.active_containers)

        # 1. Stop and remove all registered active containers with retries
        for container_name in containers_to_clean:
            self.confirm_container_removed(container_name, max_retries=3)

        # 2. Remove staging directory
        if self.staging_dir and self.staging_dir.exists():
            try:
                shutil.rmtree(self.staging_dir, ignore_errors=True)
            except Exception:
                pass

        with _ACTIVE_SESSIONS_LOCK:
            _ACTIVE_SESSIONS.pop(self.staging_dir.resolve(), None)

    def __enter__(self) -> "IsolatedWorkerSession":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()

    @staticmethod
    def _get_harness_script_content() -> str:
        """Functional harness that runs the candidate in uvicorn and verifier in a separate process."""
        return """import os
import subprocess
import sys
import time

env = os.environ.copy()
env["DATABASE_URL"] = "sqlite:////tmp/tasklane.db"

# 1. Start candidate code in a separate process via uvicorn with redirected streams
app_proc = subprocess.Popen(
    [
        sys.executable, "-m", "uvicorn", "main:app",
        "--host", "127.0.0.1",
        "--port", "8765",
        "--app-dir", "/workspace/backend"
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    env=env,
)

try:
    # 2. Run the trusted verifier in an independent process (does NOT import candidate code)
    verifier_proc = subprocess.run(
        [sys.executable, "/workspace/backend/_trusted_verifier.py", "http://127.0.0.1:8765"],
        capture_output=True,
        text=True,
        timeout=25,
    )
    sys.stdout.write(verifier_proc.stdout)
    sys.stderr.write(verifier_proc.stderr)
    sys.exit(verifier_proc.returncode)
finally:
    app_proc.terminate()
    try:
        app_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        app_proc.kill()
"""

    @staticmethod
    def _get_verifier_script_content() -> str:
        """Trusted functional verifier: uses httpx over TCP loopback and sqlite3 directly.
        Does NOT import candidate code (main.py).
        Verifies persistence directly from SQLite database /tmp/tasklane.db, independent of candidate's GET response.
        """
        return """import json
import sqlite3
import sys
import time
import httpx

base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
db_path = "/tmp/tasklane.db"

# Wait for server readiness on loopback (up to 50 retries of 0.2s = 10s)
ready = False
for _ in range(50):
    try:
        r = httpx.get(f"{base_url}/health", timeout=0.5)
        if r.status_code == 200:
            ready = True
            break
    except Exception:
        time.sleep(0.2)

if not ready:
    print("TRUSTED_VERIFIER_JSON:" + json.dumps({
        "error": "server_not_ready",
        "normal_status": 0,
        "urgent_status": 0,
        "urgent_persisted": False,
        "db_verified": False,
    }))
    sys.exit(1)

# 1. Normal task creation check
normal_status = 0
try:
    r_norm = httpx.post(
        f"{base_url}/api/tasks",
        json={"title": "Normal Priority Verification Task", "priority": "medium"},
        timeout=3,
    )
    normal_status = r_norm.status_code
except Exception:
    normal_status = 0

# 2. Urgent task creation check
urgent_status = 0
urgent_id = None
try:
    r_urg = httpx.post(
        f"{base_url}/api/tasks",
        json={"title": "Urgent Priority Verification Task", "priority": "urgent"},
        timeout=3,
    )
    urgent_status = r_urg.status_code
    if urgent_status == 201:
        urgent_id = r_urg.json().get("id")
except Exception:
    urgent_status = 0

# 3. Independent persistence verification directly in SQLite database
db_verified = False
db_priority = None
if urgent_status == 201 and urgent_id is not None:
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT priority FROM tasks WHERE id = ?", (urgent_id,))
        row = cur.fetchone()
        if row:
            db_priority = row[0]
            db_verified = (db_priority == "urgent")
        conn.close()
    except Exception:
        db_verified = False

# 4. Secondary API GET check
api_get_verified = False
if urgent_status == 201 and urgent_id:
    try:
        r_get = httpx.get(f"{base_url}/api/tasks/{urgent_id}", timeout=3)
        api_get_verified = (r_get.status_code == 200 and r_get.json().get("priority") == "urgent")
    except Exception:
        api_get_verified = False

result = {
    "normal_status": normal_status,
    "urgent_status": urgent_status,
    "db_verified": db_verified,
    "db_priority": db_priority,
    "api_get_verified": api_get_verified,
    "urgent_persisted": db_verified,
}
print("TRUSTED_VERIFIER_JSON:" + json.dumps(result))

if normal_status == 201 and urgent_status == 201 and db_verified:
    sys.exit(0)
else:
    sys.exit(1)
"""


class IsolatedWorkerController:
    """Controller for running candidate remediation tests in an isolated Docker worker."""

    _cached_image_id: Optional[str] = None
    _image_lock = threading.Lock()

    @classmethod
    def get_immutable_image_id(cls) -> Optional[str]:
        """Obtain and cache the immutable image ID (sha256 digest) for tasklane-isolated-worker:local."""
        with cls._image_lock:
            if cls._cached_image_id:
                return cls._cached_image_id

            try:
                res = subprocess.run(
                    ["docker", "image", "inspect", WORKER_IMAGE_NAME, "--format", "{{.Id}}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    cls._cached_image_id = res.stdout.strip()
                    return cls._cached_image_id
            except Exception:
                pass
            return None

    @classmethod
    def check_availability(cls) -> Tuple[bool, str]:
        """Verify that Docker is available, daemon is reachable, and the worker image is ready."""
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return False, "docker_cli_not_found"

        try:
            res = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode != 0:
                return False, "docker_daemon_unreachable"
        except (subprocess.TimeoutExpired, OSError):
            return False, "docker_daemon_timeout"

        image_id = cls.get_immutable_image_id()
        if not image_id:
            return False, f"worker_image_missing: {WORKER_IMAGE_NAME}"

        # Active probe using the immutable image ID
        try:
            probe_cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
                "--user", "10001:10001",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--log-driver", "none",
                image_id,
                "-c", "print('WORKER_PROBE_OK')"
            ]
            probe_proc = subprocess.run(
                probe_cmd,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if probe_proc.returncode != 0 or "WORKER_PROBE_OK" not in probe_proc.stdout:
                return False, f"worker_probe_failed: {probe_proc.stderr.strip()[:200]}"
        except Exception as exc:
            return False, f"worker_probe_exception: {exc}"

        return True, "available"

    @classmethod
    def is_available(cls) -> bool:
        """Boolean check for isolated worker readiness."""
        ok, _ = cls.check_availability()
        return ok

    @classmethod
    def prepare_staging_workspace(
        cls,
        source_workspace: Path,
        staging_parent: Optional[Path] = None,
    ) -> Path:
        """Legacy helper: prepares staging workspace using IsolatedWorkerSession."""
        session = IsolatedWorkerSession.create(source_workspace, staging_parent=staging_parent)
        return session.staging_dir

    @classmethod
    def _execute_bounded_container(
        cls,
        cmd: List[str],
        container_name: str,
        session: IsolatedWorkerSession,
        timeout: int,
        max_bytes: int = MAX_OUTPUT_BYTES,
    ) -> Tuple[int, str]:
        """Execute a container command with streaming output budget enforcement and bounded timeout.
        Guarantees container removal with retries and client termination on exceptions or cancellation.
        """
        session.register_container(container_name)

        proc = None
        t_out = None
        t_err = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            total_bytes = [0]
            chunks: List[bytes] = []
            budget_exceeded = threading.Event()

            def stream_reader(pipe):
                if not pipe:
                    return
                try:
                    while True:
                        buf = pipe.read(4096)
                        if not buf:
                            break
                        total_bytes[0] += len(buf)
                        if total_bytes[0] > max_bytes:
                            budget_exceeded.set()
                            # Immediately stop container and kill process
                            session.confirm_container_removed(container_name, max_retries=2)
                            try:
                                proc.kill()
                            except Exception:
                                pass
                            break
                        chunks.append(buf)
                except Exception:
                    pass
                finally:
                    try:
                        pipe.close()
                    except Exception:
                        pass

            t_out = threading.Thread(target=stream_reader, args=(proc.stdout,), daemon=True)
            t_err = threading.Thread(target=stream_reader, args=(proc.stderr,), daemon=True)
            t_out.start()
            t_err.start()

            retcode = proc.wait(timeout=timeout)
            t_out.join(timeout=1)
            t_err.join(timeout=1)

            if budget_exceeded.is_set():
                return 137, f"ERROR: Output budget exceeded limit of {max_bytes} bytes; container stopped."

            combined_output = b"".join(chunks).decode("utf-8", errors="replace")
            return retcode, combined_output[:max_bytes]

        except subprocess.TimeoutExpired:
            # Stop container immediately on timeout with retries
            session.confirm_container_removed(container_name, max_retries=3)
            if proc:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            return 124, f"ERROR: Execution timed out after {timeout}s; container stopped."

        except (KeyboardInterrupt, BaseException):
            # Clean up client process and container on cancellation or arbitrary exception
            session.confirm_container_removed(container_name, max_retries=3)
            if proc:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            raise

        finally:
            # Confirm container removal before concluding
            session.confirm_container_removed(container_name, max_retries=3)

    @classmethod
    def run_isolated_pytest(
        cls,
        session: IsolatedWorkerSession,
        timeout: int = WORKER_TIMEOUT_SECONDS,
    ) -> Tuple[bool, str]:
        """Step 1: Execute pytest against backend unit tests in the isolated container.
        Validates both return code and presence of trusted pytest execution summary.
        Detects premature exit or empty output even if returncode is 0.
        Returns (pytest_passed, bounded_output).
        """
        image_id = cls.get_immutable_image_id()
        if not image_id:
            return False, "ERROR: Worker image not available"

        container_name = f"tasklane-pytest-{uuid.uuid4().hex[:12]}"
        mount_path = str(session.staging_dir.resolve()).replace("\\", "/")

        cmd = [
            "docker", "run",
            "--name", container_name,
            "--rm",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--user", "10001:10001",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--cpus", "1.0",
            "--memory", "512m",
            "--memory-swap", "512m",
            "--pids-limit", "100",
            "--log-driver", "none",
            "-v", f"{mount_path}:/workspace:ro",
            "-e", "PYTHONPATH=/workspace/backend",
            "-e", "DATABASE_URL=sqlite:////tmp/tasklane.db",
            "-e", "TEST_DATABASE_URL=sqlite:///:memory:",
            "--entrypoint", "pytest",
            image_id,
            "/workspace/backend/tests/test_api.py",
            "-v",
            "-o", "cache_dir=/tmp/.pytest_cache",
        ]

        retcode, output = cls._execute_bounded_container(cmd, container_name, session, timeout)

        # 1. Output cannot be empty
        if not output or not output.strip():
            return False, "ERROR: pytest produced empty output (premature termination suspected)"

        # 2. Returncode must be 0
        if retcode != 0:
            return False, output

        # 3. Detect premature exit / missing summary:
        summary_match = re.search(r"=+\s+([0-9]+)\s+passed.*=+", output)
        if not summary_match:
            return False, f"ERROR: pytest exited with code 0 but missing completion summary (untrusted):\n{output}"

        passed_count = int(summary_match.group(1))
        if passed_count <= 0:
            return False, f"ERROR: pytest reported 0 passed tests:\n{output}"

        # If any failed or error counts exist in summary:
        if re.search(r"failed|error", output, re.IGNORECASE) and not re.search(r"0 failed", output):
            last_lines = output.strip().splitlines()[-3:]
            for line in last_lines:
                if "=" in line and ("failed" in line or "error" in line):
                    return False, output

        return True, output

    @classmethod
    def run_isolated_functional_verification(
        cls,
        session: IsolatedWorkerSession,
        timeout: int = WORKER_TIMEOUT_SECONDS,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """Step 2: Execute separate functional verification: candidate runs under uvicorn;
        the trusted verifier executes in an independent process querying over TCP loopback
        and verifying persistence directly in SQLite /tmp/tasklane.db.
        Candidate cannot forge success tokens, nonces, or pretend persistence.
        """
        image_id = cls.get_immutable_image_id()
        if not image_id:
            return False, {}, "ERROR: Worker image not available"

        container_name = f"tasklane-func-{uuid.uuid4().hex[:12]}"
        mount_path = str(session.staging_dir.resolve()).replace("\\", "/")

        cmd = [
            "docker", "run",
            "--name", container_name,
            "--rm",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--user", "10001:10001",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--cpus", "1.0",
            "--memory", "512m",
            "--memory-swap", "512m",
            "--pids-limit", "100",
            "--log-driver", "none",
            "-v", f"{mount_path}:/workspace:ro",
            "-e", "PYTHONPATH=/workspace/backend",
            "-e", "DATABASE_URL=sqlite:////tmp/tasklane.db",
            "--entrypoint", "python",
            image_id,
            "/workspace/backend/_run_functional_harness.py",
        ]

        retcode, output = cls._execute_bounded_container(cmd, container_name, session, timeout)

        parsed_data = {}
        for line in output.splitlines():
            if line.startswith("TRUSTED_VERIFIER_JSON:"):
                try:
                    parsed_data = json.loads(line[len("TRUSTED_VERIFIER_JSON:"):])
                except Exception:
                    pass

        functional_passed = bool(
            retcode == 0
            and parsed_data.get("normal_status") == 201
            and parsed_data.get("urgent_status") == 201
            and parsed_data.get("urgent_persisted") is True
            and parsed_data.get("db_verified") is True
        )
        return functional_passed, parsed_data, output

    @classmethod
    def run_isolated_verification(
        cls,
        staging_dir: Path,
        timeout: int = WORKER_TIMEOUT_SECONDS,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """Combined runner (legacy compatibility): runs both pytest and functional verification separately.
        Does NOT declare success if either fails.
        """
        session = IsolatedWorkerSession(staging_dir)
        try:
            pytest_passed, pytest_out = cls.run_isolated_pytest(session, timeout=timeout)
            func_passed, func_data, func_out = cls.run_isolated_functional_verification(session, timeout=timeout)

            combined_success = bool(pytest_passed and func_passed)
            combined_output = (
                f"--- Pytest (passed={pytest_passed}) ---\n{pytest_out}\n\n"
                f"--- Functional (passed={func_passed}) ---\n{func_out}"
            )
            return combined_success, func_data, combined_output
        finally:
            session.cleanup()

    @classmethod
    def verify_isolation_offline(cls) -> Dict[str, Any]:
        """Run an offline probe inside the worker container verifying strict sandbox boundaries."""
        image_id = cls.get_immutable_image_id()
        if not image_id:
            return {"error": "worker_image_missing"}

        probe_script = """
import os, sys, socket, json

results = {}

# 1. Verify network is disabled (no non-loopback egress)
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    s.connect(('8.8.8.8', 53))
    results['network_disabled'] = False
    s.close()
except OSError:
    results['network_disabled'] = True

# 2. Verify absence of Docker socket
results['no_docker_socket'] = not os.path.exists('/var/run/docker.sock')

# 3. Verify absence of .git checkout
results['no_git_checkout'] = not os.path.exists('/workspace/.git')

# 4. Verify no credentials or API keys in environment
env_keys = [k for k in os.environ if any(x in k.upper() for x in ['DEEPSEEK', 'AWS', 'DATABASE_URL', 'SECRET', 'TOKEN', 'API_KEY'])]
results['no_credentials'] = (len(env_keys) == 0)

# 5. Verify non-root user execution
results['non_root_user'] = (os.getuid() == 10001)

# 6. Verify read-only root filesystem
try:
    with open('/root_test.txt', 'w') as f:
        f.write('fail')
    results['read_only_root'] = False
except OSError:
    results['read_only_root'] = True

print('OFFLINE_VERIFICATION_JSON:' + json.dumps(results))
"""
        cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
            "--user", "10001:10001",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--log-driver", "none",
            image_id,
            "-c", probe_script,
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False)
            for line in proc.stdout.splitlines():
                if line.startswith("OFFLINE_VERIFICATION_JSON:"):
                    return json.loads(line[len("OFFLINE_VERIFICATION_JSON:"):])
            return {"error": f"Failed to parse verification output: {proc.stderr[:200]}"}
        except Exception as exc:
            return {"error": str(exc)}
