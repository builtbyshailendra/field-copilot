from datetime import date

import httpx
import pytest

from fieldcopilot.agent import ApprovalError, ApprovalService, User
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider
from fieldcopilot.tenants.tallowmere.fax import fake_fax_reader, read_fax
from fieldcopilot.tenants.tallowmere.letters import add_business_days, response_deadline
from fieldcopilot.tenants.tallowmere.tools import build_reviewer, build_tools, fake_reviewer, missing_documents
from fieldcopilot.tenants.tallowmere.workbench import FixtureWorkbench, WorkbenchClient, minimum_necessary

NURSE = User("j.patel", "tallowmere-health", frozenset({"review-nurse"}))
TODAY = lambda: date(2026, 11, 20)  # a Friday


def llm():
    fake = FakeProvider().on("review_case", fake_reviewer).on("read_fax", fake_fax_reader)
    return LLM(fake, models=dict(FAKE_MODELS))


def test_minimum_necessary_drops_member_name_and_npi():
    case = FixtureWorkbench.from_pack().case(1042)
    view = minimum_necessary(case)
    text = str(view)
    assert "Synthetic Member A" not in text and "1234567893" not in text
    assert view["member_id"] == "TM-55012" and view["facts"][0]["quote"].startswith("Low back pain")


@pytest.mark.parametrize("case_id, expected", [(1042, []), (2207, ["knee_xray_report"]),
                                               (3310, ["bmi_record", "hsat_report_or_reason"]), (4120, ["psych_evaluation"]),
                                               (5005, [])])
def test_missing_documents_follow_the_policy_requirements(case_id, expected):
    result = missing_documents(FixtureWorkbench.from_pack(), case_id)
    assert [m["type"] for m in result["missing"]] == expected


def test_deadlines_follow_um_ops_01():
    assert response_deadline(date(2026, 11, 20), "standard") == date(2026, 12, 4)
    assert response_deadline(date(2026, 11, 20), "expedited") == date(2026, 11, 24)  # skips the weekend
    assert add_business_days(date(2026, 11, 23), 1) == date(2026, 11, 24)


def test_rfi_flow_waits_for_the_nurse_and_lists_every_item_once():
    workbench = FixtureWorkbench.from_pack()
    approvals = ApprovalService(None, {"tallowmere-health": "review-nurse"}, {"tallowmere-health": 60})
    run = build_reviewer(llm(), workbench, approvals, today=TODAY).run("Review case 3310 against its policy.")
    assert run.status == "awaiting_approval" and workbench.sent_letters == []
    assert run.final.missing_documents == ["bmi_record", "hsat_report_or_reason"]
    assert run.final.letter_approval == "APR-0001"
    pending = approvals.pending("tallowmere-health")
    letter = pending[0].summary
    for part in ("Prior authorization request 3310", "1. BMI recorded within the last 6 months",
                 "2. Home sleep apnea test report", "04 December 2026", "Nurse J. Patel", "X12 275"):
        assert part in letter
    for word in ("approve", "deny", "denied", "approved"):
        assert word not in letter.lower()
    approvals.approve(pending[0].id, NURSE, pending[0].hash)
    assert len(workbench.sent_letters) == 1 and workbench.sent_letters[0]["approved_by"] == "j.patel"


def test_an_engineer_cannot_send_a_letter():
    workbench = FixtureWorkbench.from_pack()
    approvals = ApprovalService(None, {"tallowmere-health": "review-nurse"})
    build_reviewer(llm(), workbench, approvals, today=TODAY).run("Review case 2207.")
    pending = approvals.pending("tallowmere-health")[0]
    with pytest.raises(ApprovalError):
        approvals.approve(pending.id, User("ravi", "corvane-freight", frozenset({"oncall-lead"})), pending.hash)
    assert workbench.sent_letters == []


def test_rfi_cannot_ask_for_documents_the_policy_doesnt_require():
    tools = build_tools(FixtureWorkbench.from_pack(), llm(), today=TODAY)
    propose = tools.get("propose_rfi_letter")
    summary = propose.summarize(propose.validate({"case_id": 2207, "missing": ["dental_records"], "nurse": "N"}))
    assert summary.startswith("INVALID RFI") and "dental_records" in summary


def test_case_without_missing_documents_proposes_nothing():
    approvals = ApprovalService(None, {"tallowmere-health": "review-nurse"})
    run = build_reviewer(llm(), FixtureWorkbench.from_pack(), approvals, today=TODAY).run("Review case 5005.")
    assert run.status == "completed" and approvals.pending("tallowmere-health") == []
    assert all(c.status == "met" for c in run.final.criteria)


def test_fax_fields_always_need_confirmation():
    reading = read_fax(llm(), "fax-1042-p1")
    values = {f["name"]: f["value"] for f in reading["fields"]}
    assert values["member_id"] == "TM-55012" and values["service_code"] == "72148" and values["therapy_weeks"] == "3"
    assert all(f["status"] == "needs_confirmation" for f in reading["fields"])


def test_workbench_client_sends_its_token_and_maps_404():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["Authorization"]
        if request.url.path.endswith("/404"):
            return httpx.Response(404, json={"detail": "No such case"})
        return httpx.Response(200, json={"id": 7})

    client = WorkbenchClient("http://workbench", "copilot-token", transport=httpx.MockTransport(handler))
    assert client.case(7) == {"id": 7} and seen["auth"] == "Bearer copilot-token"
    with pytest.raises(KeyError):
        client.case(404)
