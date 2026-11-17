# Discovery: Tallowmere Health Plan (fictional)

Tallowmere is the made-up health plan from [prior-auth-workbench](https://github.com/builtbyshailendra/prior-auth-workbench). The workbench already turns requests from four channels into one queue with checked facts. This engagement adds a copilot for the nurses who review those requests. All policies, cases and people are synthetic.

## 1. What they asked for

> "Nurses spend too long finding the right paragraph in our medical policies, and writing the same request-for-information letters by hand."

## 2. What we think is going on

- About 30 medical policies, each 5–15 pages, revised yearly. Nurses know the common ones by heart and search PDFs for the rest.
- For each request, a nurse checks the plan's criteria against the case's facts, then approves, pends for information, or escalates to a medical director.
- Request-for-information (RFI) letters are mostly the same paragraphs with different missing items.
- Faxed clinical notes still arrive as images; the workbench's extraction covers typed notes only.

## 3. Stakeholders

| Who | What they care about | What they fear |
|---|---|---|
| Director of Utilization Management (sponsor) | Meeting CMS-0057-F's 72-hour / 7-day deadlines | Regulators finding an AI decision |
| Review nurses (users) | Finding the right criterion fast; fewer letters typed | Being overruled by a tool; extra clicks |
| Medical directors | Denials only by them, with a reason | Pressure from automated "recommendations" |
| Compliance / privacy officer | Minimum necessary PHI, audit, BAA with any vendor | PHI in logs or prompts sent to the wrong place |

## 4. Requirements

**Must**
- T1. The copilot never approves or denies. It answers questions about policy and drafts letters.
- T2. Every policy statement cites the exact section; every case fact comes from the workbench's checked facts.
- T3. "The policy doesn't say" is a valid answer and must be given when true.
- T4. RFI letters are drafts until a nurse approves them; sending is a separate, audited action.
- T5. PHI is redacted from logs and traces; prompts carry only what the task needs.
- T6. Read-only access to the workbench through its own token.

**Should**
- T7. Reads faxed pages (images) and extracts fields, with each value quoted from the page.
- T8. Answers in under 10 seconds.
- T9. Works through the nurse's existing tools via MCP where allowed.

**Won't**
- Medical necessity decisions, clinical advice to members, or anything the plan's medical directors own.

## 5. Success metrics

| Metric | Pilot target |
|---|---|
| Policy answers citing the correct section (labelled set) | ≥ 90% |
| Correct refusal when the policy is silent | ≥ 95% |
| Letters sent without approval | 0 |
| Fax fields extracted correctly, with a verified quote | ≥ 85% |
| Nurse minutes per policy check | measured before and after |

## 6. Risks

| Risk | Mitigation |
|---|---|
| Wrong citation trusted by a nurse | Citations verified in code against the source text; unverifiable claims are dropped and flagged |
| Outdated policy version | Every document carries an effective date; answers name the version used |
| PHI leakage | Redaction before logging; tenant isolation in the database; audit of every access |
| Scope creep into decisions | Hard rule in the tools: no tool can change a case's status |
