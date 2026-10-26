"""Agent adapter interface for TaskLane Issue #4 Incident Response.

Provides:
- BaseAgentAdapter: Abstract base class for incident response agents.
- MockAgentAdapter: Deterministic test fixture adapter for CI safety validation.
  DISCLOSURE: This adapter does NOT execute an AI or LLM model. It deterministically
  removes synthetic drill fixture markers to safely verify the containment pipeline.
- CodexAgentAdapter: Integration adapter for the Codex CLI.
  CRITICAL: Write-capable Codex execution is DISABLED pending maintainer authorization.
"""
import os
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from config import DIAGNOSIS_TIMEOUT_SECONDS, REPAIR_TIMEOUT_SECONDS
from fixtures import (
    has_task_creation_defect,
    remove_task_creation_defect,
    run_http_task_verification_script,
)
from sandbox import (
    SecurityViolation,
    find_test_python,
    run_disposable_tests,
    run_sandboxed_command,
    sanitize_process_environment,
    validate_safe_workspace_path,
)


class BaseAgentAdapter(ABC):
    """Abstract base class for incident responder agent adapters."""

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Return the unique name of this adapter."""
        pass

    @abstractmethod
    def diagnose(self, incident_context: Dict[str, Any]) -> Dict[str, Any]:
        """Perform read-only investigation based on incident telemetry."""
        pass

    @abstractmethod
    def repair(
        self,
        incident_context: Dict[str, Any],
        workspace_path: Path,
        operator_approved: bool = False,
    ) -> Dict[str, Any]:
        """Perform remediation strictly within the disposable workspace."""
        pass


class MockAgentAdapter(BaseAgentAdapter):
    """Deterministic test fixture adapter for automated CI safety validation.

    TRUTHFUL DISCLOSURE:
    This adapter does NOT execute an AI/LLM model. It deterministically removes
    the synthetic drill fixture markers to verify the isolation, containment,
    and recovery pipeline without flakiness or credential requirements.
    """

    @property
    def adapter_name(self) -> str:
        return "MockAgentAdapter"

    def diagnose(self, incident_context: Dict[str, Any]) -> Dict[str, Any]:
        """Perform deterministic read-only analysis of telemetry context."""
        has_5xx = incident_context.get("error_count", 0) or 0
        trace_id = incident_context.get("trace_id")
        has_evidence = incident_context.get("has_correlated_evidence", False)

        analysis = (
            f"Mock analysis: Alert '{incident_context.get('alertname')}' triggered. "
            f"Correlated trace ID: {trace_id or 'none'}. Evidence verified: {has_evidence}."
        )
        return {
            "adapter": self.adapter_name,
            "model_executed": False,
            "read_only": True,
            "has_correlated_evidence": has_evidence,
            "analysis": analysis,
            "identified_cause": "unhandled_runtime_error_in_urgent_task_creation",
            "recommended_action": "remove_urgent_task_dispatch_regression",
        }

    def repair(
        self,
        incident_context: Dict[str, Any],
        workspace_path: Path,
        operator_approved: bool = False,
    ) -> Dict[str, Any]:
        """Apply deterministic fix in disposable workspace only if operator approved."""
        if operator_approved is not True:
            raise PermissionError("Remediation rejected: operator approval required (confirm is True)")

        # Validate disposable path
        safe_ws = validate_safe_workspace_path(workspace_path, enforce_registry=False)

        # Check if bug is present
        if not has_task_creation_defect(safe_ws):
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "remediation_type": "deterministic_fixture_rollback",
                "status": "no_defect_detected",
                "diff": "",
                "tests_passed": True,
            }

        # Apply deterministic fix
        diff, success = remove_task_creation_defect(safe_ws)
        if not success:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "remediation_type": "deterministic_fixture_rollback",
                "status": "patch_failed",
                "diff": "",
                "tests_passed": False,
            }

        # Run unit tests in disposable workspace
        tests_passed, test_output = run_disposable_tests(safe_ws)

        # Run real HTTP endpoint and database persistence verification
        http_check = run_http_task_verification_script(safe_ws)
        http_recovered = (
            http_check.get("normal_status") == 201
            and http_check.get("urgent_status") == 201
            and http_check.get("urgent_persisted") is True
        )

        return {
            "adapter": self.adapter_name,
            "model_executed": False,
            "remediation_type": "deterministic_fixture_rollback",
            "status": "repaired" if (tests_passed and http_recovered) else "repair_failed",
            "diff": diff,
            "tests_passed": tests_passed,
            "http_verified": http_recovered,
            "http_details": http_check,
            "workspace": str(safe_ws),
        }


class CodexAgentAdapter(BaseAgentAdapter):
    """Codex CLI agent adapter with sandbox isolation.

    CRITICAL SAFETY POLICY:
    Write-capable model execution is strictly DISABLED by default pending explicit
    maintainer authorization and verification of an independently isolated worker.
    """

    def __init__(self, codex_bin: str = "codex"):
        self.codex_bin = codex_bin
        # Controlled by environment: write-capable is disabled by default
        self.write_capable_enabled = (
            os.environ.get("TASKLANE_ALLOW_WRITE_CAPABLE_CODEX", "false").lower() == "true"
        )

    @property
    def adapter_name(self) -> str:
        return "CodexAgentAdapter"

    def _is_available(self) -> bool:
        """Check if codex CLI binary is found on PATH."""
        from shutil import which
        return which(self.codex_bin) is not None

    def diagnose(self, incident_context: Dict[str, Any]) -> Dict[str, Any]:
        """Perform read-only investigation using Codex CLI in read-only sandbox mode."""
        if not self._is_available():
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": "codex_unavailable",
                "detail": f"Codex binary '{self.codex_bin}' not found on PATH",
            }

        prompt = (
            f"You are an on-call SRE investigating an alert.\n"
            f"Alert: {incident_context.get('alertname')}\n"
            f"Trace ID: {incident_context.get('trace_id')}\n"
            f"Identify the failure cause. Do NOT modify any files."
        )

        cmd = [
            self.codex_bin,
            "exec",
            "--sandbox", "read-only",
            "--prompt", prompt,
        ]

        try:
            repo_root = Path(__file__).resolve().parents[1]
            safe_env = sanitize_process_environment()
            proc = subprocess.run(
                cmd,
                cwd=str(repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=DIAGNOSIS_TIMEOUT_SECONDS,
                check=False,
                env=safe_env,
            )
            # Never expose raw process errors or unchecked stderr dumps
            if proc.returncode == 0 and proc.stdout:
                analysis = proc.stdout[:2048].strip()
            else:
                analysis = "Diagnostic execution completed without actionable output"

            return {
                "adapter": self.adapter_name,
                "model_executed": (proc.returncode == 0),
                "status": "diagnosed" if proc.returncode == 0 else "diagnostic_failed",
                "analysis": analysis,
            }
        except subprocess.TimeoutExpired:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": "timed_out",
                "detail": f"Codex diagnosis timed out after {DIAGNOSIS_TIMEOUT_SECONDS}s",
            }
        except Exception:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": "execution_error",
                "detail": "Failed to spawn Codex subprocess",
            }

    def repair(
        self,
        incident_context: Dict[str, Any],
        workspace_path: Path,
        operator_approved: bool = False,
    ) -> Dict[str, Any]:
        """Perform write-capable remediation in disposable workspace.
        
        ENFORCEMENT: Disabled unless explicit maintainer authorization is set.
        """
        if operator_approved is not True:
            raise PermissionError("Remediation rejected: operator approval required (confirm is True)")

        if not self.write_capable_enabled:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": "disabled_by_policy",
                "error": "Write-capable model execution is disabled pending explicit maintainer authorization.",
                "tests_passed": False,
            }

        # If explicitly enabled by maintainer with proper isolation
        safe_ws = validate_safe_workspace_path(workspace_path, enforce_registry=False)
        return {
            "adapter": self.adapter_name,
            "model_executed": False,
            "status": "unimplemented_isolated_worker",
            "error": "Dedicated non-root isolated container worker is pending maintainer deployment.",
            "tests_passed": False,
        }
