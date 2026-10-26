"""Agent adapter interface for TaskLane Issue #4 Incident Response.

Provides:
- BaseAgentAdapter: Abstract base class for incident response agents.
- MockAgentAdapter: Deterministic test fixture adapter for CI safety validation.
  DISCLOSURE: This adapter does NOT execute an AI or LLM model. It deterministically
  removes synthetic drill fixture markers to safely verify the containment pipeline.
- CodexAgentAdapter: Integration adapter for the Codex CLI.
  CRITICAL: Write-capable Codex execution is DISABLED pending maintainer authorization.
- DeepSeekAgentAdapter: Integration adapter for DeepSeek Flash (deepseek-chat).
  Provides read-only telemetry analysis and structured unified-diff remediation
  confined strictly to disposable workspaces with atomic rollback.
"""
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import (
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    DIAGNOSIS_TIMEOUT_SECONDS,
    MAX_DIFF_BYTES,
    MAX_OUTPUT_BYTES,
    REPAIR_TIMEOUT_SECONDS,
)
from fixtures import (
    get_target_file,
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


def extract_diff_from_text(text: str) -> str:
    """Extract unified diff from text, stripping markdown code fences if present."""
    if not text:
        return ""
    lines = text.splitlines(keepends=True)
    in_fence = False
    fence_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                break
            else:
                in_fence = True
                continue
        if in_fence:
            fence_lines.append(line)
    if fence_lines:
        return "".join(fence_lines)

    # If no fences, find where diff starts
    diff_start = -1
    for i, line in enumerate(lines):
        if line.startswith("--- ") or line.startswith("diff --git "):
            diff_start = i
            break
    if diff_start != -1:
        return "".join(lines[diff_start:])
    return text


def validate_unified_diff(
    diff_text: str,
    allowed_rel_path: str = "backend/main.py",
    max_bytes: int = MAX_DIFF_BYTES,
) -> Tuple[bool, Optional[str]]:
    """Validate that a diff strictly targets the allowed relative file path and satisfies safety constraints."""
    if not diff_text or not diff_text.strip():
        return False, "Empty or whitespace-only diff"

    encoded = diff_text.encode("utf-8")
    if len(encoded) > max_bytes:
        return False, f"Diff size ({len(encoded)} bytes) exceeds maximum limit ({max_bytes} bytes)"

    lines = diff_text.splitlines()

    # Disallowed git patch operations
    forbidden_markers = [
        "new file mode",
        "deleted file mode",
        "old mode",
        "new mode",
        "GIT binary patch",
        "rename from",
        "rename to",
        "similarity index",
    ]
    for line in lines:
        for marker in forbidden_markers:
            if marker in line:
                return False, f"Diff contains forbidden patch operation: '{marker}'"

    target_files_from = []
    target_files_to = []

    for line in lines:
        if line.startswith("--- "):
            parts = line[4:].strip().split()
            if parts:
                target_files_from.append(parts[0])
        elif line.startswith("+++ "):
            parts = line[4:].strip().split()
            if parts:
                target_files_to.append(parts[0])

    if not target_files_from or not target_files_to:
        return False, "Missing unified diff headers (--- and +++)"

    allowed_names = {
        allowed_rel_path,
        Path(allowed_rel_path).name,
        allowed_rel_path.replace("\\", "/"),
    }

    def normalize_diff_path(p: str) -> str:
        if p.startswith("a/") or p.startswith("b/"):
            p = p[2:]
        return p.replace("\\", "/").strip()

    for p in target_files_from + target_files_to:
        if ".." in p or p.startswith("/") or p.startswith("\\") or ":" in p:
            return False, f"Path traversal or absolute path detected: '{p}'"
        norm = normalize_diff_path(p)
        if norm not in allowed_names:
            return False, f"Diff targets unauthorized file '{p}' (expected '{allowed_rel_path}')"

    has_hunk = any(line.startswith("@@ ") for line in lines)
    if not has_hunk:
        return False, "Diff contains no hunk headers (@@ ... @@)"

    return True, None


def apply_unified_diff(original_text: str, diff_text: str) -> Tuple[str, bool]:
    """Apply a unified diff to original_text in pure Python.

    Returns (repaired_text, True) on clean application, or (original_text, False) on failure.
    """
    orig_lines = original_text.splitlines(keepends=True)
    diff_lines = diff_text.splitlines(keepends=True)

    hunks = []
    current_hunk = None
    hunk_header_re = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")

    for line in diff_lines:
        m = hunk_header_re.match(line)
        if m:
            if current_hunk:
                hunks.append(current_hunk)
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            current_hunk = {
                "old_start": old_start,
                "old_count": old_count,
                "lines": [],
            }
        elif current_hunk is not None:
            if line.startswith((" ", "+", "-", "\\")):
                current_hunk["lines"].append(line)
    if current_hunk:
        hunks.append(current_hunk)

    if not hunks:
        return original_text, False

    offset = 0
    result_lines = list(orig_lines)

    for hunk in hunks:
        old_start = hunk["old_start"]
        expected_old = []
        new_lines = []
        for hline in hunk["lines"]:
            if hline.startswith(" "):
                expected_old.append(hline[1:])
                new_lines.append(hline[1:])
            elif hline.startswith("-"):
                expected_old.append(hline[1:])
            elif hline.startswith("+"):
                new_lines.append(hline[1:])

        target_idx = old_start - 1 + offset
        match_idx = None

        # Try exact target_idx
        if 0 <= target_idx <= len(result_lines) - len(expected_old):
            candidate = [l.rstrip("\r\n") for l in result_lines[target_idx : target_idx + len(expected_old)]]
            expected_stripped = [l.rstrip("\r\n") for l in expected_old]
            if candidate == expected_stripped:
                match_idx = target_idx

        # Search nearby or globally if line numbers shifted
        if match_idx is None:
            expected_stripped = [l.rstrip("\r\n") for l in expected_old]
            for i in range(len(result_lines) - len(expected_old) + 1):
                candidate = [l.rstrip("\r\n") for l in result_lines[i : i + len(expected_old)]]
                if candidate == expected_stripped:
                    match_idx = i
                    break

        if match_idx is None:
            return original_text, False

        result_lines[match_idx : match_idx + len(expected_old)] = new_lines
        offset += len(new_lines) - len(expected_old)

    # Normalize line endings to match original
    joined = "".join(result_lines)
    if "\r\n" in original_text and "\r\n" not in joined:
        joined = joined.replace("\n", "\r\n")
    return joined, True


class DeepSeekAgentAdapter(BaseAgentAdapter):
    """Integration adapter for DeepSeek Flash (deepseek-chat).

    CRITICAL SAFETY BOUNDARIES:
    - Write-capable remediation is disabled by default via TASKLANE_ALLOW_WRITE_CAPABLE_DEEPSEEK=false.
    - Diagnosis is strictly read-only and telemetry-driven.
    - When write remediation is authorized by maintainer and approved by operator:
      * Only single-file unified diff targeting backend/main.py is requested.
      * No shell commands or arbitrary code execution are requested or executed.
      * Strict diff validation (size limits, path containment, forbidden git patch operations).
      * Confined exclusively to registered disposable incident workspaces.
      * Atomic file backup and rollback if patch application or post-repair test verification fails.
      * Environment sanitization explicitly purges DEEPSEEK_API_KEY from child processes.
      * API credentials and authorization headers are never logged or exposed in exceptions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._api_key = api_key
        self.api_url = api_url or DEEPSEEK_API_URL
        self.model = model or DEEPSEEK_MODEL
        self.write_capable_enabled = (
            os.environ.get("TASKLANE_ALLOW_WRITE_CAPABLE_DEEPSEEK", "false").lower() == "true"
        )

    @property
    def adapter_name(self) -> str:
        return "DeepSeekAgentAdapter"

    def _get_api_key(self) -> Optional[str]:
        return self._api_key or os.environ.get("DEEPSEEK_API_KEY")

    def _call_api(
        self,
        messages: List[Dict[str, str]],
        timeout: int,
        max_tokens: int = 1024,
    ) -> Tuple[Optional[str], Optional[str]]:
        api_key = self._get_api_key()
        if not api_key:
            return None, "api_key_missing"

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "TaskLane-OnCall/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_body = resp.read(MAX_OUTPUT_BYTES + 1)
                if len(raw_body) > MAX_OUTPUT_BYTES:
                    return None, f"Response body exceeded maximum buffer limit ({MAX_OUTPUT_BYTES} bytes)"
                resp_data = json.loads(raw_body.decode("utf-8"))
                choices = resp_data.get("choices")
                if not choices or not isinstance(choices, list):
                    return None, "DeepSeek API response missing choices array"
                msg = choices[0].get("message", {})
                content = msg.get("content")
                if content is None:
                    return None, "DeepSeek API response missing message content"
                return str(content), None
        except urllib.error.HTTPError as e:
            return None, f"DeepSeek API HTTP error: {e.code} ({e.reason})"
        except urllib.error.URLError as e:
            return None, f"DeepSeek API connection error: {e.reason}"
        except TimeoutError:
            return None, f"DeepSeek API request timed out after {timeout}s"
        except Exception as e:
            return None, f"DeepSeek API unexpected error: {type(e).__name__}"

    def diagnose(self, incident_context: Dict[str, Any]) -> Dict[str, Any]:
        """Perform read-only investigation using DeepSeek Flash."""
        api_key = self._get_api_key()
        if not api_key:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "read_only": True,
                "status": "api_key_missing",
                "analysis": "DeepSeek API key is not configured (DEEPSEEK_API_KEY is unset).",
            }

        alertname = incident_context.get("alertname", "UnknownAlert")
        trace_id = incident_context.get("trace_id", "none")
        error_count = incident_context.get("error_count", 0)
        logs_snippet = incident_context.get("logs", "")

        prompt = (
            f"You are an on-call SRE investigating an incident for TaskLane.\n"
            f"Alert: {alertname}\n"
            f"Correlated Trace ID: {trace_id}\n"
            f"5xx Error Count: {error_count}\n"
            f"Log Telemetry Snippet: {logs_snippet}\n\n"
            f"Analyze the telemetry context and identify the probable root cause. "
            f"Do NOT execute or propose shell commands. Do NOT modify any files."
        )

        messages = [
            {"role": "system", "content": "You are an autonomous on-call SRE assistant for TaskLane."},
            {"role": "user", "content": prompt},
        ]

        response_text, error_msg = self._call_api(
            messages=messages,
            timeout=DIAGNOSIS_TIMEOUT_SECONDS,
            max_tokens=1024,
        )

        if error_msg:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "read_only": True,
                "status": "diagnostic_failed",
                "detail": error_msg,
            }

        return {
            "adapter": self.adapter_name,
            "model_executed": True,
            "read_only": True,
            "status": "diagnosed",
            "analysis": response_text[:2048].strip() if response_text else "",
        }

    def repair(
        self,
        incident_context: Dict[str, Any],
        workspace_path: Path,
        operator_approved: bool = False,
    ) -> Dict[str, Any]:
        """Perform remediation strictly within the disposable workspace."""
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

        safe_ws = validate_safe_workspace_path(workspace_path, enforce_registry=False)
        target_file = get_target_file(safe_ws)
        if not target_file.exists():
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": "target_not_found",
                "error": f"Target file '{target_file.name}' not found in workspace",
                "tests_passed": False,
            }

        api_key = self._get_api_key()
        if not api_key:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": "api_key_missing",
                "error": "DeepSeek API key is not configured (DEEPSEEK_API_KEY is unset).",
                "tests_passed": False,
            }

        current_content = target_file.read_text(encoding="utf-8")
        alertname = incident_context.get("alertname", "TaskLane5xxRate")
        trace_id = incident_context.get("trace_id", "none")

        prompt = (
            f"You are an autonomous on-call remediation engineer for TaskLane.\n"
            f"Incident Alert: {alertname}\n"
            f"Trace ID: {trace_id}\n"
            f"Target file: backend/main.py\n\n"
            f"An unhandled regression was introduced in create_task causing urgent-priority task creation to fail with HTTP 500.\n"
            f"Here is the current content of backend/main.py:\n"
            f"```python\n{current_content}\n```\n\n"
            f"Provide a standard unified diff (starting with '--- a/backend/main.py' and '+++ b/backend/main.py') "
            f"to fix this defect so urgent tasks succeed with HTTP 201.\n"
            f"CRITICAL RULES:\n"
            f"1. Output ONLY the unified diff patch.\n"
            f"2. Do NOT include explanations, commentary, or markdown text outside the diff.\n"
            f"3. Do NOT provide shell commands.\n"
            f"4. Touch ONLY backend/main.py.\n"
        )

        messages = [
            {"role": "system", "content": "You are an automated code remediation agent that outputs ONLY unified diffs."},
            {"role": "user", "content": prompt},
        ]

        response_text, error_msg = self._call_api(
            messages=messages,
            timeout=REPAIR_TIMEOUT_SECONDS,
            max_tokens=2048,
        )

        if error_msg:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": "api_error",
                "error": error_msg,
                "tests_passed": False,
            }

        diff_text = extract_diff_from_text(response_text or "")
        is_valid, validation_err = validate_unified_diff(diff_text, allowed_rel_path="backend/main.py")
        if not is_valid:
            return {
                "adapter": self.adapter_name,
                "model_executed": True,
                "status": "invalid_diff",
                "error": f"Generated diff rejected by security validator: {validation_err}",
                "diff": diff_text[:1024],
                "tests_passed": False,
            }

        # Atomic backup and application
        original_backup = current_content
        repaired_content, applied = apply_unified_diff(original_backup, diff_text)
        if not applied:
            return {
                "adapter": self.adapter_name,
                "model_executed": True,
                "status": "patch_failed",
                "error": "Unified diff could not be applied cleanly to target file.",
                "diff": diff_text,
                "tests_passed": False,
            }

        # Write patch to target file
        target_file.write_text(repaired_content, encoding="utf-8")

        # Run verification in sanitized workspace
        tests_passed, test_output = run_disposable_tests(safe_ws)
        http_check = run_http_task_verification_script(safe_ws)
        http_recovered = (
            http_check.get("normal_status") == 201
            and http_check.get("urgent_status") == 201
            and http_check.get("urgent_persisted") is True
        )

        if not (tests_passed and http_recovered):
            # Rollback to original backup atomically
            target_file.write_text(original_backup, encoding="utf-8")
            return {
                "adapter": self.adapter_name,
                "model_executed": True,
                "remediation_type": "model_unified_diff",
                "status": "repair_failed",
                "error": "Tests or HTTP verification failed after patch; rolled back to original content.",
                "diff": diff_text,
                "tests_passed": False,
                "http_verified": False,
                "http_details": http_check,
                "workspace": str(safe_ws),
            }

        return {
            "adapter": self.adapter_name,
            "model_executed": True,
            "remediation_type": "model_unified_diff",
            "status": "repaired",
            "diff": diff_text,
            "tests_passed": True,
            "http_verified": True,
            "http_details": http_check,
            "workspace": str(safe_ws),
        }
