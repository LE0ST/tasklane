# TaskLane — Project 1 Roadmap & Verified Milestone Status

**Evidence snapshot:** October 25, 2026. Status reflects current merged main `9968999` plus separate unmerged Draft PR #9. This is project tracking, not an official grading result. For acceptance criteria see the linked public issues. For reviewable CI/commit chain see [evidence-traceability.md](evidence-traceability.md).

## Milestones and public issue tracker

| Milestone | Owner / reviewer roles | Current state | Implementation evidence |
|---|---|---|---|
| [#1 — Team, specs and process](https://github.com/LE0ST/tasklane/issues/1) | Flash + Luna; independent Sol review; LE0ST merge gate | **Closed** | [PR #6](https://github.com/LE0ST/tasklane/pull/6), squash `f6f4c7b` |
| [#2 — Local OTel and telemetry](https://github.com/LE0ST/tasklane/issues/2) | Flash implementation; Astra/Sol reviews; LE0ST gate | **Closed** | [PR #7](https://github.com/LE0ST/tasklane/pull/7), squash `cc9cce7` |
| [#3 — 5xx user-impact alert/drill](https://github.com/LE0ST/tasklane/issues/3) | Flash implementation; Astra/Sol reviews; LE0ST gate | **Closed** | [PR #8](https://github.com/LE0ST/tasklane/pull/8), squash `9968999`; [main CI 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032) |
| [#4 — Bounded on-call receiver](https://github.com/LE0ST/tasklane/issues/4) | Flash implementation; Astra/Sol security and SRE audits | **Open, Draft** | [PR #9](https://github.com/LE0ST/tasklane/pull/9); [CI 40265085450](https://github.com/LE0ST/tasklane/actions/runs/40265085450); independent merge-gate pending |
| [#5 — Deployment continuity and submission](https://github.com/LE0ST/tasklane/issues/5) | Luna coordination; Flash docs; Astra operations; Sol QA | **Open** | [runbook](operations-runbook.md), [matrix](evidence-traceability.md), [checklist](submission-checklist.md); docs PR requires review |

## Practical dependency order

1. Keep PR #9 Draft until targeted Astra/Sol security review finishes. The human maintainer decides whether to merge the **limited read-only diagnostic** feature.
2. A draft on-call PR must not be represented as production code or as proof of LLM-generated repair. The mock exercise and real Grafana alert-delivery checks are separate.
3. Review and merge Issue #5 documentation on a separate branch after changes are verified, then capture the final merged `main` SHA and check Render/Neon read-only.
4. Confirm the exact submission deadline/time zone on the course platform, submit the public repository/demo, and complete **three peer reviews**.

## Rubric evidence mapping

The official criteria include Problem Description, Product Specification, Agent Team Specification, Context Management, Application, Testing, Containerization, Deployment, CI/CD, Observability, Alerting on User Impact, and On-Call Agent (12 criteria, 24 available points).

- **Specification and team:** [`_docs/specs.md`](../_docs/specs.md), [AGENTS.md](../AGENTS.md), [teams.md](teams.md), [process.md](process.md), [Issues](https://github.com/LE0ST/tasklane/issues).
- **App, testing, containers, deployment and CD:** [README](../README.md), [CI workflow](../.github/workflows/ci.yml), [Compose](../docker-compose.yml), [runbook](operations-runbook.md), [Render demo](https://tasklane-app.onrender.com).
- **Observability and alert:** [observability](observability.md), [incident-drill](incident-drill.md), PRs #7/#8 with real CI evidence.
- **On-call:** [Draft PR #9](https://github.com/LE0ST/tasklane/pull/9) verifies local Grafana-to-responder diagnostics; the separate deterministic mock fix is **not** autonomous model-generated remediation. Full rubric credit is not claimed.

**No projection of 23–24/24 or other unawarded score is made.** External course reviewers grade the submission.
