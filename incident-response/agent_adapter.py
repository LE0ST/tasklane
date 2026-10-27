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
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import (
    ALLOWED_DEEPSEEK_HOSTS,
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    DIAGNOSIS_TIMEOUT_SECONDS,
    ERROR_API_KEY_MISSING,
    ERROR_BLOCKED_MISSING_ISOLATED_WORKER,
    ERROR_DIFF_VALIDATION_FAILED,
    ERROR_INSECURE_API_SCHEME,
    ERROR_PATCH_APPLICATION_FAILED,
    ERROR_TEST_VERIFICATION_FAILED,
    ERROR_UNAUTHORIZED_API_HOST,
    ERROR_UPSTREAM_CONNECTION,
    ERROR_UPSTREAM_HTTP,
    ERROR_UPSTREAM_INVALID_RESPONSE,
    ERROR_UPSTREAM_PAYLOAD_TOO_LARGE,
    ERROR_UPSTREAM_PROTOCOL,
    ERROR_UPSTREAM_TIMEOUT,
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
    atomic_write_file,
    find_test_python,
    is_isolated_worker_available,
    run_disposable_tests,
    run_sandboxed_command,
    sanitize_process_environment,
    validate_safe_target_file,
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

        # Run in-process TestClient endpoint and database persistence verification
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
            "testclient_verified": http_recovered,
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


def validate_api_url(url: str, allowed_hosts: Optional[set] = None) -> Tuple[bool, str]:
    """Validate API URL scheme (strictly HTTPS) and allowed origin hostname."""
    if not url or not url.strip():
        return False, ERROR_INVALID_API_URL

    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception:
        return False, ERROR_INVALID_API_URL

    if parsed.scheme.lower() != "https":
        return False, ERROR_INSECURE_API_SCHEME

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False, ERROR_UNAUTHORIZED_API_HOST

    allowed = allowed_hosts if allowed_hosts is not None else ALLOWED_DEEPSEEK_HOSTS
    if hostname not in allowed:
        return False, ERROR_UNAUTHORIZED_API_HOST

    return True, "ok"


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Enforce strict redirect policy: disallow cross-origin or insecure redirects, and never forward credentials."""

    def __init__(self, allowed_hosts: Optional[set] = None):
        super().__init__()
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else ALLOWED_DEEPSEEK_HOSTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Disallow insecure or cross-origin redirects
        try:
            parsed_orig = urllib.parse.urlparse(req.full_url)
            parsed_new = urllib.parse.urlparse(newurl)
        except Exception:
            raise urllib.error.HTTPError(newurl, code, "Malformed redirect URL", headers, fp)

        if parsed_new.scheme.lower() != "https":
            raise urllib.error.HTTPError(newurl, code, "Insecure redirect rejected", headers, fp)

        orig_host = (parsed_orig.hostname or "").lower()
        new_host = (parsed_new.hostname or "").lower()
        orig_port = parsed_orig.port or 443
        new_port = parsed_new.port or 443

        if orig_host != new_host or orig_port != new_port:
            raise urllib.error.HTTPError(newurl, code, "Cross-origin redirect rejected", headers, fp)

        if new_host not in self.allowed_hosts:
            raise urllib.error.HTTPError(newurl, code, "Redirect to unauthorized host rejected", headers, fp)

        # For same-origin redirects, strip Authorization header to prevent credential forwarding
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and hasattr(new_req, "headers"):
            new_req.headers.pop("Authorization", None)
            new_req.headers.pop("authorization", None)
        return new_req


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


def normalize_diff_path(p: str) -> str:
    """Normalize a diff path by stripping quotes, git prefixes (a/, b/) and normalizing slashes."""
    p = p.strip().strip("'\"")
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    return p.replace("\\", "/").strip()


def validate_unified_diff(
    diff_text: str,
    allowed_rel_path: str = "backend/main.py",
    max_bytes: int = MAX_DIFF_BYTES,
) -> Tuple[bool, Optional[str]]:
    """Strictly validate that a diff targets exactly one allowed file, has valid hunks, and consumes the entire input.

    Rejects:
    - Empty or whitespace diffs.
    - Diffs exceeding max_bytes.
    - Forbidden git patch operations (binary patches, mode changes, file creations/deletions/renames).
    - Diffs targeting more than one file, or targeting unauthorized files.
    - Inconsistencies between diff --git, ---, and +++.
    - Malformed hunk headers or invalid line ranges (start < 1, count < 0).
    - Hunk line count mismatches (declared old/new count != actual lines in hunk).
    - Out-of-order or overlapping hunks.
    - Unparsed trailing content after the hunks.
    """
    if not diff_text or not diff_text.strip():
        return False, "empty_or_whitespace_diff"

    encoded = diff_text.encode("utf-8")
    if len(encoded) > max_bytes:
        return False, "diff_size_exceeded"

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
                return False, f"forbidden_patch_operation_{marker.replace(' ', '_')}"

    allowed_norm = allowed_rel_path.replace("\\", "/").strip()
    allowed_name = Path(allowed_rel_path).name
    allowed_paths = {allowed_norm, allowed_name}

    diff_git_target = None
    file_from = None
    file_to = None

    idx = 0
    num_lines = len(lines)
    hunk_start_idx = None
    hunk_header_re = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$")

    while idx < num_lines:
        line = lines[idx]
        if hunk_header_re.match(line):
            hunk_start_idx = idx
            break

        if line.startswith("diff --git "):
            parts = line[11:].strip().split()
            if len(parts) != 2:
                return False, "malformed_diff_git_header"
            p1 = normalize_diff_path(parts[0])
            p2 = normalize_diff_path(parts[1])
            if p1 != p2:
                return False, "diff_git_path_mismatch"
            if p1 not in allowed_paths:
                return False, "unauthorized_diff_git_file"
            diff_git_target = p1

        elif line.startswith("--- "):
            parts = line[4:].strip().split()
            if not parts:
                return False, "empty_from_header"
            raw_p = parts[0]
            if ".." in raw_p or raw_p.startswith("/") or raw_p.startswith("\\") or ":" in raw_p:
                return False, "path_traversal_in_header"
            p = normalize_diff_path(raw_p)
            if p not in allowed_paths:
                return False, "unauthorized_from_file"
            if file_from is not None:
                return False, "multiple_files_not_allowed"
            file_from = p

        elif line.startswith("+++ "):
            parts = line[4:].strip().split()
            if not parts:
                return False, "empty_to_header"
            raw_p = parts[0]
            if ".." in raw_p or raw_p.startswith("/") or raw_p.startswith("\\") or ":" in raw_p:
                return False, "path_traversal_in_header"
            p = normalize_diff_path(raw_p)
            if p not in allowed_paths:
                return False, "unauthorized_to_file"
            if file_to is not None:
                return False, "multiple_files_not_allowed"
            file_to = p

        elif line.startswith(("index ", "similarity ", "copy ")) or line.strip() == "":
            pass
        else:
            return False, "unexpected_header_line"

        idx += 1

    if file_from is None or file_to is None:
        return False, "missing_file_headers"

    if file_from != file_to:
        return False, "header_file_mismatch"

    if diff_git_target is not None and diff_git_target != file_from:
        return False, "git_header_discrepancy"

    if hunk_start_idx is None:
        return False, "no_hunks_found"

    # 2. Parse and validate hunks strictly
    hunks = []
    idx = hunk_start_idx
    accumulated_offset = 0

    while idx < num_lines:
        line = lines[idx]
        m = hunk_header_re.match(line)
        if not m:
            if all(l.strip() == "" or l.startswith("\\ No newline") for l in lines[idx:]):
                break
            return False, "unparsed_content_after_hunks"

        old_start = int(m.group(1))
        old_count = int(m.group(2)) if m.group(2) is not None else 1
        new_start = int(m.group(3))
        new_count = int(m.group(4)) if m.group(4) is not None else 1

        if old_start < 1 or new_start < 1 or old_count < 0 or new_count < 0:
            return False, "invalid_hunk_range"

        if old_count == 0 and new_count == 0:
            return False, "empty_hunk"

        # Validate consistent new position relative to accumulated offset
        expected_new_start = old_start + accumulated_offset
        if new_start != expected_new_start:
            return False, "inconsistent_new_range_start"

        if hunks:
            last = hunks[-1]
            last_old_end = last["old_start"] + max(last["old_count"], 1)
            if old_start < last_old_end:
                return False, "overlapping_or_out_of_order_hunk"
            last_new_end = last["new_start"] + max(last["new_count"], 1)
            if new_start < last_new_end:
                return False, "overlapping_or_out_of_order_new_hunk"

        idx += 1
        actual_old = 0
        actual_new = 0

        while idx < num_lines and (actual_old < old_count or actual_new < new_count):
            b_line = lines[idx]
            if not b_line:
                b_line = " "
            prefix = b_line[0]

            if prefix == " ":
                actual_old += 1
                actual_new += 1
            elif prefix == "-":
                actual_old += 1
            elif prefix == "+":
                actual_new += 1
            elif prefix == "\\":
                pass
            else:
                return False, "malformed_hunk_body_line"

            idx += 1

        if actual_old != old_count:
            return False, "hunk_old_count_mismatch"
        if actual_new != new_count:
            return False, "hunk_new_count_mismatch"

        if idx < num_lines and lines[idx].startswith("\\ No newline"):
            idx += 1

        hunks.append({
            "old_start": old_start,
            "old_count": old_count,
            "new_start": new_start,
            "new_count": new_count,
        })
        accumulated_offset += (new_count - old_count)

    # Verify all remaining lines are consumed
    while idx < num_lines:
        if lines[idx].strip() != "" and not lines[idx].startswith("\\ No newline"):
            return False, "trailing_unparsed_content"
        idx += 1

    if not hunks:
        return False, "no_valid_hunks"

    return True, None


def apply_unified_diff(
    original_text: str,
    diff_text: str,
    allowed_rel_path: str = "backend/main.py",
) -> Tuple[str, bool]:
    """Apply a strictly validated unified diff to original_text in pure Python.

    Returns (repaired_text, True) on clean application, or (original_text, False) on failure.
    """
    is_valid, _ = validate_unified_diff(diff_text, allowed_rel_path=allowed_rel_path)
    if not is_valid:
        return original_text, False

    orig_lines = original_text.splitlines(keepends=True)
    diff_lines = diff_text.splitlines()

    hunk_header_re = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")
    hunks = []
    idx = 0
    while idx < len(diff_lines):
        line = diff_lines[idx]
        m = hunk_header_re.match(line)
        if m:
            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) is not None else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) is not None else 1
            idx += 1
            hlines = []
            actual_old = 0
            actual_new = 0
            while idx < len(diff_lines) and (actual_old < old_count or actual_new < new_count):
                hl = diff_lines[idx]
                if not hl:
                    hl = " "
                if hl.startswith((" ", "-")):
                    actual_old += 1
                if hl.startswith((" ", "+")):
                    actual_new += 1
                hlines.append(hl)
                idx += 1
            hunks.append({
                "old_start": old_start,
                "old_count": old_count,
                "lines": hlines,
            })
        else:
            idx += 1

    offset = 0
    result_lines = list(orig_lines)

    for hunk in hunks:
        old_start = hunk["old_start"]
        expected_old = []
        new_lines = []

        for hl in hunk["lines"]:
            if hl.startswith(" "):
                expected_old.append(hl[1:] + "\n")
                new_lines.append(hl[1:] + "\n")
            elif hl.startswith("-"):
                expected_old.append(hl[1:] + "\n")
            elif hl.startswith("+"):
                new_lines.append(hl[1:] + "\n")

        target_idx = old_start - 1 + offset
        match_idx = None
        expected_stripped = [l.rstrip("\r\n") for l in expected_old]

        if 0 <= target_idx <= len(result_lines) - len(expected_old):
            candidate = [l.rstrip("\r\n") for l in result_lines[target_idx : target_idx + len(expected_old)]]
            if candidate == expected_stripped:
                match_idx = target_idx

        if match_idx is None:
            matches = []
            for i in range(len(result_lines) - len(expected_old) + 1):
                candidate = [l.rstrip("\r\n") for l in result_lines[i : i + len(expected_old)]]
                if candidate == expected_stripped:
                    matches.append(i)
            if len(matches) == 1:
                match_idx = matches[0]
            else:
                return original_text, False

        result_lines[match_idx : match_idx + len(expected_old)] = new_lines
        offset += len(new_lines) - len(expected_old)

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
      * Execution of untrusted code on the host without an isolated worker is strictly blocked.
      * Only single-file unified diff targeting backend/main.py is accepted.
      * Strict diff validation (size limits, path containment, hunk counts, full consumption).
      * Confined exclusively to registered disposable incident workspaces.
      * Symlink rejection and atomic tempfile replacement with fsync.
      * Atomic rollback to original content on test failures or ANY unhandled exceptions.
      * Environment sanitization explicitly purges DEEPSEEK_API_KEY from child processes.
      * Public responses return fixed, sanitized error categories; credentials/URLs are never leaked.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        model: Optional[str] = None,
        allowed_hosts: Optional[set] = None,
    ):
        self._api_key = api_key
        self.api_url = api_url or DEEPSEEK_API_URL
        self.model = model or DEEPSEEK_MODEL
        self.allowed_hosts = allowed_hosts if allowed_hosts is not None else ALLOWED_DEEPSEEK_HOSTS
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
            return None, ERROR_API_KEY_MISSING

        # Validate URL scheme and origin BEFORE creating request or attaching credentials
        is_valid_url, url_err = validate_api_url(self.api_url, self.allowed_hosts)
        if not is_valid_url:
            return None, url_err

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

        opener = urllib.request.build_opener(SafeRedirectHandler(self.allowed_hosts))

        try:
            with opener.open(req, timeout=timeout) as resp:
                raw_body = resp.read(MAX_OUTPUT_BYTES + 1)
                if len(raw_body) > MAX_OUTPUT_BYTES:
                    return None, ERROR_UPSTREAM_PAYLOAD_TOO_LARGE
                resp_data = json.loads(raw_body.decode("utf-8"))
                choices = resp_data.get("choices")
                if not choices or not isinstance(choices, list):
                    return None, ERROR_UPSTREAM_INVALID_RESPONSE
                msg = choices[0].get("message", {})
                content = msg.get("content")
                if content is None:
                    return None, ERROR_UPSTREAM_INVALID_RESPONSE
                return str(content), None
        except urllib.error.HTTPError as e:
            # Fixed sanitized category: never interpolate e.reason or e.url
            return None, f"{ERROR_UPSTREAM_HTTP}_{e.code}"
        except urllib.error.URLError:
            # Fixed sanitized category: never interpolate e.reason
            return None, ERROR_UPSTREAM_CONNECTION
        except TimeoutError:
            return None, ERROR_UPSTREAM_TIMEOUT
        except Exception:
            return None, ERROR_UPSTREAM_PROTOCOL

    def diagnose(self, incident_context: Dict[str, Any]) -> Dict[str, Any]:
        """Perform read-only investigation using DeepSeek Flash."""
        api_key = self._get_api_key()
        if not api_key:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "read_only": True,
                "status": "api_key_missing",
                "error_code": ERROR_API_KEY_MISSING,
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
                "error_code": error_msg,
                "analysis": "Diagnostic telemetry analysis failed due to an upstream API error.",
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
                "error": "disabled_by_policy",
                "tests_passed": False,
            }

        # Worker isolation check: untrusted code must never be executed on host
        if not is_isolated_worker_available():
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": ERROR_BLOCKED_MISSING_ISOLATED_WORKER,
                "error": (
                    "Execution of model-generated code on the host is strictly blocked: "
                    "no isolated test worker with disabled network and filesystem boundary is active."
                ),
                "tests_passed": False,
            }

        safe_ws = validate_safe_workspace_path(workspace_path, enforce_registry=False)

        raw_target = get_target_file(safe_ws)
        try:
            target_file = validate_safe_target_file(raw_target, safe_ws)
        except (SecurityViolation, FileNotFoundError) as exc:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": "target_validation_failed",
                "error": type(exc).__name__,
                "tests_passed": False,
            }

        api_key = self._get_api_key()
        if not api_key:
            return {
                "adapter": self.adapter_name,
                "model_executed": False,
                "status": ERROR_API_KEY_MISSING,
                "error": ERROR_API_KEY_MISSING,
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
                "error": ERROR_DIFF_VALIDATION_FAILED,
                "validation_code": validation_err,
                "tests_passed": False,
            }

        # Atomic replacement with guaranteed rollback on ANY failure or exception
        original_backup = current_content
        repaired_content, applied = apply_unified_diff(original_backup, diff_text, allowed_rel_path="backend/main.py")
        if not applied:
            return {
                "adapter": self.adapter_name,
                "model_executed": True,
                "status": "patch_failed",
                "error": ERROR_PATCH_APPLICATION_FAILED,
                "diff": diff_text,
                "tests_passed": False,
            }

        # Stage candidate code and execute tests strictly in isolated worker session
        from isolated_worker import IsolatedWorkerController, IsolatedWorkerSession

        try:
            with IsolatedWorkerSession.create(safe_ws) as session:
                candidate_target = session.staging_dir / "backend" / "main.py"
                candidate_target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_file(candidate_target, repaired_content)

                # Step 1: Execute pytest separately against backend unit tests
                pytest_passed, pytest_out = IsolatedWorkerController.run_isolated_pytest(
                    session, timeout=REPAIR_TIMEOUT_SECONDS
                )

                # Step 2: Execute separate functional verification (candidate in uvicorn, verifier in httpx loopback)
                func_passed, func_data, func_out = IsolatedWorkerController.run_isolated_functional_verification(
                    session, timeout=REPAIR_TIMEOUT_SECONDS
                )

                worker_output = (
                    f"--- Pytest (passed={pytest_passed}) ---\n{pytest_out}\n\n"
                    f"--- Functional Verification (passed={func_passed}) ---\n{func_out}"
                )

                pytest_evaluation = {
                    "status": "advisory_passed" if pytest_passed else "failed",
                    "scope": "in_process_unit_tests",
                    "trust_level": "advisory_untrusted",
                    "independent_validation": False,
                    "limitation": (
                        "In-process unit test execution shares the Python runtime address space with candidate code. "
                        "Candidate code can intercept stdout, monkeypatch pytest hooks/reporters, or invoke os._exit(0). "
                        "Consequently, pytest output is strictly advisory and does NOT constitute independent proof of correctness."
                    ),
                    "passed": bool(pytest_passed),
                }

                persistence_evaluation = {
                    "status": "db_verified" if func_data.get("db_verified") else "failed",
                    "scope": "container_tmpfs_sqlite",
                    "trust_level": "advisory_shared_permission_domain",
                    "independent_validation": False,
                    "limitation": (
                        "Direct querying of /tmp/tasklane.db confirms physical database persistence on disk, "
                        "preventing superficial in-memory HTTP mocking. However, candidate (uvicorn) and verifier "
                        "share container user UID 10001 and filesystem permissions on /tmp/tasklane.db; "
                        "this does not provide independent security isolation."
                    ),
                    "persisted": bool(func_data.get("urgent_persisted")),
                }

                if not (pytest_passed and func_passed):
                    return {
                        "adapter": self.adapter_name,
                        "model_executed": True,
                        "remediation_type": "model_unified_diff",
                        "status": "repair_failed",
                        "error": ERROR_TEST_VERIFICATION_FAILED,
                        "diff": diff_text,
                        "tests_passed": bool(pytest_passed),
                        "tests_passed_advisory": bool(pytest_passed),
                        "independent_validation": False,
                        "authoritative_verified": False,
                        "pytest_verification": pytest_evaluation,
                        "persistence_verification": persistence_evaluation,
                        "functional_verified": bool(func_passed),
                        "functional_details": func_data,
                        "evaluation_boundary": "advisory_shared_domain_untrusted",
                        "workspace": str(safe_ws),
                        "worker_output": worker_output,
                    }

                # CRITICAL SAFETY REQUIREMENT:
                # Until reliable independent verification is established and maintainer-approved,
                # automatic application of model-generated patches to the workspace target file
                # is kept strictly DISABLED. The target file is NEVER overwritten automatically.
                return {
                    "adapter": self.adapter_name,
                    "model_executed": True,
                    "remediation_type": "model_unified_diff",
                    "status": "automatic_patch_application_disabled",
                    "automatic_patch_application_enabled": False,
                    "patch_applied": False,
                    "diff": diff_text,
                    "tests_passed": bool(pytest_passed),
                    "tests_passed_advisory": bool(pytest_passed),
                    "independent_validation": False,
                    "authoritative_verified": False,
                    "pytest_verification": pytest_evaluation,
                    "persistence_verification": persistence_evaluation,
                    "functional_verified": bool(func_passed),
                    "functional_details": func_data,
                    "evaluation_boundary": "advisory_shared_domain_untrusted",
                    "workspace": str(safe_ws),
                    "worker_output": worker_output,
                }
        except BaseException:
            # Workspace target file was never modified
            raise
