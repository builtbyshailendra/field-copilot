"""Inject five failures into a real kind cluster and investigate each one with live kubectl data.

Runs in CI (.github/workflows/lab.yml) after `kind create cluster`. For each scenario:
1. deploy a healthy revision 1 of a service in namespace `freight`;
2. break it the way the matching runbook describes (a release, a config change, ...);
3. wait until Kubernetes shows the failure;
4. run the investigator agent (fake model = the rules baseline) with KubeCluster tools;
5. check the top cause, that every evidence excerpt is in the live tool output, and
   that nothing ran without approval. Then approve the proposed rollback (where there is
   one) and check the service recovers.

Images: busybox only (pinned tag), so the lab has no other dependencies.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

from fieldcopilot.agent import ApprovalService, Limits, User
from fieldcopilot.llm import FAKE_MODELS, LLM, FakeProvider
from fieldcopilot.tenants.corvane.cluster import KubeCluster
from fieldcopilot.tenants.corvane.investigation import build_investigator, check_evidence, fake_investigator

NS = "freight"
IMAGE = "busybox:1.36.1"


def kubectl(*args: str, stdin: str | None = None, check: bool = True) -> str:
    out = subprocess.run(["kubectl", "-n", NS, *args], input=stdin, capture_output=True, text=True, timeout=180)
    if check and out.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {out.stderr}")
    return out.stdout


def deployment(name: str, container: dict, extra_spec: dict | None = None) -> str:
    spec = {
        "replicas": 1, "progressDeadlineSeconds": 60, "revisionHistoryLimit": 5,
        "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}},
        "selector": {"matchLabels": {"app": name}},
        "template": {"metadata": {"labels": {"app": name}},
                     "spec": {"terminationGracePeriodSeconds": 2, "containers": [{"name": name, **container}]}},
    }
    spec.update(extra_spec or {})
    return json.dumps({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "labels": {"app": name}}, "spec": spec})


def apply(manifest: str) -> None:
    kubectl("apply", "-f", "-", stdin=manifest)


def wait_ready(name: str, timeout: int = 120) -> None:
    kubectl("rollout", "status", f"deployment/{name}", f"--timeout={timeout}s")


def wait_for(predicate, timeout: int = 150, every: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(every)
    return False


def pod_states(name: str) -> list[dict]:
    return KubeCluster(NS).pods(name)["pods"]


SLEEP = {"image": IMAGE, "command": ["sh", "-c", "echo started; sleep 3600"]}
HTTPD = {"image": IMAGE, "command": ["sh", "-c", "mkdir -p /www && echo ok > /www/index.html && httpd -f -p 8080 -h /www"],
         "ports": [{"containerPort": 8080}]}


def scenario_bad_release() -> tuple[str, str]:
    apply(deployment("shipment-api", SLEEP))
    wait_ready("shipment-api")
    crash = {"image": IMAGE, "command": ["sh", "-c",
             "echo 'INFO starting shipment-api 2.14.0'; echo \"KeyError: 'LABEL_SERVICE_TOKEN'\"; exit 1"]}
    apply(deployment("shipment-api", crash))
    wait_for(lambda: any(p["restarts"] >= 2 for p in pod_states("shipment-api")))
    return "shipment-api", "bad_release"


def scenario_image_pull() -> tuple[str, str]:
    apply(deployment("label-printer", SLEEP))
    wait_ready("label-printer")
    apply(deployment("label-printer", {**SLEEP, "image": "busybox:1.36.O"}))
    wait_for(lambda: any(p["status"] in ("ImagePullBackOff", "ErrImagePull") for p in pod_states("label-printer")))
    return "label-printer", "image_pull"


def scenario_memory_leak() -> tuple[str, str]:
    apply(deployment("tracking-worker", {**SLEEP, "resources": {"limits": {"memory": "32Mi"}}}))
    wait_ready("tracking-worker")
    leak = {"image": IMAGE, "command": ["sh", "-c", "echo 'INFO tracking-worker 0.88.0'; tail /dev/zero"],
            "resources": {"limits": {"memory": "32Mi"}}}
    apply(deployment("tracking-worker", leak))
    wait_for(lambda: any(p.get("last_state", {}).get("reason") == "OOMKilled" for p in pod_states("tracking-worker")))
    return "tracking-worker", "memory_leak"


def scenario_readiness() -> tuple[str, str]:
    probe = lambda path: {"httpGet": {"path": path, "port": 8080}, "periodSeconds": 2, "failureThreshold": 2}
    apply(deployment("rate-quote", {**HTTPD, "readinessProbe": probe("/")}))
    wait_ready("rate-quote")
    apply(deployment("rate-quote", {**HTTPD, "readinessProbe": probe("/healthz/ready")}))
    wait_for(lambda: "Readiness probe failed" in json.dumps(KubeCluster(NS).events("rate-quote")))
    return "rate-quote", "readiness_failure"


def scenario_config_change() -> tuple[str, str]:
    cm = lambda key, change="": json.dumps({"apiVersion": "v1", "kind": "ConfigMap",
                                            "metadata": {"name": "carrier-gateway-config", "labels": {"app": "carrier-gateway"},
                                                         "annotations": {"lab/change": change} if change else {}},
                                            "data": {key: "https://carriers.example/api"}})
    apply(cm("CARRIER_API_URL"))
    env = [{"name": "CARRIER_API_URL", "valueFrom": {"configMapKeyRef": {"name": "carrier-gateway-config", "key": "CARRIER_API_URL"}}}]
    apply(deployment("carrier-gateway", {**SLEEP, "env": env}))
    wait_ready("carrier-gateway")
    time.sleep(70)  # make the config change clearly newer than the deploy
    kubectl("delete", "configmap", "carrier-gateway-config")
    apply(cm("CARRIER_URL", "renamed key CARRIER_API_URL to CARRIER_URL"))
    kubectl("delete", "pod", "-l", "app=carrier-gateway", "--wait=false")
    wait_for(lambda: any(p["status"] == "CreateContainerConfigError" for p in pod_states("carrier-gateway")))
    return "carrier-gateway", "config_change"


SCENARIOS = [scenario_bad_release, scenario_image_pull, scenario_memory_leak, scenario_readiness, scenario_config_change]


def investigate(service: str):
    cluster = KubeCluster(NS)
    approvals = ApprovalService(None, {"corvane-freight": "oncall-lead"})
    llm = LLM(FakeProvider().on("investigate", fake_investigator), models=dict(FAKE_MODELS))
    agent = build_investigator(llm, cluster, approvals, limits=Limits(max_steps=8))
    run = agent.run(f"Alert on {service} in namespace freight (service: {service})")
    return run, cluster, approvals


def main() -> int:
    subprocess.run(["kubectl", "create", "namespace", NS], capture_output=True)
    failures = 0
    rows = ["| Scenario | Expected | Ranked first | Evidence checks | Unapproved actions | After approval |", "|---|---|---|---|---|---|"]
    for build in SCENARIOS:
        service, expected = build()
        run, cluster, approvals = investigate(service)
        report = run.final
        top = report.hypotheses[0].cause if report and report.hypotheses else "none"
        check = check_evidence(report, run) if report else None
        unapproved = len(cluster.actions)
        recovered = "n/a"
        pending = approvals.pending("corvane-freight")
        if pending and pending[0].tool == "do_rollback":
            lead = User("lab-lead", "corvane-freight", frozenset({"oncall-lead"}))
            approvals.approve(pending[0].id, lead, pending[0].hash, "lab drill")
            try:
                wait_ready(service, 120)
                recovered = "recovered"
            except RuntimeError:
                recovered = "not recovered"
        ok = top == expected and check is not None and check.ratio == 1.0 and unapproved == 0 and recovered != "not recovered"
        failures += not ok
        rows.append(f"| {build.__name__.removeprefix('scenario_')} | {expected} | {top} | "
                    f"{check.ratio:.0%} | {unapproved} | {recovered} |" if check else f"| {build.__name__} | {expected} | none | - | - | - |")
        if not ok:
            print(f"FAILED {service}: status={run.status} error={run.error}\n{json.dumps(run.evidence, indent=1)[:3000]}")
    print("\n".join(rows))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
