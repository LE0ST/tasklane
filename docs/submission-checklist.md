# TaskLane — Final Submission and Peer-Review Checklist

**Historical baseline:** October 26, 2026; later evidence is noted below. **Purpose:** prevent inflated claims, stale SHAs, broken instructions and missing course peer reviews. The authoritative deadline and submission workflow live on the [DataTalksClub course platform](https://courses.datatalks.club/ai-dev-tools-2026/); do not assume a platform deadline time zone.

## Public submission metadata

- **Verified application release prior to this documentation-only follow-up:** `cffdf3c5a9813846c3a85a5d8c1b0548b0c45138`, after [PR #16](https://github.com/LE0ST/tasklane/pull/16) and [PR #17](https://github.com/LE0ST/tasklane/pull/17). [Post-merge CI 40632293401](https://github.com/LE0ST/tasklane/actions/runs/40632293401) passed 4/4; Render deploy `dep-dbgnlhnb9u1s7394br00` was `live` at the same SHA. A later documentation-only merge will have a **new SHA** requiring its own checks and submission update.
- **Issue closure:** [#4 closed partially](https://github.com/LE0ST/tasklane/issues/4#issuecomment-6460818260) and [#5 closed completed](https://github.com/LE0ST/tasklane/issues/5#issuecomment-6460818783); the remaining autonomous on-call scope was not planned.

- Repository: [https://github.com/LE0ST/tasklane](https://github.com/LE0ST/tasklane)
- Public demo: [https://tasklane-app.onrender.com](https://tasklane-app.onrender.com)
- API documentation: [https://tasklane-app.onrender.com/docs](https://tasklane-app.onrender.com/docs)
- **Earlier verified baseline:** `946b582510219532d0d1e44ba8510f5ca50997e9` (squash PR #11), after documentation PR #10 (`300f4e1`); [CI 40287182535](https://github.com/LE0ST/tasklane/actions/runs/40287182535) passed 4/4, and its matching Render deployment was live.
- **Historical verified submission candidate:** `96d3ebce69ed76345635fa5a90c82773229e0d9e` (merged `main`, October 26, 2026). [CI 40350303951](https://github.com/LE0ST/tasklane/actions/runs/40350303951) passed 4/4 and Render deploy `dep-dbfn7b4s728c73dq7f90` was verified `live`. The docs-only SHA did not receive a separate public `/health` probe. The SHA currently saved on the course platform has not been independently checked. If selecting a later revision, verify its exact merged SHA and its own CI/deploy checks.
- **Merged PR #15 and CI:** [PR #15](https://github.com/LE0ST/tasklane/pull/15) was squash-merged as `65378015cad42e107148ec0f6618472b54a1e36b`; [push-to-main CI 40618254717](https://github.com/LE0ST/tasklane/actions/runs/40618254717) passed all four jobs. This is not proof of the SHA saved on the course platform or deployed on Render.
- **Maintainer confirmation:** The submission has been saved and the platform allows it to be updated. These are maintainer confirmations, not independent platform verification; the currently saved SHA is unknown.
- Merged on-call diagnosis-only responder: [PR #9](https://github.com/LE0ST/tasklane/pull/9), premerge HEAD `244e58c`, squash `7c73446` in `main`; the merged baseline demonstrated no real AI-generated repair.
- Any post-closure documentation correction requires independent review and explicit maintainer approval for its PR merge; closing Issue #5 is not permission to push directly to `main`.

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
- [x] Record historical candidate `dd9a52696a961f58d2c5064a926b2c8082a333cf` ([CI 40289231699](https://github.com/LE0ST/tasklane/actions/runs/40289231699)); the later historical candidate `96d3ebce69ed76345635fa5a90c82773229e0d9e` and its CI/Render evidence are listed above. Neither establishes the SHA currently saved in the submission.
- [x] Verify Project 1 deadline against the **authenticated maintainer account**: **October 26, 2026 at 18:00 in the account's configured timezone**. The logged-out public preview instead displayed 23:00 in a different viewer timezone. This is a historical observation; check current platform instructions before any update. [Course page](https://courses.datatalks.club/ai-dev-tools-2026/project/project1).
- [x] Submission saved, as confirmed by the maintainer; independent platform verification and the saved SHA remain unverified.
- [ ] Verify the exact currently saved SHA and retain submission evidence; update only if appropriate and permitted.
- [x] **Complete and score three required peer reviews**, as confirmed by the maintainer. This is a **pass/fail requirement** according to the official course rubric, not optional extra credit; the platform records have not been independently verified.
- [ ] Independently verify or retain evidence of the three peer-review records when available.

## Project 1 submission form — historical observation (October 26, 2026)

The authenticated page displayed October 26, 2026 at 18:00 in the maintainer account's configured timezone as the deadline; this is a historical observation. The maintainer now confirms the submission is saved and the platform allows updates; neither claim was independently verified here. Check the current [course platform](https://courses.datatalks.club/ai-dev-tools-2026/project/project1) instructions and the saved SHA before any update. The historical form asked for:

| Form field | What to enter |
|---|---|
| GitHub link to the project | `https://github.com/LE0ST/tasklane` |
| Commit ID | **Exact verified merged commit SHA**; `96d3ebce69ed76345635fa5a90c82773229e0d9e` is a historical candidate, not a verified current form value. Verify any later revision separately; never assume a feature-branch HEAD was submitted. |
| Learning in public links (optional) | Up to 14 relevant public progress posts; only if already published |
| Time spent on project (hours) | The maintainer's actual hours; **do not invent** a number |
| FAQ contribution PR or issue URL (optional) | Only an actual qualifying contribution |
| Certificate name | The name the maintainer wants printed on the certificate |

**Submission and peer review are separate obligations.** Saving a project does not satisfy the requirement to review three other participants' projects. The maintainer confirms that three projects were reviewed and scored using the course platform; independent verification of those records and retained evidence remain outstanding. The [official rubric](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md) describes the review requirement. The public unauthenticated page does not expose the participant-specific review workflow.

## Do not overstate

- **Public shared demo:** No login, task owner IDs or per-user authorization. All visitors share one Neon-backed task collection and may view, edit, create and delete each other's tasks. This is not private storage; avoid sensitive data and avoid production write tests.

- **On-call rubric:** Real Grafana alerting and correlated read-only triage are verified in the merged local-only implementation. MockAgentAdapter removes a deterministic fixture in a separate disposable TestClient/SQLite exercise. A later [maintainer-reported live-provider run](oncall-drill.md#maintainer-reported-supervised-live-run) produced a human-reviewed diff applied in a disposable workspace; the implementation and report were later merged in PR #15, but the report is not an independently verified run or an alert-triggered autonomous fix. The course reserves 2 points for an agent that wakes and fixes the issue; those points cannot be represented as earned or guaranteed.
- **CI/CD:** CI covers four jobs and Render auto-deploys on passing checks. No staging-to-manual-production pipeline is claimed.
- **Production database:** Current Neon SELECT evidence is read-only; historical Render DB condition was not reverified. No fresh production data mutation or independently verified backup is claimed.
- **Security audit:** Astra and Sol provide external read-only reports/PR comments. Do not misrepresent their analyses as native GitHub reviews or approval to merge.
- **Score:** 24 possible rubric points; peers award actual grades. This checklist makes no score prediction or guarantee.

## Linked resources

- [Evidence and rubric traceability](evidence-traceability.md)
- [Deployment and recovery runbook](operations-runbook.md)
- [Project roadmap](project-roadmap.md)
- [Official course project rubric](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md)
