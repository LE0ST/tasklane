# Issue #4 — Local On-Call Diagnostic Responder and Mock Recovery

**Historical baseline (October 26, 2026):** [PR #9](https://github.com/LE0ST/tasklane/pull/9) was human-authorized and squash-merged to `main` as [`7c73446`](https://github.com/LE0ST/tasklane/commit/7c7344690bf3260297301a14f434d00cb6eb123a). [Merged-main GitHub Actions 40273628271](https://github.com/LE0ST/tasklane/actions/runs/40273628271) passed all four jobs. The local Grafana alert-driven **read-only diagnosis** is implemented; deterministic mock fixture repair was separately tested. At that merged baseline, no live LLM-authored fix or independently isolated write-capable agent repair was demonstrated. The later supervised drill was merged in [PR #15](https://github.com/LE0ST/tasklane/pull/15) as `65378015cad42e107148ec0f6618472b54a1e36b`; its [push-to-main CI](https://github.com/LE0ST/tasklane/actions/runs/40618254717) passed all four jobs. The maintainer-reported run is recorded below.

---

## 1. Mission & Rubric Alignment

According to the official **AI Dev Tools Zoomcamp 2026** course rubric:
- **On-Call Agent (2 points):** An agent wakes up on an alert and **fixes the issue**. A passive webhook receiver that only outputs diagnostic logs is insufficient.

TaskLane implements a bounded, local-only **diagnostic responder**, a separate mock exercise, and a supervised live-provider drill merged in PR #15. It does not yet demonstrate an autonomous AI fix:
1. **Wakes Up on Alert:** Receives real local Grafana `TaskLane5xxRate` webhook notifications on `POST /alerts` (port 8001).
2. **Read-Only Diagnosis by Default:** Correlates local Prometheus metrics (`tasklane_task_5xx_errors_total`), structured Loki logs (`{service_name="tasklane-api"}` for `/api/tasks` 5xx), and Tempo SERVER ERROR spans without granting write permissions (`write_authorized: false`).
3. **Strict Network-Facing Read-Only Policy:** Network-facing repair is disabled by default in normal service mode (`/authorize-repair` returns HTTP 403 `repair_disabled`). HTTP repair is permitted only when explicitly enabled locally for isolated demonstration via `TASKLANE_ALLOW_HTTP_REPAIR=true`.
4. **Explicit Authenticated Operator Gate:** When HTTP repair is enabled, remediation requires genuine single-use, cryptographically random incident token authentication via dedicated local operator CLI (`operator_cli.py`) with strict boolean `confirm: true`. Incorrect token attempts never consume legitimate approval tokens.
5. **Service-Owned Disposable Workspace Containment:** Defects are introduced and repaired strictly within private service-owned disposable directories (`tasklane_oncall_service`). Workspaces are registered to incident IDs. Source files are copied using an explicit POSITIVE file manifest (`DISPOSABLE_WORKSPACE_MANIFEST`), strictly excluding `.env`, credentials, `conftest.py`, and unrelated tests.
6. **Application Policy vs Kernel Sandbox:** Subprocess directory containment, environment variable filtering, and post-execution output truncation are application-level policy controls on the host, not a kernel-enforced OS sandbox (such as gVisor, microVMs, or network-disabled containers). Host subprocess execution runs under the current host process identity (non-root `oncall` execution is enforced only when running within the containerized service).
7. **Truthful Reporting & Disclosures:** CI runs use `MockAgentAdapter` for deterministic safety verification (fixture marker rollback, not AI). Write-capable live model execution (`CodexAgentAdapter`) is kept explicitly **DISABLED** pending maintainer authorization. Full autonomous AI remediation remains unimplemented.
8. **Isolated Container Test Worker & Evaluation Boundary Limitations:** Candidate model diffs are evaluated inside an ephemeral container (`tasklane-isolated-worker:local`) using an immutable digest identity, with `--network none`, `--read-only` root, non-root user `10001:10001`, dropped capabilities (`--cap-drop ALL`), and `--log-driver none`. Candidate execution (`uvicorn`) is isolated from the functional verifier (`_trusted_verifier.py`).
   - *Worker staging permissions:* On POSIX, allowlisted staged files are readable by the unprivileged worker UID and mounted read-only; the default service-owned staging parent is private to the host user. Callers supplying a custom staging parent must keep it private. No credentials or `.env` files belong in the manifest.
   - *Unit test boundary limitation:* Pytest executes candidate code in-process, sharing memory and file descriptors; candidate code could intercept reporters or forge stdout lines (e.g. '1 passed'). In-process test outputs are therefore classified as untrusted/advisory (`trust_level: advisory_untrusted`). They are never reported as independent proof of correctness.
   - *Persistence verification scope & shared permissions:* Direct querying of SQLite `/tmp/tasklane.db` confirms physical database persistence on disk (preventing in-memory HTTP response mocking). However, candidate and verifier share the same container user UID 10001 and filesystem read-write permissions on `/tmp/tasklane.db`. This does NOT constitute independent security isolation between candidate and verifier.
9. **Automatic Patch Application Disabled:** Because in-process unit tests and shared filesystem permissions lack an unforgeable, independent verification boundary, automatic application of model-generated patches to the workspace target file is strictly **DISABLED** by policy (`status: automatic_patch_application_disabled`). Workspace files are never overwritten automatically.
10. **Resource Lifecycle, Crash Limitations & Recovery:**
   - *In-Process Lifetime:* Active sessions and container tracking sets reside in RAM and do NOT survive process crashes (SIGKILL, power failure, or OOM).
   - *No External Daemon Guarantee:* If the Docker daemon is unreachable or the process is killed abruptly, orphaned containers remain on the Docker host. No automatic OS watchdog or supervisor daemon exists.
   - *Recovery:* The pending registry is best-effort, can be corrupt or overwritten by concurrent processes, and cannot establish cross-process activity. Automatic orphan recovery is disabled and reports unknown state. Orderly sessions still attempt cleanup of their own resources.
   - *Manual recovery:* List labeled workers and inspect each resource individually. Check its full Docker ID, name, `tasklane.session_id`, state and owner with the operator responsible for concurrent sessions. Created, paused and running containers may all be active. Remove a specific resource only after establishing it is no longer in use. PID alone is not proof of inactivity.


---

## 2. System Architecture

```mermaid
flowchart TD
    subgraph Observability Stack
        Grafana["Grafana Alerting<br/>TaskLane5xxRate"]
        Prom["Prometheus (:9090)<br/>5xx Error Rate"]
        Loki["Grafana Loki (:3100)<br/>Structured Request Logs"]
        Tempo["Grafana Tempo (:3200)<br/>Trace Metadata"]
    end

    subgraph Incident Response Subsystem (:8001)
        Receiver["Alert Receiver<br/>POST /alerts<br/>(Deduplication & Validation)"]
        Collector["Telemetry Collector<br/>(Structured Loki + Tempo Validation)"]
        DiagGate["Read-Only Diagnosis<br/>(write_authorized: false)"]
        TokenStore["Local Token Storage<br/>(auth_incident.token)"]
        AuthGate["Operator Authorization Gate<br/>POST /authorize-repair<br/>(X-Operator-Token Header)"]
        OpCLI["Local Operator CLI<br/>(operator_cli.py)"]
        Registry["Workspace Registry<br/>(Incident ID -> Path)"]
        Sandbox["Disposable Test Runner<br/>(Application Policy; Not OS Isolation)"]
        Adapter["Agent Adapter<br/>(Mock; Codex Writes Disabled)"]
    end

    subgraph Service-Owned Disposable Containment
        DispWS["Disposable Workspace<br/>(tasklane_oncall_service/workspace_...)<br/>Target: backend/main.py"]
    end

    Grafana -->|POST /alerts (firing)| Receiver
    Receiver --> Collector
    Collector --> Prom
    Collector --> Loki
    Collector --> Tempo
    Collector --> DiagGate
    DiagGate -->|Read-Only| Adapter
    Receiver -->|Local Opt-In Mock Drill Only| TokenStore
    Receiver -->|Local Opt-In Mock Drill Only| Registry

    OpCLI -->|Read Local Token| TokenStore
    OpCLI -->|Authenticated POST| AuthGate
    AuthGate -->|Consume Token & Verify Registry| Sandbox
    Sandbox -->|Restricted Subprocess; Not Kernel Sandbox| Adapter
    Adapter -->|Apply Code Diff| DispWS
    Sandbox -->|Run Pytest & HTTP Verification| DispWS
```

---

## 3. Security Controls & Sandbox Boundaries

All incident response operations enforce strict defense-in-depth guardrails:

| Control | Implementation | Verification |
|:---|:---|:---|
| **Webhook Input Validation** | `incident-response/responder.py` | Rejects payloads > 64KB, malformed JSON, empty lists, and unrelated alerts (e.g. `CPUAlert`). Safely resets to `idle` on errors. |
| **Deduplication & Concurrency Guard** | In-memory mutex state lock | Prevents duplicate firing notifications from initiating overlapping runs. At most one active repair at a time. |
| **Strict Read-Only Default** | Enforced in `_handle_alert_webhook()` | `POST /alerts` sets `write_authorized: false` and cannot modify source files or spawn write processes. |
| **Network-Facing Repair Policy** | `ALLOW_HTTP_REPAIR` | In normal service mode, `/authorize-repair` returns HTTP 403 `repair_disabled`. Enabled only for local isolated drills via `TASKLANE_ALLOW_HTTP_REPAIR=true`. |
| **Atomic Token Authentication** | `verify_token_match()` & `consume_token()` | Token verification and state transitions are executed atomically under lock. Incorrect token attempts NEVER consume legitimate tokens. |
| **Strict Boolean Confirm** | `_handle_authorize_repair()` | Requires `confirm is True` (rejects strings `"false"`, `"true"`, or truthy non-booleans). |
| **Clean Resolution Handling** | `_handle_alert_webhook()` | Resolved notifications cannot re-enable repairs while earlier repair is active; resolution during diagnosis is never overwritten by delayed completion. |
| **Workspace Ownership & Registry** | `validate_safe_workspace_path()` | Enforces registration binding to incident ID; rejects root itself, unregistered paths, parent escapes (`..`), and symlinks. |
| **Positive File Manifest** | `DISPOSABLE_WORKSPACE_MANIFEST` | Copies ONLY files in explicit positive manifest; strictly excludes `conftest.py`, `.env`, credentials, unrelated tests, and binaries. |
| **Minimal Environment Allowlist** | `sanitize_process_environment()` | Inherits only allowlisted system keys; purges `AWS_*`, `DATABASE_URL`, `TEST_DATABASE_URL`, `HOME`, `PYTHONPATH`. Rejects `LD_PRELOAD`, `PYTEST_ADDOPTS`, proxy overrides. |
| **Policy Boundary vs Kernel Sandbox** | Application-level policy on host | Directory `cwd` containment and env filtering are application policy controls, not a kernel sandbox. Non-root `oncall` execution is present only inside the containerized service image, not when executing host subprocesses. Direct TCP network egress and kernel memory bounds are not restricted on the host. |
| **Bounded Retention** | `prune_stale_resources()` | Retains at most 3 workspaces, 10 incident history records, and automatically prunes expired/consumed tokens. |
| **Process Timeout & Output Capture Limitations** | Subprocess timeout and post-run truncation | Only the returned output is truncated to 64KB **after** capture; stdout/stderr pipes themselves are not memory-bounded. Descendant termination and network isolation are not independently enforced. This is not a secure executor for untrusted generated code. |

---

## 4. Telemetry Correlation & Real Grafana Delivery

### 4.1 Structured Loki Log & Tempo Span Validation
The telemetry collector (`collector.py`) enforces rigorous evidence correlation:
1. Queries Loki with `{service_name="tasklane-api"}` (not generic substrings).
2. Parses structured JSON logs, filtering for `/api/tasks` routes with HTTP status codes 500–599.
3. Extracts and normalizes the 32-character hex trace ID (rejecting all-zero dummy values).
4. Queries Tempo `/api/traces/{trace_id}` and validates:
   - Span kind is `SERVER` (2).
   - Status code is `ERROR` (2).
   - Attributes contain matching HTTP 5xx response status code.
5. Missing evidence explicitly sets `has_correlated_evidence: false` with missing items listed; never silently passes as successful diagnosis.

### 4.2 Real Grafana Delivery Verification
The automated local stack CI pipeline verifies:
1. `POST /api/tasks` triggers a local failure (503).
2. Prometheus increments `tasklane_task_5xx_errors_total`.
3. Grafana evaluates `TaskLane5xxRate` and fires.
4. Grafana delivers webhook to `oncall-responder:8001/alerts`.
5. Responder records read-only diagnosis with correlated Loki log and Tempo trace evidence.
6. Alert resolves and responder updates state without locking future incidents.
Verified by automated integration test: `incident-response/oncall_smoke.py`.

---

## 5. Deliberate Defect Fixture & Verified Recovery

The defect fixture (`fixtures.py`) introduces a narrowly scoped regression affecting valid urgent-priority task creation:
```python
# --- ONCALL-BUG-FIXTURE-START ---
    if getattr(task_in.priority, "value", task_in.priority) == "urgent":
        raise RuntimeError("UNCAUGHT REGRESSION: Failed to dispatch urgent task")
# --- ONCALL-BUG-FIXTURE-END ---
```

### Verified HTTP Before/After Behavior
1. **Normal Task Creation:** `POST /api/tasks` with `priority="medium"` succeeds with HTTP 201 before and after repair.
2. **Urgent Task Creation Before Repair:** Produces unhandled exception $\rightarrow$ **HTTP 500 Internal Server Error**.
3. **Remediation:** Operator authorizes repair via local operator CLI $\rightarrow$ deterministic removal of defect marker $\rightarrow$ unified diff emitted $\rightarrow$ unit tests pass.
4. **Urgent Task Creation After Repair:** Returns **HTTP 201 Created**.
5. **Persistence Verification:** Querying the local database confirms the urgent task is persisted.

---

## 6. Truthful Attribution & Model Execution Disclosure

- **CI Safety & Policy Determinism:** Continuous Integration environments use `MockAgentAdapter` to execute regression tests and sandbox boundary checks reliably without network flakes, API costs, or credential exposures.
- **Mock Adapter Disclosure:** `MockAgentAdapter` is explicitly documented as a deterministic test fixture adapter that performs marker rollback for containment verification. It is **NOT** represented as an autonomous AI or LLM repair.
- **Codex Agent Adapter:** `CodexAgentAdapter` is implemented with `--sandbox read-only` diagnosis and `--sandbox workspace-write` repair execution. Subprocess environment is strictly sanitized via `sanitize_process_environment()`.
- **Write-Capable Codex Execution Disabled:** In accordance with repository safety policies, write-capable Codex execution is **DISABLED** pending explicit maintainer authorization and verification of an independently isolated worker. No live write-capable model execution was performed.
- **DeepSeek Flash Agent Adapter & Controlled Local Demonstration:**
  - *Adapter Design:* `DeepSeekAgentAdapter` implements read-only diagnosis (`diagnose`) and isolated remediation proposal (`repair`). API calls strictly enforce HTTPS, allowed host origins (`api.deepseek.com`), bounded response size, sanitized error reporting, and stripped authorization headers across redirects.
  - *5-Stage Controlled Demonstration Flow (`model_drill.py`):*
    1. Simulated alert context and simulated read-only diagnosis.
    2. Defect injection strictly into disposable workspace & BEFORE HTTP verification (normal=201, urgent=500).
    3. Offline synthetic diff or explicit live-provider diff proposal & isolated container evaluation: automatic patch application is disabled by policy; target file remains defective.
    4. Explicit human review and approval: operator inspects diff and confirms application strictly to disposable workspace.
    5. AFTER verification: success requires the worker to exit successfully and its HTTP and SQLite checks to pass. A failed worker is reported as failed even if its parsed output appears positive; if review is withheld, AFTER is not run. Main repository checkout and production are never modified.
  - *Offline Simulation vs Live Model Execution:* The default mode generates a synthetic diff and cannot invoke a provider. `live_provider` explicitly sends the disposable target file and incident context to DeepSeek to request a diff. Provider errors, malformed responses, invalid diffs and failed candidate evaluation stop the drill before review or application. Automated tests intercept transport and use dummy keys. A separate maintainer-reported live run is recorded below.
  - *Verifier Limitations:* Loopback HTTP checks run in a separate process inside the same container, not a separate security domain. In-process pytest shares Python runtime with candidate code and is advisory only. SQLite inspection shares UID 10001 and filesystem permissions with the candidate, which can alter the database. For the DeepSeek adapter, `tests_passed` remains false on both successful and failed functional verification; `tests_passed_advisory` reports pytest only. The responder requires `tests_passed` for success and therefore cannot present this advisory result as a verified repair.
  - *Human Approval Limitations:* The CLI displays the diff, workspace and hashes, then asks the operator to type YES. The programmatic callback is a trust boundary: tests can simulate confirmation but cannot prove a human reviewed it. Preapproval is rejected. The current-process workspace registration and hashes are checked again before writing; symlinks are rejected. Portable filesystem operations cannot eliminate a race between the last check and replacement, nor protect against a hostile process with the same user privileges. Run only one supervised drill at a time in a private workspace with no concurrent writers: the shared workspace retention and disk registry are not coordinated across processes and can remove another drill's workspace.
- **Rubric Status & Review Boundary:** Autonomous wake-up, Prometheus/Loki/Tempo correlation, and read-only diagnosis are verified against real Grafana alerts. Disposable repair containment and recovery verification are verified via deterministic simulation and tests. Live autonomous AI code remediation remains unproven; the live provider mode is a supervised local experiment, not autonomous remediation. The limited diagnosis-only PR was approved and merged; full autonomous AI code remediation is still unproven.

### Maintainer-reported supervised live run

The maintainer reported one local `live_provider` run at feature-branch commit `2cc525107d2cf977e8c9dc9e0bf68b0f478b5a65`. This is a report supplied by the maintainer, not an independently inspected recording or CI run. The drill implementation and this report were later merged in PR #15; the run itself occurred at the feature-branch commit. No run date or external evidence artifact was supplied.

- **Provider:** The run reported `provider_response=reported_received`. The presented diff removed the injected regression block that raised `RuntimeError` for urgent tasks. The report associates the diff with the live-provider response.
- **Human review and application:** The maintainer inspected the diff and entered `YES`. The reported fields were `approval=approved` and `application=applied`; application was to a disposable workspace.
- **After verification:** The run reported `after=passed`, `success=true`, and `pytest_advisory=true`. The report contains no raw worker output or separate HTTP/SQLite observations; do not treat it as an independently verified test transcript.
- **Limits:** Pytest imports candidate code in-process and is advisory. The HTTP verifier is a separate process inside the same container, not a separate security domain. SQLite inspection shares the candidate's user identity and filesystem permissions. These checks do not establish an independent security boundary or prove general correctness.

The incident context for this drill is simulated. No evidence supplied for this run shows that a real alert triggered it, that the model applied the patch autonomously, or that production was repaired. The earlier Grafana-triggered read-only diagnosis and mock repair are separate evidence. The live experiment requires one supervised execution, human review, and no concurrent writers.

---

## 7. Reproducible Local Commands

### 7.1 Running Controlled Model Remediation Drill
Execute the controlled 5-stage demonstration flow for DeepSeekAgentAdapter with explicit human approval, isolated worker evaluation, and before/after HTTP verification:

```bash
uv run --project backend pytest incident-response/tests/test_model_drill.py -v
uv run --project backend python incident-response/model_drill.py
```

To opt into a supervised live provider experiment, an operator can set the key in the current shell without displaying it or saving it in the repository. For example, in Bash:

```bash
read -rs -p 'DeepSeek API key: ' DEEPSEEK_API_KEY; echo
export DEEPSEEK_API_KEY
uv run --project backend python incident-response/model_drill.py --mode live_provider
unset DEEPSEEK_API_KEY
```

On PowerShell, use a secure prompt and remove the environment variable afterwards:

```powershell
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'DeepSeek API key' -AsSecureString)).Password
uv run --project backend python incident-response/model_drill.py --mode live_provider
Remove-Item Env:DEEPSEEK_API_KEY
```

The live request sends the disposable `backend/main.py` content and synthetic incident context to DeepSeek; review the data and provider terms before running it. The CLI prints the proposed diff and hashes, then requires the operator to type `YES`; any other answer withholds application. A successful AFTER result still has the shared-permission verification limits above. Do not run concurrent drills or writers. No live DeepSeek execution or human-reviewed live repair has been demonstrated by the automated tests.

### 7.2 Recovering Orphaned Containers Safely
Automatic orphan recovery is disabled because the registry is not a reliable cross-process ownership record. Do not use bulk removal commands. List and inspect resources individually:

```bash
docker ps -a --filter "label=tasklane.component=isolated-worker" --no-trunc
docker container inspect <individual-container-id>
```

Confirm the full ID, name, labels, session and activity with the responsible operator before deciding whether to remove that individual container. Unknown or corrupt registry state is not evidence of cleanup.

### 7.3 Running Automated Unit & Security Tests
Verify all 54 responder tests at merged PR #9 baseline, covering alert validation, atomic token auth, single-use consumption, incorrect-token preservation, workspace registry, positive manifest isolation, unsafe override rejection, and mock remediation:

```bash
uv run --project backend pytest incident-response/tests -v
```

### 7.2 Dedicated Operator CLI (Container vs Host Boundary)
The operator CLI provides an out-of-band path for authorized remediation.

> [!IMPORTANT]
> In normal default service mode, network-facing repair authorization is disabled (`repair_disabled`).
> Authorizing repair via the operator CLI returns HTTP 403 `repair_disabled` unless the responder was
> explicitly launched with `TASKLANE_ALLOW_HTTP_REPAIR=true` (as in an authorized local drill).

- **When running inside Docker** (tokens reside in the container's private `/tmp/tasklane_oncall_service`):
  ```bash
  docker exec tasklane-oncall-responder python operator_cli.py authorize --incident-id <ID> --confirm
  ```
- **When running standalone locally**:
  ```bash
  python incident-response/operator_cli.py authorize --incident-id <ID> --confirm
  ```

### 7.3 Running the End-to-End Incident Drill
The standalone **mock-only** drill exercises deterministic fixture remediation in a disposable workspace (with `TASKLANE_ALLOW_HTTP_REPAIR=true`). It is strictly for trusted local demonstration, not an LLM-generated fix or a safely confined worker for arbitrary code:

```bash
# Terminal 1: Start the on-call responder with local HTTP repair enabled
TASKLANE_ALLOW_HTTP_REPAIR=true python incident-response/responder.py 8001

# Terminal 2: Execute the full remediation drill
python incident-response/drill.py
```

### 7.4 Running with Docker Compose Overlay
Start the on-call responder alongside the local observability stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.oncall.yml up --build -d
```

Verify real Grafana delivery and telemetry correlation:
```bash
python observability/incident_smoke.py
python incident-response/oncall_smoke.py
```

Tear down:
```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.oncall.yml down -v
```

---

## 8. Evidence Checklist

- [x] Lightweight local responder on port 8001 (`POST /alerts`, `GET /health`, `GET /status`, `POST /authorize-repair`)
- [x] Rejection of malformed, oversized (>64KB), and unrelated alert payloads; clean recovery to `idle` on errors
- [x] Deduplication of rapid notifications to prevent overlapping repairs
- [x] Read-only diagnosis by default (`write_authorized: false`)
- [x] Network-facing repair disabled by default (`repair_disabled` HTTP 403 in normal service mode)
- [x] Atomic token verification and state transition under mutex lock
- [x] Incorrect token attempts NEVER consume legitimate approval tokens
- [x] Strict boolean confirmation required (`confirm is True`)
- [x] Service-owned workspace directory (`tasklane_oncall_service`) with incident registry
- [x] Positive file manifest (`DISPOSABLE_WORKSPACE_MANIFEST`) strictly excluding `.env`, credentials, `conftest.py`, unrelated tests, and binaries
- [x] Minimal environment variable allowlist; purges `AWS_*`, `DATABASE_URL`, `TEST_DATABASE_URL`, `HOME`, `PYTHONPATH`; rejects `LD_PRELOAD`, `PYTEST_ADDOPTS`, proxy overrides
- [x] Application policy boundary clearly distinguished from OS kernel sandboxes
- [x] Bounded resource retention: <= 3 workspaces, <= 10 incident history records, automatic token expiration
- [x] Structured Loki query `{service_name="tasklane-api"}` with task route 5xx parsing
- [x] Tempo SERVER ERROR span validation with 5xx status metadata
- [x] Real Grafana webhook delivery to on-call responder verified (`oncall_smoke.py`)
- [x] Defect fixture references `task_in.priority` and demonstrates real HTTP 500 before $\rightarrow$ 201 after + DB persistence
- [x] Truthful disclosures: `MockAgentAdapter` is deterministic test fixture; live Codex write repair kept disabled
- [x] 40 unit and security tests in `incident-response/tests` (100% passing)
- [x] Zero regressions on existing 76 backend unit tests, PostgreSQL integration, frontend build, Playwright E2E, and Issue #3 drill
