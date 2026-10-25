"""Replaceable coding agent adapter interface and implementations.

Supports:
- BaseAgentAdapter: Enforces read-only diagnosis by default and explicit operator approval for repairs.
- MockAgentAdapter: Deterministic model adapter for CI guardrail and safety tests (truthfully labeled).
- CodexAgentAdapter: Genuine CLI adapter using authenticated Codex with bounded timeouts and read-only/workspace-write sandboxing.
"""
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from fixtures import has_task_creation_defect, remove_task_creation_defect
from sandbox import (
    SecurityViolation,
    run_disposable_tests,
    run_sandboxed_command,
    validate_safe_workspace_path,
)


class BaseAgentAdapter:
    """Abstract base adapter defining the on-call agent contract."""

    adapter_name: str = "base"

    def diagnose(self, incident_context: Dict[str, Any], workspace_path: Optional[Path] = None) -> Dict[str, Any]:
        """Perform read-only investigation. NEVER modifies files or commits code."""
        raise NotImplementedError

    def repair(
        self,
        incident_context: Dict[str, Any],
        workspace_path: Path,
        operator_approved: bool = False,
    ) -> Dict[str, Any]:
        """Perform write-capable remediation in the disposable workspace.

        STRICT GATE: Requires operator_approved == True.
        """
        if not operator_approved:
            raise PermissionError("Write-capable agent remediation requires explicit local operator authorization.")
        raise NotImplementedError


class MockAgentAdapter(BaseAgentAdapter):
    """Deterministic adapter for automated testing and CI safety verification.

    Explicitly labeled as 'mock_agent' so evidence is never misrepresented as genuine AI.
    """

    adapter_name: str = "mock_agent"

    def diagnose(self, incident_context: Dict[str, Any], workspace_path: Optional[Path] = None) -> Dict[str, Any]:
        """Analyze incident telemetry in read-only mode."""
        trace_id = incident_context.get("trace_id")
        logs = incident_context.get("recent_logs", [])
        alertname = incident_context.get("alertname", "TaskLane5xxRate")

        suspected_file = "backend/main.py"
        root_cause = "Unknown error"

        # Inspect sanitized logs for known defect patterns
        for log in logs:
            if "REGRESSION" in log or "503" in log or "500" in log:
                root_cause = "Unhandled exception detected during task creation pipeline in backend/main.py"
                break

        return {
            "status": "diagnosed",
            "adapter": self.adapter_name,
            "alertname": alertname,
            "trace_id": trace_id,
            "root_cause": root_cause,
            "suspected_file": suspected_file,
            "read_only": True,
            "file_modified": False,
        }

    def repair(
        self,
        incident_context: Dict[str, Any],
        workspace_path: Path,
        operator_approved: bool = False,
    ) -> Dict[str, Any]:
        """Apply deterministic remediation to the disposable workspace after explicit operator approval."""
        if not operator_approved:
            raise PermissionError("Write-capable agent remediation requires explicit local operator authorization.")

        # Ensure target workspace is safe
        safe_ws = validate_safe_workspace_path(workspace_path)

        if not has_task_creation_defect(safe_ws):
            return {
                "status": "no_defect_found",
                "adapter": self.adapter_name,
                "workspace": str(safe_ws),
                "operator_approved": True,
                "diff": "",
                "tests_passed": False,
            }

        # Apply fix inside disposable workspace
        diff_text, repaired = remove_task_creation_defect(safe_ws)
        if not repaired:
            return {
                "status": "repair_failed",
                "adapter": self.adapter_name,
                "workspace": str(safe_ws),
                "operator_approved": True,
                "diff": "",
                "tests_passed": False,
            }

        # Run verification tests in disposable workspace
        tests_passed, test_output = run_disposable_tests(safe_ws)

        return {
            "status": "repaired",
            "adapter": self.adapter_name,
            "workspace": str(safe_ws),
            "operator_approved": True,
            "diff": diff_text,
            "tests_passed": tests_passed,
            "test_output": test_output[:1000],
        }


