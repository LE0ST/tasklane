# TaskLane — Project 1 Roadmap & Verified Milestone Status

**Historical milestone snapshot:** October 26, 2026. Merged main `7c73446` includes PR #9's limited on-call diagnosis-only feature. PR #10 was merged October 26, 2026 as `300f4e100200a183cf05bdfff95b777a62ddfd19`. This is project tracking, not an official grading result. For acceptance criteria see the linked public issues. For reviewable CI/commit chain see [evidence-traceability.md](evidence-traceability.md).

## Milestones and public issue tracker

| Milestone | Owner / reviewer roles | Current state | Implementation evidence |
|---|---|---|---|
| [#1 — Team, specs and process](https://github.com/LE0ST/tasklane/issues/1) | Flash + Luna; independent Sol review; LE0ST merge gate | **Closed** | [PR #6](https://github.com/LE0ST/tasklane/pull/6), squash `f6f4c7b` |
| [#2 — Local OTel and telemetry](https://github.com/LE0ST/tasklane/issues/2) | Flash implementation; Astra/Sol reviews; LE0ST gate | **Closed** | [PR #7](https://github.com/LE0ST/tasklane/pull/7), squash `cc9cce7` |
| [#3 — 5xx user-impact alert/drill](https://github.com/LE0ST/tasklane/issues/3) | Flash implementation; Astra/Sol reviews; LE0ST gate | **Closed** | [PR #8](https://github.com/LE0ST/tasklane/pull/8), squash `9968999`; [main CI 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032) |
| [#4 — Bounded on-call receiver](https://github.com/LE0ST/tasklane/issues/4) | Flash earlier implementation; Terra PR #15; Astra/Sol reviews | **Closed — partial scope; remaining autonomous repair not planned** | [PR #9](https://github.com/LE0ST/tasklane/pull/9) read-only diagnosis; [PR #15](https://github.com/LE0ST/tasklane/pull/15) supervised drill; [scope disposition](https://github.com/LE0ST/tasklane/issues/4#issuecomment-6460818260). No alert-triggered autonomous fix demonstrated. |
| [#5 — Deployment continuity and submission](https://github.com/LE0ST/tasklane/issues/5) | Luna coordination; Flash docs; Astra operations; Sol QA | **Closed — completed** | [Final closure](https://github.com/LE0ST/tasklane/issues/5#issuecomment-6460818783), [PR #16](https://github.com/LE0ST/tasklane/pull/16), [PR #17](https://github.com/LE0ST/tasklane/pull/17); [push CI 40632293401](https://github.com/LE0ST/tasklane/actions/runs/40632293401) 4/4 and Render `live` for app SHA `cffdf3c5`. |

## Practical dependency order

1. PR #9's independent security/SRE review and human-authorized squash merge are completed at `7c73446`. PR #15 subsequently merged the supervised local drill; Issue #4 was subsequently closed with partial functionality delivered; the remaining autonomous remediation was not planned, with limitations preserved in the issue.
2. The opt-in on-call overlay is now versioned in merged `main`; the mock exercise and real Grafana alert-delivery checks remain separate. The [maintainer-reported live-provider drill](oncall-drill.md#maintainer-reported-supervised-live-run) is separate, supervised, and confined to a disposable workspace; no alert-triggered autonomous fix or production agent deployment is claimed.
3. PRs #10 and #11 are merged. If selecting a later submission revision, confirm its exact merged SHA and its own CI/deployment evidence without production mutations.
4. The maintainer confirms that the submission is saved and the platform allows updates; neither was independently verified here. The saved SHA is unknown. Verify the saved SHA and current platform instructions. The maintainer confirms three peer reviews were completed and scored; the platform records have not been independently verified.

## Retained limitations and verification follow-ups

- **Issue #4 (closed, partial):** A real alert-triggered read-only responder and a separate maintainer-reported, human-reviewed DeepSeek drill were demonstrated at different times; an integrated autonomous patch and independently isolated verification remain unproven. The course's 2-point autonomous on-call criterion is not claimed. [Closure details](https://github.com/LE0ST/tasklane/issues/4#issuecomment-6460818260).
- **Issue #5 (closed, completed):** Final app release `cffdf3c5a9813846c3a85a5d8c1b0548b0c45138` passed 4/4 CI and matched a Render `live` deployment. The exact SHA saved in the course form remains unverified and should be updated by the maintainer if different; three peer reviews completed/scored per maintainer without direct platform inspection. Historical Neon persistence was verified, but the former Render DB and independent backup are not currently verified. [Closure details](https://github.com/LE0ST/tasklane/issues/5#issuecomment-6460818783).

## Rubric evidence mapping

The official criteria include Problem Description, Product Specification, Agent Team Specification, Context Management, Application, Testing, Containerization, Deployment, CI/CD, Observability, Alerting on User Impact, and On-Call Agent (12 criteria, 24 available points).

- **Specification and team:** [`_docs/specs.md`](../_docs/specs.md), [AGENTS.md](../AGENTS.md), [teams.md](teams.md), [process.md](process.md), [Issues](https://github.com/LE0ST/tasklane/issues).
- **App, testing, containers, deployment and CD:** [README](../README.md), [CI workflow](../.github/workflows/ci.yml), [Compose](../docker-compose.yml), [runbook](operations-runbook.md), [Render demo](https://tasklane-app.onrender.com).
- **Observability and alert:** [observability](observability.md), [incident-drill](incident-drill.md), PRs #7/#8 with real CI evidence.
- **On-call:** [Merged PR #9](https://github.com/LE0ST/tasklane/pull/9) verifies local Grafana-to-responder diagnostics; the separate maintainer-reported live diff required human approval and is not an alert-triggered autonomous repair. Full rubric credit is not claimed.

**No projection of 23–24/24 or other unawarded score is made.** External course reviewers grade the submission.
