// k6 load test: document questions against /v1/ask, with the repetition real on-call and
// review traffic has. Run once with the answer cache off and once with it on:
//   k6 run -e BASE_URL=http://127.0.0.1:8020 -e PASSWORD=... --summary-export off.json deploy/load/ask.js
// Thresholds make it a gate in CI: errors under 1%, p95 under the budget, and no near-miss
// question ever answered from cache.
import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://127.0.0.1:8020";
const MIX = JSON.parse(open("./questions.json")).questions;
const nearMissFromCache = new Counter("near_miss_served_from_cache");
const cacheHits = new Counter("cache_hits");

export const options = {
  scenarios: {
    oncall: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.RATE || 4),
      timeUnit: "1s",
      duration: __ENV.DURATION || "60s",
      preAllocatedVUs: 20,
      maxVUs: 60,
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{kind:ask}": [`p(95)<${__ENV.P95_MS || 4000}`],
    near_miss_served_from_cache: ["count==0"],
  },
};

export function setup() {
  const tokens = {};
  for (const user of [...new Set(MIX.map((q) => q.user))]) {
    const res = http.post(`${BASE}/v1/auth/login`, JSON.stringify({ username: user, password: __ENV.PASSWORD }),
      { headers: { "Content-Type": "application/json" } });
    check(res, { "signed in": (r) => r.status === 200 });
    tokens[user] = res.json("token");
  }
  return tokens;
}

export default function (tokens) {
  const q = MIX[Math.floor(Math.random() * MIX.length)];
  const res = http.post(`${BASE}/v1/ask`, JSON.stringify({ message: q.message }), {
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokens[q.user]}` },
    tags: { kind: "ask" },
    timeout: "60s",
  });
  check(res, {
    "200": (r) => r.status === 200,
    "answered or declined, no error": (r) => r.body.includes("event: answer") && !r.body.includes("event: error"),
  });
  if (res.body.includes('"cache:hit"')) {
    cacheHits.add(1);
    // A near-miss may be served its own earlier answer, never one produced for another question.
    const answer = res.body.split("event: answer\ndata: ")[1];
    const source = answer ? JSON.parse(answer.split("\n")[0]).result.question : "";
    if (q.near_miss && source !== q.message) nearMissFromCache.add(1);
  }
}