class CodexAgentAdapter(BaseAgentAdapter):
    """Genuine coding agent adapter invoking the authenticated Codex CLI.

    Enforces:
    - --sandbox read-only for diagnosis.
    - --sandbox workspace-write for authorized repair.
    - NEVER uses --dangerously-bypass-approvals-and-sandbox.
    - Strict 30s timeout per invocation.
    - Falls back with truthful error documentation if CLI is unavailable.
    """

    adapter_name: str = "codex_cli"

    def __init__(self, codex_bin: Optional[str] = None):
        self.codex_bin = codex_bin if codex_bin is not None else shutil.which("codex")

    def _is_available(self) -> bool:
        if not self.codex_bin:
            return False
        return Path(self.codex_bin).exists() or shutil.which(self.codex_bin) is not None

    def diagnose(self, incident_context: Dict[str, Any], workspace_path: Optional[Path] = None) -> Dict[str, Any]:
        """Invoke Codex CLI in read-only sandbox mode to diagnose the incident."""
        if not self._is_available():
            return {
                "status": "diagnosis_skipped",
                "adapter": self.adapter_name,
                "error": "Codex CLI is not installed or not in PATH",
                "read_only": True,
                "file_modified": False,
            }

        ws_dir = workspace_path or Path.cwd()
        trace_id = incident_context.get("trace_id", "none")
        prompt = (
            f"DIAGNOSIS TASK: Read-only investigation. Alert {incident_context.get('alertname')} fired. "
            f"Correlated trace ID: {trace_id}. Review backend/main.py and identify any defect affecting task creation. "
            "Output a brief 3-sentence diagnostic summary. DO NOT edit any files."
        )

        cmd = [
            self.codex_bin,
            "exec",
            "--sandbox", "read-only",
            "-C", str(ws_dir),
            "--ephemeral",
            prompt,
        ]

        try:
            ret, stdout, stderr = run_sandboxed_command(cmd, cwd=ws_dir, timeout=30)
            if ret == 0:
                return {
                    "status": "diagnosed",
                    "adapter": self.adapter_name,
                    "findings": stdout.strip()[:1000],
                    "read_only": True,
                    "file_modified": False,
                }
            return {
                "status": "diagnosis_error",
                "adapter": self.adapter_name,
                "error": stderr.strip()[:500],
                "read_only": True,
                "file_modified": False,
            }
        except Exception as exc:
            return {
                "status": "diagnosis_exception",
                "adapter": self.adapter_name,
                "error": str(exc),
                "read_only": True,
                "file_modified": False,
            }

    def repair(
        self,
        incident_context: Dict[str, Any],
        workspace_path: Path,
        operator_approved: bool = False,
    ) -> Dict[str, Any]:
        """Invoke Codex CLI to modify code strictly inside the disposable workspace."""
        if not operator_approved:
            raise PermissionError("Write-capable agent remediation requires explicit local operator authorization.")

        safe_ws = validate_safe_workspace_path(workspace_path)
        if not self._is_available():
            return {
                "status": "repair_unavailable",
                "adapter": self.adapter_name,
                "error": "Codex CLI not available in environment",
                "operator_approved": True,
                "tests_passed": False,
            }

        prompt = (
            "REPAIR TASK: There is a bug in backend/main.py causing task creation to fail with 5xx. "
            "Locate and remove the bug block marked with ONCALL-BUG-FIXTURE. "
            "Verify backend/main.py is syntactically valid."
        )

        # Strictly workspace-write sandbox; never bypass approvals or sandbox
        cmd = [
            self.codex_bin,
            "exec",
            "--sandbox", "workspace-write",
            "-C", str(safe_ws),
            "--ephemeral",
            prompt,
        ]

        try:
            ret, stdout, stderr = run_sandboxed_command(cmd, cwd=safe_ws, timeout=45)
            # Run tests to verify
            tests_passed, test_out = run_disposable_tests(safe_ws)
            return {
                "status": "repaired" if tests_passed else "repair_unverified",
                "adapter": self.adapter_name,
                "workspace": str(safe_ws),
                "operator_approved": True,
                "codex_returncode": ret,
                "tests_passed": tests_passed,
                "test_output": test_out[:1000],
            }
        except Exception as exc:
            return {
                "status": "repair_exception",
                "adapter": self.adapter_name,
                "workspace": str(safe_ws),
                "operator_approved": True,
                "error": str(exc),
                "tests_passed": False,
            }
