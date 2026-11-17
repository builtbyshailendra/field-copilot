# Workshop plan: Corvane Freight, session 1 (90 minutes)

**Goal:** agree on the three incident patterns the pilot covers, the evidence sources, and how we'll measure success.

**Who:** Head of Platform, two on-call engineers, the security lead. Us: one forward deployed engineer, one product engineer.

| Time | Activity | Output |
|---|---|---|
| 0–10 | Their goals, in their words. What would make on-call better next quarter? | Sponsor's success statement |
| 10–35 | Walk through the last three real incidents, minute by minute. Where did time go? | A timeline per incident; the slow steps circled |
| 35–50 | Evidence sources: which dashboards, logs, events and docs did they actually use? Access model for each | Data source list with owner and access method |
| 50–65 | Security: where can data go, which model endpoints are allowed, what must never be in a prompt | Constraints list, signed off by the security lead |
| 65–80 | Success metrics: agree the table in the discovery README, or change it | Metrics everyone accepts |
| 80–90 | Next steps: shadow-mode pilot on staging; who approves actions | Pilot plan owner and date |

**Questions to ask that people forget:**
- Who is allowed to approve a rollback at 3 am, and how do we know who they are?
- Which incidents would you never want an assistant involved in?
- What does "done" look like for an investigation: a cause, a fix, or a ticket?
- Where do postmortems live, and can we read them?

**After the session:** send a one-page summary within 24 hours with the decisions and open questions. Nothing gets built until the security constraints are confirmed in writing.
