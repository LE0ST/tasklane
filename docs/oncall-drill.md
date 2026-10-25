# Issue #4 — Autonomous On-Call Incident Responder & Bounded Remediation

**Status:** Architecture, alert receiver, sandbox containment, replaceable agent adapter, and reproducible incident drill implemented and verified in branch `project1/oncall-responder`. Draft PR targets `main` referencing Issue #4.

---

## 1. Mission & Rubric Alignment

According to the official **AI Dev Tools Zoomcamp 2026** course rubric:
- **On-Call Agent (2 points):** An agent wakes up on an alert and **fixes the issue**. A passive webhook receiver that only outputs diagnostic logs is insufficient.

TaskLane implements a bounded on-call remediation system adhering to least-privilege principles:
1. **Wakes Up on Alert:** Receives local Grafana `TaskLane5xxRate` webhook notifications on `POST /alerts` (port 8001).
2. **Read-Only Diagnosis by Default:** Queries local Prometheus, Loki, and Tempo to correlate metrics, structured logs, and trace IDs without granting file modification permissions.
3. **Explicit Operator Authorization Gate:** Remediations can only execute when an operator explicitly authorizes write-capable execution via `POST /authorize-repair`.
4. **Disposable Workspace Containment:** Defects are introduced and repaired strictly within isolated, temporary disposable workspaces. The main application checkout, `main` branch, Render production, and Neon database are **never** mutated.
5. **Truthful Reporting:** CI runs use `MockAgentAdapter` for deterministic safety verification; the live `CodexAgentAdapter` is provided with strict sandbox and timeout guardrails.

---

## 2. System Architecture

```mermaid
flowchart TD
    subgraph Observability Stack
        Grafana["Grafana Alerting<br/>TaskLane5xxRate"]
        Prom["Prometheus (:9090)<br/>5xx Error Rate"]
        Loki["Grafana Loki (:3100)<br/>Sanitized Request Logs"]
        Tempo["Grafana Tempo (:3200)<br/>Trace Metadata"]
    end

    subgraph Incident Response System (:8001)
        Receiver["Alert Receiver<br/>POST /alerts<br/>(Deduplication & Validation)"]
        Collector["Telemetry Collector<br/>(Sanitizes & Correlates)"]
        DiagGate["Read-Only Diagnosis<br/>(write_authorized: false)"]
        AuthGate["Operator Approval Gate<br/>POST /authorize-repair<br/>(LE0ST confirmation)"]
        Sandbox["Disposable Sandbox Runner<br/>(Path & Credential Containment)"]
        Adapter["Agent Adapter<br/>(Mock / Codex CLI)"]
    end

    subgraph Restricted Environment
        DispWS["Disposable Workspace<br/>(/tmp/tasklane_disposable/...)<br/>Target: backend/main.py"]
    end

    Grafana -->|POST /alerts (firing)| Receiver
    Receiver --> Collector
    Collector --> Prom
    Collector --> Loki
    Collector --> Tempo
    Collector --> DiagGate
    DiagGate -->|Read-Only| Adapter

    AuthGate -->|Operator Token & Safe Path| Sandbox
    Sandbox -->|Restricted Writes| Adapter
    Adapter -->|Apply Code Diff| DispWS
    Sandbox -->|Run Pytest| DispWS
```

---

## 3. Security Controls & Sandbox Boundaries

All incident response operations enforce strict defense-in-depth guardrails:

| Control | Implementation | Verification |
|:---|:---|:---|
| **Webhook Input Validation** | `incident-response/responder.py` | Rejects payloads > 64KB, malformed JSON, `resolved` alerts, empty lists, and unrelated alerts (e.g. `CPUAlert`). |
| **Deduplication** | In-memory mutex state lock | Prevents duplicate firing notifications from initiating overlapping diagnostic or repair runs. |
| **Read-Only Default** | Enforced in `_handle_alert_webhook()` | `POST /alerts` sets `write_authorized: false` and cannot modify source files or spawn write processes. |
| **Operator Approval Gate** | `_handle_authorize_repair()` | Requires `operator: "LE0ST"` and `confirm: true`. Unconfirmed requests return HTTP 403. |
| **Path Traversal Guard** | `validate_safe_workspace_path()` | Resolves realpaths; rejects parent escapes (`..`), symlinks outside base, and attempts to target the main checkout or system root. |
| **Credential Sanitization** | `sanitize_process_environment()` | Purges `GITHUB_TOKEN`, `DATABASE_URL`, `RENDER_API_KEY`, and API secrets from child processes. |
| **Forbidden Flags** | `run_sandboxed_command()` | Rejects `--dangerously-bypass-approvals-and-sandbox` unconditionally. |
| **Process Timeouts** | Subprocess timeout enforcement | Bounded execution: 30s diagnosis timeout, 45s-60s command timeout. |

---

## 4. Reproducible Local Commands

### 4.1 Running Automated Unit & Security Tests
Verify all 28 automated tests covering alert rejection, deduplication, path escape prevention, credential redaction, and mock remediation:

