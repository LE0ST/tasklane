# TaskLane — Project 1 Roadmap & Verified Milestone Status

**Evidence snapshot:** October 26, 2026. Merged main `7c73446` includes PR #9's limited on-call diagnosis-only feature. PR #10 was merged October 26, 2026 as `300f4e100200a183cf05bdfff95b777a62ddfd19`. This is project tracking, not an official grading result. For acceptance criteria see the linked public issues. For reviewable CI/commit chain see [evidence-traceability.md](evidence-traceability.md).

## Milestones and public issue tracker

| Milestone | Owner / reviewer roles | Current state | Implementation evidence |
|---|---|---|---|
| [#1 — Team, specs and process](https://github.com/LE0ST/tasklane/issues/1) | Flash + Luna; independent Sol review; LE0ST merge gate | **Closed** | [PR #6](https://github.com/LE0ST/tasklane/pull/6), squash `f6f4c7b` |
| [#2 — Local OTel and telemetry](https://github.com/LE0ST/tasklane/issues/2) | Flash implementation; Astra/Sol reviews; LE0ST gate | **Closed** | [PR #7](https://github.com/LE0ST/tasklane/pull/7), squash `cc9cce7` |
| [#3 — 5xx user-impact alert/drill](https://github.com/LE0ST/tasklane/issues/3) | Flash implementation; Astra/Sol reviews; LE0ST gate | **Closed** | [PR #8](https://github.com/LE0ST/tasklane/pull/8), squash `9968999`; [main CI 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032) |
| [#4 — Bounded on-call receiver](https://github.com/LE0ST/tasklane/issues/4) | Flash implementation; Astra/Sol security and SRE audits | **Open — partial scope merged** | [PR #9](https://github.com/LE0ST/tasklane/pull/9), squash `7c73446`, [PR CI 40272231320](https://github.com/LE0ST/tasklane/actions/runs/40272231320) 4/4 green; full autonomous AI remediation unproven |
| [#5 — Deployment continuity and submission](https://github.com/LE0ST/tasklane/issues/5) | Luna coordination; Flash docs; Astra operations; Sol QA | **Open** | [PR #10](https://github.com/LE0ST/tasklane/pull/10) and [PR #11](https://github.com/LE0ST/tasklane/pull/11) merged; [runbook](operations-runbook.md), [matrix](evidence-traceability.md), [checklist](submission-checklist.md) |

## Practical dependency order

1. PR #9's independent security/SRE review and human-authorized squash merge are completed at `7c73446`. Only the limited read-only diagnostic feature is accepted; Issue #4 remains open for any unproven autonomous remediation.
2. The opt-in on-call overlay is now versioned in merged `main`; the mock exercise and real Grafana alert-delivery checks remain separate. A later [maintainer-reported live-provider drill](oncall-drill.md#maintainer-reported-supervised-live-run) is separate, supervised, and confined to a disposable workspace; no alert-triggered autonomous fix or production agent deployment is claimed.
3. PRs #10 and #11 are merged. If selecting a later submission revision, confirm its exact merged SHA and its own CI/deployment evidence without production mutations.
4. The maintainer confirms that the submission is saved and the platform allows updates; neither was independently verified here. The saved SHA is unknown. Verify the saved SHA and current platform instructions, and complete **three peer reviews**.

## Remaining acceptance evidence

- **Issue #4:** Preserve scrubbed evidence tying a real alert webhook, incident metadata and trace/logs to the agent response and actual repair in the same local run. The existing real-alert read-only diagnosis and the maintainer-reported human-approved live drill are separate. The autonomous disposable-workspace remediation criterion, a trustworthy passing test and recovered user journey, and the complete wake/diagnose/fix trail remain unproven. Any future write-capable path needs explicitly enabled bounded privileges and a separately reviewed isolation and verification boundary; keep read-only defaults and production isolation.
- **Issue #5:** Confirm the exact SHA saved in the maintainer-reported submission and the three peer-review records; the maintainer confirms the submission is saved and can be updated, without independent platform verification. If a later merged candidate is selected, verify its own CI and deployment evidence; do not present the local feature SHA as deployed. Historical Neon persistence and comparison with the original Render database exist, but the original database's present state and an independent source backup are unverified. Keep the recovery plan and verify an available backup when authorized; do not attempt to access the former database, and do not alter production to fill the gap. Confirm current course-platform instructions and deadlines rather than assuming the historical dates still apply.

## Rubric evidence mapping

The official criteria include Problem Description, Product Specification, Agent Team Specification, Context Management, Application, Testing, Containerization, Deployment, CI/CD, Observability, Alerting on User Impact, and On-Call Agent (12 criteria, 24 available points).

- **Specification and team:** [`_docs/specs.md`](../_docs/specs.md), [AGENTS.md](../AGENTS.md), [teams.md](teams.md), [process.md](process.md), [Issues](https://github.com/LE0ST/tasklane/issues).
- **App, testing, containers, deployment and CD:** [README](../README.md), [CI workflow](../.github/workflows/ci.yml), [Compose](../docker-compose.yml), [runbook](operations-runbook.md), [Render demo](https://tasklane-app.onrender.com).
- **Observability and alert:** [observability](observability.md), [incident-drill](incident-drill.md), PRs #7/#8 with real CI evidence.
- **On-call:** [Merged PR #9](https://github.com/LE0ST/tasklane/pull/9) verifies local Grafana-to-responder diagnostics; the separate maintainer-reported live diff required human approval and is not an alert-triggered autonomous repair. Full rubric credit is not claimed.

**No projection of 23–24/24 or other unawarded score is made.** External course reviewers grade the submission.
