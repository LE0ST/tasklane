# TaskLane — Final Submission and Peer-Review Checklist

**As-of:** October 25, 2026. **Purpose:** prevent inflated claims, stale SHAs, broken instructions and missing course peer reviews. The authoritative deadline and submission workflow live on the [DataTalksClub course platform](https://courses.datatalks.club/ai-dev-tools-2026/); do not assume a platform deadline time zone.

## Public submission metadata

- Repository: [https://github.com/LE0ST/tasklane](https://github.com/LE0ST/tasklane)
- Public demo: [https://tasklane-app.onrender.com](https://tasklane-app.onrender.com)
- API documentation: [https://tasklane-app.onrender.com/docs](https://tasklane-app.onrender.com/docs)
- **Merged `main` baseline (verified):** `996899924f04d3abe02ca5fd1d1578ad06e554c8`.
- **Final submission commit SHA: PENDING.** Record the exact final `main` commit SHA only after all authorized PR merges and final CI/deploy checks.
- Draft on-call responder: [PR #9](https://github.com/LE0ST/tasklane/pull/9), HEAD `ef534630d87b806187df9ec58e8209ba92d30325` as of this snapshot, **not part of main yet**.
- Documentation/evidence changes belong to Issue #5 and require their own review and explicit maintainer merge approval.

## Final gate checklist — update only when evidence exists

- [x] English problem description, app behavior, spec and OpenAPI documented.
- [x] Public Issues #1–#5 have acceptance criteria, and merged Issue #1–#3 history is linked.
- [x] Agent team, role boundaries, and modular AGENTS.md guidance documented.
- [x] One-command Docker Compose stack with local PostgreSQL is implemented and tested in CI.
- [x] Backend tests, PostgreSQL integration, Alembic migrations, Playwright and local observability CI passed at merged `main` SHA `9968999` ([run 40238643032](https://github.com/LE0ST/tasklane/actions/runs/40238643032)).
- [x] Local Grafana user-impact task 503 firing and resolved notifications demonstrated; no production fault injection.
- [x] Render service/deploy status and Neon existing row count verified read-only on October 25.
- [x] Production-safe app rollback and database recovery **procedures** documented; no actual production rollback or backup restore is claimed.
- [ ] Receive independent Sol/Astra approval for the final reviewed PR #9 HEAD.
- [ ] If approved, obtain explicit maintainer authorization and merge PR #9; revalidate CI and Render/Neon read-only.
- [ ] Merge Issue #5 documentation PR only after review and explicit maintainer approval; capture its final merged SHA and passing CI.
- [ ] Verify public demo and application behavior without adding or changing production tasks.
- [ ] Record final `main` commit SHA and linked Actions deployment evidence in submission.
- [ ] Confirm exact deadline, complete course-platform submission, and save proof of submission.
- [ ] **Complete three required peer reviews** of other participants on the course platform and retain evidence of each. This is a **pass/fail requirement** according to the official course rubric, not optional extra credit.

## Do not overstate

- **On-call rubric:** Real Grafana alerting and correlated read-only triage are verified in the PR. MockAgentAdapter removes a deterministic fixture in a separate disposable TestClient/SQLite exercise. No live coding model generated the tested repair. The course reserves 2 points for an agent that wakes and fixes the issue; those points cannot be represented as earned or guaranteed.
- **CI/CD:** CI covers four jobs and Render auto-deploys on passing checks. No staging-to-manual-production pipeline is claimed.
- **Production database:** Current Neon SELECT evidence is read-only; historical Render DB condition was not reverified. No fresh production data mutation or independently verified backup is claimed.
- **Security audit:** Astra and Sol provide external read-only reports/PR comments. Do not misrepresent their analyses as native GitHub reviews or approval to merge.
- **Score:** 24 possible rubric points; peers award actual grades. This checklist makes no score prediction or guarantee.

## Linked resources

- [Evidence and rubric traceability](evidence-traceability.md)
- [Deployment and recovery runbook](operations-runbook.md)
- [Project roadmap](project-roadmap.md)
- [Official course project rubric](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md)
