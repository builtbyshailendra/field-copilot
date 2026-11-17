from pathlib import Path

import yaml

from fieldcopilot.evals.feedback import Feedback, FeedbackStore
from fieldcopilot.evals.judge import calibrate, cohen_kappa
from fieldcopilot.evals.suite import check_gates, make_llm, run


def test_cohen_kappa():
    assert cohen_kappa(["pass", "fail"] * 5, ["pass", "fail"] * 5) == 1.0
    assert cohen_kappa(["pass", "fail", "pass", "fail"], ["pass", "pass", "pass", "pass"]) == 0.0


def test_heuristic_judge_calibration_is_measured_not_assumed():
    cal = calibrate(make_llm("fake"))
    assert cal.n == 20 and 0.4 <= cal.kappa <= 0.6 and "j10" in cal.disagreements


def test_gates_report_missing_and_breached_values():
    metrics = {"routing": {"accuracy": 0.8}, "redteam": {"passed": 19}}
    gates = {"routing": {"accuracy": {"min": 0.9}}, "redteam": {"passed": {"min": 19}}, "judge": {"kappa": {"min": 0.5}}}
    assert check_gates(metrics, gates) == ["routing.accuracy = 0.8 < 0.9", "judge.kappa: missing"]


def test_full_suite_passes_its_fake_gates():
    metrics = run("fake", "hashing")
    gates = yaml.safe_load((Path(__file__).resolve().parents[1] / "evals" / "gates.yaml").read_text(encoding="utf-8"))
    assert check_gates(metrics, gates["fake+hashing"]) == []


def test_thumbs_down_becomes_a_redacted_candidate_and_can_be_promoted(tmp_path):
    store = FeedbackStore(tmp_path / "feedback")
    fb = Feedback("tallowmere-health", "What's the knee X-ray window for member TM-55012?", "90 days", "down", "policy says 60")
    assert store.record(fb) is True
    assert store.record(Feedback("tallowmere-health", "what's the knee x-ray window for member TM-55012", "x", "down")) is False
    assert store.record(Feedback("tallowmere-health", "thanks", "ok", "up")) is False
    candidate = store.candidates("tallowmere-health")[0]
    assert "TM-55012" not in candidate["question"] and candidate["id"] == "fb-001"
    target = tmp_path / "qa_from_feedback.yaml"
    case = store.promote("tallowmere-health", "fb-001", facts=["60 days"], cite=["mp-118-knee-mri#criteria"], target=target)
    assert yaml.safe_load(target.read_text(encoding="utf-8")) == [case]
