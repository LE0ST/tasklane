"""Isolated Test Worker Controller for TaskLane On-Call Remediation.

CRITICAL ARCHITECTURAL CONSTRAINTS:
1. Separation of concerns: Model API calls occur strictly OUTSIDE the worker on the trusted host.
2. Positive manifest only: Worker receives ONLY explicit source files and test fixtures in a dedicated
   staging directory. No .git, no .env, no repository root, no Docker socket.
3. Defense-in-depth isolation:
   - Network: --network none (strictly no egress or ingress).
   - User: unprivileged non-root user (uid 10001, gid 10001).
   - Filesystem: read-only root (--read-only) with restricted tmpfs (--tmpfs /tmp:rw,noexec,nosuid,size=64m).
   - Resources: CPU bound (--cpus=1.0), memory bound (--memory=512m, --memory-swap=512m), pids bound (--pids-limit=100).
   - Capabilities: --cap-drop ALL, --security-opt no-new-privileges.
4. Trusted controller lifecycle: The controller creates, bounds, monitors, and immediately destroys
   the ephemeral worker container (--rm). Neither the responder nor candidate code receives Docker socket access.
5. Strict timeout and bounded output: Execution is bounded by timeout; stdout/stderr truncated to 64 KB.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import DISPOSABLE_WORKSPACE_MANIFEST, MAX_OUTPUT_BYTES

WORKER_IMAGE_NAME = "tasklane-isolated-worker:local"
WORKER_TIMEOUT_SECONDS = 45


class IsolatedWorkerController:
    """Controller for running candidate remediation tests in an isolated Docker worker."""

    @classmethod
    def check_availability(cls) -> Tuple[bool, str]:
        """Verify that Docker is available, the daemon is reachable, and the worker image is ready."""
        docker_bin = shutil.which("docker")
        if not docker_bin:
            return False, "docker_cli_not_found"

        # Check Docker daemon connectivity
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

        # Check worker image presence
        try:
            inspect_res = subprocess.run(
                ["docker", "image", "inspect", WORKER_IMAGE_NAME],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if inspect_res.returncode != 0:
                return False, f"worker_image_missing: {WORKER_IMAGE_NAME}"
        except (subprocess.TimeoutExpired, OSError):
            return False, "docker_inspect_timeout"

        # Active probe: run probe container with --network none and --read-only
        try:
            probe_cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
                "--user", "10001:10001",
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--entrypoint", "python",
                WORKER_IMAGE_NAME,
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
        """Copy ONLY the explicit positive manifest files into a clean, ephemeral staging directory.

        Guarantees that:
        - No .git directory exists.
        - No .env or credentials exist.
        - No Docker socket or control endpoint exists.
        """
        resolved_src = source_workspace.resolve()
        parent_dir = staging_parent or Path(tempfile.gettempdir()).resolve() / "tasklane_worker_staging"
        parent_dir.mkdir(parents=True, exist_ok=True)

        staging_dir = parent_dir / f"stage_{uuid.uuid4().hex[:12]}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        # Copy only manifest files
        for rel_path in DISPOSABLE_WORKSPACE_MANIFEST:
            src_file = resolved_src / rel_path
            if src_file.is_file():
                dest_file = staging_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)

        # Write self-contained task verification script inside staging
        verify_script = staging_dir / "backend" / "_verify_tasks.py"
        verify_script.write_text(cls._get_verification_script_content(), encoding="utf-8")

        # Explicit negative validation
        assert not (staging_dir / ".git").exists(), "Security Violation: .git leaked into worker staging"
        assert not (staging_dir / ".env").exists(), "Security Violation: .env leaked into worker staging"
        assert not (staging_dir / "docker.sock").exists(), "Security Violation: docker.sock in staging"

        return staging_dir

    @classmethod
    def run_isolated_verification(
        cls,
        staging_dir: Path,
        timeout: int = WORKER_TIMEOUT_SECONDS,
    ) -> Tuple[bool, Dict[str, Any], str]:
        """Execute candidate verification inside an ephemeral, resource-bounded container with NO network."""
        container_name = f"tasklane-worker-{uuid.uuid4().hex[:12]}"
        resolved_staging = staging_dir.resolve()
        mount_path = str(resolved_staging).replace("\\", "/")

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
            "-v", f"{mount_path}:/workspace:ro",
            "-e", "PYTHONPATH=/workspace/backend",
            "--entrypoint", "python",
            WORKER_IMAGE_NAME,
            "/workspace/backend/_verify_tasks.py",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            raw_output = f"{proc.stdout}\n{proc.stderr}".strip()
            bounded_output = raw_output[:MAX_OUTPUT_BYTES]

            parsed_data = {}
            for line in proc.stdout.splitlines():
                if line.startswith("TASKLANE_VERIFY_JSON:"):
                    try:
                        parsed_data = json.loads(line[len("TASKLANE_VERIFY_JSON:"):])
                    except Exception:
                        pass

            success = bool(
                proc.returncode == 0
                and parsed_data.get("normal_status") == 201
                and parsed_data.get("urgent_status") == 201
                and parsed_data.get("urgent_persisted") is True
            )
            return success, parsed_data, bounded_output

        except subprocess.TimeoutExpired:
            # Enforce cleanup of stalled container
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, check=False)
            return False, {}, f"ERROR: Isolated worker exceeded timeout of {timeout}s"
        except Exception as exc:
            return False, {}, f"ERROR: Isolated worker execution error: {exc}"
        finally:
            # Clean up staging directory
            if staging_dir.exists():
                try:
                    shutil.rmtree(staging_dir, ignore_errors=True)
                except OSError:
                    pass

    @classmethod
    def verify_isolation_offline(cls) -> Dict[str, Any]:
        """Run an offline probe inside the worker container verifying strict sandbox boundaries."""
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
            "--entrypoint", "python",
            WORKER_IMAGE_NAME,
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

    @staticmethod
    def _get_verification_script_content() -> str:
        return """import json
import sys
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from database import Base, get_db
from main import app
from models import Task

test_engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
Base.metadata.create_all(bind=test_engine)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app, raise_server_exceptions=False)

# 1. Normal task creation
normal_resp = client.post("/api/tasks", json={"title": "Normal Priority Task", "priority": "medium"})
normal_status = normal_resp.status_code

# 2. Urgent task creation
urgent_resp = client.post("/api/tasks", json={"title": "Urgent Priority Task", "priority": "urgent"})
urgent_status = urgent_resp.status_code

# 3. Persistence verification if urgent succeeded
urgent_persisted = False
if urgent_status == 201:
    task_id = urgent_resp.json().get("id")
    if task_id:
        get_resp = client.get(f"/api/tasks/{task_id}")
        urgent_persisted = (get_resp.status_code == 200 and get_resp.json().get("priority") == "urgent")

result = {
    "normal_status": normal_status,
    "urgent_status": urgent_status,
    "urgent_persisted": urgent_persisted,
}
print("TASKLANE_VERIFY_JSON:" + json.dumps(result))
"""
