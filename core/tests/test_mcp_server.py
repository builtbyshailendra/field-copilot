import anyio
import pytest
from mcp.client.client import Client

from fieldcopilot.agent import ApprovalService
from fieldcopilot.mcp_server import Caller, HashedTokenVerifier, build_server, token_hash
from fieldcopilot.tenants.corvane.cluster import FixtureCluster, load_scenario
from fieldcopilot.tenants.corvane.tools import build_tools


def corvane_server(audit=None):
    cluster = FixtureCluster(load_scenario("s01"))
    tools = build_tools(cluster)
    approvals = ApprovalService(tools, {"corvane-freight": "oncall-lead"})
    server = build_server("corvane-freight", tools, approvals, default_caller=Caller("asha", "corvane-freight"),
                          audit=audit or (lambda e, d: None))
    return server, cluster, approvals


def run(coro):
    return anyio.run(coro)


def test_only_offered_tools_are_exposed_with_honest_annotations():
    server, _, _ = corvane_server()

    async def go():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    tools = {t.name: t for t in run(go)}
    assert "do_rollback" not in tools and "propose_rollback" in tools and "get_pods" in tools
    assert tools["get_pods"].annotations.read_only_hint is True
    assert tools["propose_rollback"].annotations.read_only_hint is False
    assert set(tools["get_logs"].input_schema["properties"]) == {"pod", "previous", "tail"}


def test_read_only_call_returns_live_tool_output_and_is_audited():
    events = []
    server, _, _ = corvane_server(lambda e, d: events.append((e, d)))

    async def go():
        async with Client(server) as client:
            return await client.call_tool("get_pods", {"service": "shipment-api"})

    result = run(go)
    assert "CrashLoopBackOff" in result.content[0].text
    assert events[0][0] == "mcp.call" and events[0][1]["user"] == "asha" and events[0][1]["tool"] == "get_pods"


def test_proposal_through_mcp_creates_an_approval_and_runs_nothing():
    server, cluster, approvals = corvane_server()

    async def go():
        async with Client(server) as client:
            return await client.call_tool("propose_rollback", {"service": "shipment-api", "to_revision": 213, "reason": "bad release"})

    text = run(go).content[0].text
    assert "APR-0001" in text and "has NOT run" in text and "oncall-lead" in text
    assert cluster.actions == [] and approvals.pending("corvane-freight")[0].tool == "do_rollback"


def test_token_verifier_accepts_only_this_tenants_hashed_tokens():
    entries = {token_hash("corvane-secret"): {"user": "ravi", "tenant": "corvane-freight"},
               token_hash("tallowmere-secret"): {"user": "nina", "tenant": "tallowmere-health"}}
    verifier = HashedTokenVerifier("corvane-freight", entries)
    ok = run(lambda: verifier.verify_token("corvane-secret"))
    assert ok is not None and ok.subject == "ravi" and ok.claims["tenant"] == "corvane-freight"
    assert run(lambda: verifier.verify_token("tallowmere-secret")) is None
    assert run(lambda: verifier.verify_token("guess")) is None
    assert "corvane-secret" not in str(entries)
