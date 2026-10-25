# TaskLane — Project 1 Roadmap & Issue Mapping

This document outlines the execution roadmap, issue linkages, and rubric scoring milestones for **Project 1 (AI Dev Tools Zoomcamp 2026)**.

- **Project Submission Deadline:** October 26, 2026, at 23:00 (Course platform timezone)
- **Target Score:** 22–24 / 24 points (Pass threshold: 10 points)
- **Public Issue Tracker:** [https://github.com/LE0ST/tasklane/issues](https://github.com/LE0ST/tasklane/issues)

---

## 1. Roadmap Milestones & Public GitHub Issues

```mermaid
gantt
    title Project 1 Implementation Timeline
    dateFormat YYYY-MM-DD
    section Phase 1: Team & Specs
    Issue #1: Agent Team & Modular Docs (PR #6 merged)  :done,  des1, 2026-10-23, 2026-10-23
    section Phase 2: Telemetry
    Issue #2: OpenTelemetry Instrumentation (PR #7 merged) :done, des2, 2026-10-23, 2026-10-25
    section Phase 3: Alerting
    Issue #3: User-Impact Alert & Drill (Draft PR #8)    :active, des3, 2026-10-25, 2026-10-26
    section Phase 4: SRE Automation
    Issue #4: Bounded On-Call Responder                :         des4, 2026-10-25, 2026-10-25
    section Phase 5: Submission Pack
    Issue #5: End-to-End Verification & Audit          :         des5, 2026-10-25, 2026-10-26
```

---

## 2. Issue Mapping to Official Rubric Criteria

### [Issue #1: Define agent team, public tracking, and modular project context](https://github.com/LE0ST/tasklane/issues/1)
- **Status:** **DONE — PR #6 squash-merged to `main` as `f6f4c7b` and deployed on Render October 23, 2026.**
- **Lead Agent:** Flash 3.8 High (Implementation) & GPT-6 Luna (Tech Lead)
- **Review Status:** External Sol QA findings were addressed, the maintainer approved PR #6, and the merged release was verified on Render. The GitHub PR records the external review summary.
- **Governance Gate:** Human Maintainer (`LE0ST`)
- **Rubric Criteria Targeted:**
  - **Product Specification (2 pts):** Specification exists, public GitHub Issues tracker established, tasks define testable acceptance criteria.
  - **Agent Team Specification (2 pts):** Roster defined (Luna, Flash, Sol, Astra, Argon, Maintainer), responsibilities separated, write restrictions documented.
  - **Context Management (2 pts):** Central `AGENTS.md` points to modular documentation (`docs/teams.md`, `docs/process.md`, `docs/architecture.md`, `docs/project-roadmap.md`).
- **Specification Links:** Supports `_docs/specs.md` §1–§7 by establishing traceable governance for all future technical changes.
- **Deliverables:** `AGENTS.md` index, `docs/teams.md`, `docs/process.md`, `docs/architecture.md`, `docs/project-roadmap.md`, updated `README.md` reflecting Neon database architecture.

---

### [Issue #2: Instrument TaskLane task workflows with correlated telemetry](https://github.com/LE0ST/tasklane/issues/2)
- **Status:** **DONE — PR #7 squash-merged to `main` as `cc9cce7`, deployed on Render on October 25, 2026.** Observability remains opt-in and local-only. [CI run 40219455194](https://github.com/LE0ST/tasklane/actions/runs/40219455194).
- **Lead Agent:** Flash 3.8 High (Implementation) & GPT-6 Astra (SRE Design)
- **Reviewers:** GPT-6.1 Sol (QA/Security)
- **Governance Gate:** Human Maintainer
- **Rubric Criteria Targeted:**
  - **Observability (2 pts):** Core user journeys (`POST /api/tasks`, `PATCH /api/tasks/{id}`) instrumented with metrics (`http_requests_total`), distributed traces (`trace_id`), and structured logs. Telemetry signals correlated and stored in Prometheus, Loki, Tempo, viewable in Grafana.
- **Specification Links:** `_docs/specs.md` §2.1 (Workflow Lanes), §2.2 (Lifecycle & Transitions), §5 (API Endpoints).
- **Deliverables:**
  - `backend/telemetry.py` (OpenTelemetry SDK instrumentation).
  - Metrics recording HTTP method, status code, route (bounded labels; zero PII).
  - Docker Compose telemetry stack configuration.

---

### [Issue #3: Add a real user-impact alert and reproducible incident drill](https://github.com/LE0ST/tasklane/issues/3)
- **Status:** IN PROGRESS — [Draft PR #8](https://github.com/LE0ST/tasklane/pull/8); independent SRE and QA/security review findings resolved in PR #8.
- **Implementation:** GPT-6 Luna through the maintainer's GitHub connection.
- **Independent SRE Review:** Astra.
- **Independent QA/Security Review:** Sol.
- **Human Merge Authority:** LE0ST.
- **Rubric Criteria Targeted:**
  - **Alerting on User Impact (1 pt):** Evaluates real user failure (e.g., 5xx rate on task creation or invalid state transition over 2m window) rather than generic CPU usage.
- **Specification Links:** `_docs/specs.md` §4 (Validation Rules), §5 (API Endpoints).
- **Deliverables:**
  - Dedicated bounded aggregate task-operation 5xx counter (`tasklane_task_5xx_errors_total`) initialized at zero during startup.
  - Versioned Grafana alert rule `TaskLane5xxRate`.
  - Local reproducible failure drill producing matching metric, structured log, trace, and trace ID.
  - Sanitized before/after evidence showing alert firing and resolution.

---

### [Issue #4: Implement bounded on-call responder and prove controlled remediation](https://github.com/LE0ST/tasklane/issues/4)
- **Status:** IMPLEMENTED (Draft PR targeting main; awaiting independent SRE/Security audit)
- **Branch:** `project1/oncall-responder`
- **Implementation:** Gemini Flash High
- **Independent SRE Review:** Astra Medium
- **Independent Security & Sandbox Audit:** Sol Medium
- **Governance Gate:** Human Maintainer (`LE0ST`)
- **Rubric Criteria Targeted:**
  - **On-Call Agent (2 pts):** Autonomous responder receives alert webhook, inspects logs/traces, diagnoses root cause, and applies a verified code fix in an isolated disposable worktree after explicit operator authorization.
- **Specification Links:** `_docs/specs.md` §6 (Acceptance Criteria AC-1..AC-10), `docs/oncall-drill.md`.
- **Deliverables:**
  - `incident-response/` service listening on port 8001 (`POST /alerts`).
  - Evidence gathering script querying Loki and Tempo for correlated context.
  - Strict read-only default sandbox mode with explicit operator-approved remediation demonstration.
  - Disposable workspace containment rejecting directory traversal and credential leaks.
  - **Safety Guarantee:** No `--dangerously-bypass-approvals-and-sandbox`; no operation on live Render/Neon production environments.

---

### [Issue #5: Verify CI/CD, Neon persistence, rollback and final peer-review evidence](https://github.com/LE0ST/tasklane/issues/5)
- **Status:** PLANNED
- **Lead Agent:** GPT-6 Astra (Deployment), GPT-6.1 Sol (QA), Flash 3.8 High (Documentation), GPT-6 Luna (Lead)
- **Governance Gate:** Human Maintainer
- **Rubric Criteria Targeted:**
  - **Application (2 pts):** Full-stack works end-to-end, single-command run.
  - **Testing (2 pts):** Unit, integration, and E2E tests run and pass.
  - **Containerization (2 pts):** Docker Compose starts app and database in one command.
  - **Deployment (2 pts):** Live public URL on Render with persistent Neon PostgreSQL 16.
  - **CI/CD (2–3 pts):** Automated tests on PR, continuous deployment to Render.
  - **Problem Description (2 pts):** Comprehensive, clear English documentation.
- **Specification Links:** All sections of `_docs/specs.md`.
- **Deliverables:**
  - Complete peer-review evaluation guide.
  - Submission SHA and repository verification.
  - Operational rollback documentation and data recovery plan.

---

## 3. Rubric Score Progression & Provisional Target Estimates

> [!IMPORTANT]
> The figures below represent **provisional target estimates** conditioned upon successful implementation, independent QA approval, passing CI, and maintainer merge for each milestone. They are internal planning targets and do not represent points officially awarded by course peer reviewers or grading staff.

| Milestone | Issues Addressed | Target Criteria Scope | Provisional Target Estimate |
|:---|:---|:---|:---:|
| **Baseline (Phase 0)** | Prior Homework 2 & 3 | Problem Description (2), Application (2), Testing (2), Containerization (2), Deployment (2), CI/CD (2), Product Spec (1), Context (1) | **14 / 24 pts** (Baseline) |
| **Milestone 1** | Issue #1 *(Merged, PR #6)* | Target: Product Specification (2/2), Agent Team (2/2), Context Management (2/2) | **18 / 24 pts** *(Provisional Target)* |
| **Milestone 2** | Issue #2 *(Draft PR #7; not yet merged)* | Target: Observability (2/2) | **20 / 24 pts** *(Provisional Target)* |
| **Milestone 3** | Issue #3 *(Planned)* | Target: Alerting on User Impact (1/1) | **21 / 24 pts** *(Provisional Target)* |
| **Milestone 4** | Issue #4 *(Planned)* | Target: On-Call Agent (2/2) | **23 / 24 pts** *(Provisional Target)* |
| **Milestone 5** | Issue #5 *(Planned)* | Target: Verification, Rollback, Peer Review Evidence Pack | **23–24 / 24 pts** *(Provisional Target)* |
