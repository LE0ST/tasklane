# TaskLane — Final Submission and Peer-Review Checklist

**As-of:** October 26, 2026. **Purpose:** prevent inflated claims, stale SHAs, broken instructions and missing course peer reviews. The authoritative deadline and submission workflow live on the [DataTalksClub course platform](https://courses.datatalks.club/ai-dev-tools-2026/); do not assume a platform deadline time zone.

## Public submission metadata

- Repository: [https://github.com/LE0ST/tasklane](https://github.com/LE0ST/tasklane)
- Public demo: [https://tasklane-app.onrender.com](https://tasklane-app.onrender.com)
- API documentation: [https://tasklane-app.onrender.com/docs](https://tasklane-app.onrender.com/docs)
- **Earlier verified baseline:** `946b582510219532d0d1e44ba8510f5ca50997e9` (squash PR #11), after documentation PR #10 (`300f4e1`); [CI 40287182535](https://github.com/LE0ST/tasklane/actions/runs/40287182535) passed 4/4, and its matching Render deployment was live.
- **Current verified submission candidate:** `96d3ebce69ed76345635fa5a90c82773229e0d9e` (merged `main`, October 26, 2026). [CI 40350303951](https://github.com/LE0ST/tasklane/actions/runs/40350303951) passed 4/4 and Render deploy `dep-dbfn7b4s728c73dq7f90` was verified `live`. The docs-only SHA did not receive a separate public `/health` probe. Saving the candidate on the course platform remains unverified. If selecting a later revision, use its exact merged SHA after its own CI/deploy checks.
- Merged on-call diagnosis-only responder: [PR #9](https://github.com/LE0ST/tasklane/pull/9), premerge HEAD `244e58c`, squash `7c73446` in `main`; **no real AI-generated repair**.
- Documentation/evidence changes belong to Issue #5 and require their own review and explicit maintainer merge approval.

## Final gate checklist — update only when evidence exists

- [x] English problem description, app behavior, spec and OpenAPI documented.
- [x] Public Issues #1–#5 have acceptance criteria, and merged Issue #1–#3 history is linked.
- [x] Agent team, role boundaries, and modular AGENTS.md guidance documented.
- [x] One-command Docker Compose stack with local PostgreSQL is implemented and tested in CI.
- [x] Backend, PostgreSQL, Alembic, Playwright, and local telemetry/on-call CI passed at merged `main` SHA `7c73446` ([run 40273628271](https://github.com/LE0ST/tasklane/actions/runs/40273628271), 4/4 jobs; 76 backend + 54 responder tests).
- [x] Local Grafana user-impact task 503 firing and resolved notifications demonstrated; no production fault injection.
- [x] Render service/deploy status and Neon existing row count verified read-only through October 26.
- [x] Production-safe app rollback and database recovery **procedures** documented; no actual production rollback or backup restore is claimed.
- [x] Receive external independent security and SRE approval for PR #9's limited diagnosis-only scope (Sol reviewed earlier security HEAD; Astra approved final identity-only HEAD).
- [x] Obtain explicit maintainer approval and squash-merge PR #9 as `7c73446`.
- [x] Verify merged-main [CI 40273628271](https://github.com/LE0ST/tasklane/actions/runs/40273628271) passed 4/4 and Render deploy `dep-dbfe8kuk1f9s7386g05g` is `live` at `7c73446`; Neon read-only totals unchanged (2 tasks, 1 migration check; Alembic `1ea1353bfe87`).
- [x] Merge Issue #5 documentation PRs #10 and #11 with maintainer authorization; baseline `946b582` passed [CI 40287182535](https://github.com/LE0ST/tasklane/actions/runs/40287182535), 4/4.
- [x] Verify public `/health` returned healthy at baseline `946b582`, without production task writes; see [verification recorded in Issue #5](https://github.com/LE0ST/tasklane/issues/5#issuecomment-6410875915). This is a health check, not a new full UI workflow test.
- [x] Record verified candidate SHA `dd9a52696a961f58d2c5064a926b2c8082a333cf` and linked CI/Render evidence above; actual course submission is still pending.
- [x] Verify Project 1 deadline against the **authenticated maintainer account**: **October 26, 2026 at 18:00 in the account's configured timezone**. The logged-out public preview instead displayed 23:00 in a different viewer timezone. Follow the authenticated time, not the public preview. [Course page](https://courses.datatalks.club/ai-dev-tools-2026/project/project1).
- [ ] Complete course-platform submission while logged in, and save proof of submission.
- [ ] **Complete three required peer reviews** of other participants on the course platform and retain evidence of each. This is a **pass/fail requirement** according to the official course rubric, not optional extra credit.

## Actual Project 1 submission form (confirmed October 26, 2026)

Open [Project 1 — course platform](https://courses.datatalks.club/ai-dev-tools-2026/project/project1), log in, and **save the submission before October 26, 2026 at 18:00 in the maintainer account's displayed timezone**. The authenticated page explicitly permits saving a project and updating its commit SHA before the deadline. Submitting now with a verified green `main` commit avoids losing the whole entry if optional cleanup PRs take longer. The form asks for:

| Form field | What to enter |
|---|---|
| GitHub link to the project | `https://github.com/LE0ST/tasklane` |
| Commit ID | **Exact verified merged commit SHA**; candidate `96d3ebce69ed76345635fa5a90c82773229e0d9e`, or a later merged revision after its own verification; never a premerge feature-branch HEAD |
| Learning in public links (optional) | Up to 14 relevant public progress posts; only if already published |
| Time spent on project (hours) | The maintainer's actual hours; **do not invent** a number |
| FAQ contribution PR or issue URL (optional) | Only an actual qualifying contribution |
| Certificate name | The name the maintainer wants printed on the certificate |

**Submission and peer review are separate obligations.** Saving a project does not satisfy the requirement to review three other participants' projects. After submitting, use the platform's review assignments/instructions, score **three** projects using the [official rubric](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md), and retain completion evidence. The public unauthenticated page does not expose the participant-specific review workflow.

## Do not overstate

- **On-call rubric:** Real Grafana alerting and correlated read-only triage are verified in the merged local-only implementation. MockAgentAdapter removes a deterministic fixture in a separate disposable TestClient/SQLite exercise. No live coding model generated the tested repair. The course reserves 2 points for an agent that wakes and fixes the issue; those points cannot be represented as earned or guaranteed.
- **CI/CD:** CI covers four jobs and Render auto-deploys on passing checks. No staging-to-manual-production pipeline is claimed.
- **Production database:** Current Neon SELECT evidence is read-only; historical Render DB condition was not reverified. No fresh production data mutation or independently verified backup is claimed.
- **Security audit:** Astra and Sol provide external read-only reports/PR comments. Do not misrepresent their analyses as native GitHub reviews or approval to merge.
- **Score:** 24 possible rubric points; peers award actual grades. This checklist makes no score prediction or guarantee.

## Linked resources

- [Evidence and rubric traceability](evidence-traceability.md)
- [Deployment and recovery runbook](operations-runbook.md)
- [Project roadmap](project-roadmap.md)
- [Official course project rubric](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md)