```bash
uv run pytest incident-response/tests -v
```

### 4.2 Running the End-to-End Incident Drill
The standalone drill exercises the complete lifecycle in an isolated workspace:

```bash
# Terminal 1: Start the on-call responder
python incident-response/responder.py 8001

# Terminal 2: Execute the full remediation drill
python incident-response/drill.py
```

### 4.3 Running with Docker Compose Overlay
To start the optional on-call responder container alongside the local observability stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.oncall.yml up --build -d
```

Verify service status:
```bash
curl -sf http://localhost:8001/health
curl -sf http://localhost:8001/status
```

Tear down:
```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml -f docker-compose.oncall.yml down -v
```

---

## 5. Verified Incident Remediation Evidence

The drill executes a verified 7-stage remediation workflow:

```
======================================================================
TASKLANE ISSUE #4: AUTONOMOUS ON-CALL REMEDIATION DRILL
======================================================================

[Step 1/7] Checking on-call responder health...
 PASS: Responder online at http://127.0.0.1:8001 (service=tasklane-oncall-responder)

[Step 2/7] Provisioning isolated disposable workspace with deterministic bug fixture...
 PASS: Disposable workspace created at: C:\Users\RENEC\AppData\Local\Temp\workspace_e67b21db4e68
 PASS: Bug fixture injected strictly in disposable copy (main checkout untouched).

[Step 3/7] BEFORE: Verifying task creation failure in disposable workspace...
 PASS: Verified failing baseline: tests failed as expected due to injected defect.

[Step 4/7] Simulating Grafana TaskLane5xxRate alert firing webhook...
 PASS: Alert received. Read-only diagnosis performed (adapter=mock_agent).
       Root cause finding: Unhandled exception detected during task creation pipeline in backend/main.py
       Write authorization: False (strictly denied by default)

[Step 5/7] Requesting explicit operator repair authorization...
 PASS: Operator authorization granted by LE0ST.
 PASS: Repair executed in disposable workspace (status=repaired).

[Step 6/7] REPAIR DIFF: Inspecting agent-produced modification...
--- BEGIN SANITIZED DIFF ---
  --- a/main.py
  +++ b/main.py
  @@ -110,10 +110,6 @@
       summary="Create a new task (strictly placed in backlog)",
   )
   def create_task(task_in: TaskCreate, db: Session = Depends(get_db)):
  -# --- ONCALL-BUG-FIXTURE-START ---
  -    if task.priority == "urgent":
  -        raise RuntimeError("UNCAUGHT REGRESSION: Failed to dispatch urgent task")
--- END SANITIZED DIFF ---
 PASS: Verified genuine code modification applied to disposable workspace.

[Step 7/7] AFTER: Verifying test suite passes and application recovers...
 PASS: All automated backend tests PASSED in the repaired disposable workspace.
 PASS: User-facing task creation behavior verified restored.

======================================================================
ON-CALL DRILL COMPLETED SUCCESSFULLY
Adapter Used: mock_agent
EVIDENCE NOTE: Executed with deterministic MockAgentAdapter (CI/policy compliant).
======================================================================
```

---

## 6. Truthful Attribution & Model Execution Disclosure

- **CI Determinism:** Continuous Integration environments use `MockAgentAdapter` to execute regression tests and sandbox boundary checks reliably without network flakes, API costs, or secret exposures.
- **Genuine AI Integration:** `CodexAgentAdapter` is implemented in `incident-response/agent_adapter.py`, wrapping the authentic `codex exec` CLI with `--sandbox read-only` and `--sandbox workspace-write`.
- **Known Limitations:**
  - Automated non-interactive CLI invocations on Windows without background daemon setup may experience stdin waiting delays or require elevated AppContainer configurations.
  - To prevent false claims of autonomous AI repairs, the system explicitly logs the active adapter name. Live LLM execution is reported as a candidate adapter rather than falsely claimed as the sole verification method.

---

## 7. Evidence Checklist

- [x] Lightweight local responder on port 8001 (`POST /alerts`, `GET /health`, `GET /status`, `POST /authorize-repair`)
- [x] Rejection of malformed, oversized (>64KB), resolved, and unrelated alert payloads
- [x] Deduplication of rapid notifications to prevent overlapping repairs
- [x] Read-only diagnosis by default (`write_authorized: false`)
- [x] Explicit operator authorization gate requiring confirmation from `LE0ST`
- [x] Disposable workspace containment rejecting directory traversal and symlink escapes
- [x] Environment variable sanitization stripping tokens and connection strings
- [x] Deterministic task-creation bug fixture injected exclusively into disposable workspace
- [x] End-to-end remediation drill demonstrating before/diagnosis/repair/verification/recovery
- [x] 28 unit and security tests in `incident-response/tests`
- [x] Zero regressions on existing backend unit tests, PostgreSQL integration, frontend build, Playwright E2E, and Issue #3 drill
