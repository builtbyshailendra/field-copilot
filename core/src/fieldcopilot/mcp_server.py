"""An MCP server per tenant: the copilot's tools inside engineers' and nurses' own AI clients.

The same rules apply as in the copilot's own agent:
- only the tenant's tools are exposed, and "acts" tools never are;
- read-only tools are annotated readOnlyHint=true;
- a "proposes" tool creates an approval request and says plainly that nothing ran,
  so a client like Claude Desktop can't take an action that skips the on-call lead or
  the nurse;
- every call is audited with the caller's identity.

Identity:
- stdio (local): the user and tenant come from FIELD_MCP_USER / FIELD_MCP_TENANT, the
  person running the client on their own machine.
- Streamable HTTP: a bearer token. Tokens are stored only as SHA-256 hashes, each bound
  to one tenant and user; a token for another tenant is rejected.

    python -m fieldcopilot.mcp_server --tenant corvane-freight            # stdio
    python -m fieldcopilot.mcp_server --tenant corvane-freight --http     # needs FIELD_MCP_TOKENS
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
from dataclasses import dataclass
from typing import Any, Callable

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic_core import PydanticUndefined

from fieldcopilot.agent import ApprovalService, Tool, ToolContext, ToolRegistry, run_tool

AuditSink = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class Caller:
    user: str
    tenant: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class HashedTokenVerifier:
    """Bearer tokens for one tenant. `entries` maps sha256(token) -> {"user": ..., "tenant": ...}."""

    def __init__(self, tenant: str, entries: dict[str, dict[str, str]]):
        self.tenant, self.entries = tenant, entries

    async def verify_token(self, token: str) -> AccessToken | None:
        entry = self.entries.get(token_hash(token))
        if not entry or entry.get("tenant") != self.tenant:
            return None
        return AccessToken(token=token, client_id=entry["user"], scopes=["copilot"],
                           subject=entry["user"], claims={"tenant": entry["tenant"]})


def current_caller(default: Caller) -> Caller:
    token = get_access_token()
    if token is None:
        return default
    return Caller(user=token.subject or token.client_id, tenant=(token.claims or {}).get("tenant", default.tenant))


def _signature(tool: Tool) -> inspect.Signature:
    params = []
    for name, field in tool.input_model.model_fields.items():
        default = inspect.Parameter.empty if field.default is PydanticUndefined else field.default
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=field.annotation, default=default))
    return inspect.Signature(params, return_annotation=str)


def _wrap(tool: Tool, tenant: str, approvals: ApprovalService, default: Caller, audit: AuditSink):
    def call(**kwargs: Any) -> str:
        caller = current_caller(default)
        if caller.tenant != tenant:
            return "Not available for this tenant."
        audit("mcp.call", {"tenant": tenant, "user": caller.user, "tool": tool.name, "args": kwargs})
        ctx = ToolContext(tenant, run_id=f"mcp-{caller.user}", user=caller.user)
        if tool.side_effect == "proposes":
            parsed = tool.validate(kwargs)
            summary = tool.summarize(parsed) if tool.summarize else f"{tool.name} {parsed.model_dump()}"
            approval = approvals.request(tenant, ctx.run_id, tool.executes or tool.name, parsed.model_dump(), summary)
            return (f"Approval request {approval.id} created: {summary}\n\nIt has NOT run. A person with the "
                    f"{approvals.approver_roles.get(tenant)} role must approve it in the copilot.")
        result, _ = run_tool(tool, ctx, kwargs)
        return ("ERROR: " if result.is_error else "") + result.as_text()

    call.__name__ = tool.name
    call.__doc__ = tool.description
    call.__signature__ = _signature(tool)
    call.__annotations__ = {n: f.annotation for n, f in tool.input_model.model_fields.items()} | {"return": str}
    return call


def build_server(tenant: str, tools: ToolRegistry, approvals: ApprovalService, allowlist: list[str] | None = None,
                 default_caller: Caller | None = None, audit: AuditSink | None = None,
                 token_verifier: HashedTokenVerifier | None = None, auth=None) -> MCPServer:
    server = MCPServer(name=f"field-copilot-{tenant}",
                       instructions=f"Tools for {tenant}. Proposals create approval requests; nothing runs without a person.",
                       token_verifier=token_verifier, auth=auth)
    default = default_caller or Caller(os.environ.get("FIELD_MCP_USER", "local-user"), tenant)
    sink = audit or (lambda event, data: None)
    for tool in tools.offered(allowlist):
        server.add_tool(_wrap(tool, tenant, approvals, default, sink), name=tool.name, description=tool.description,
                        annotations=ToolAnnotations(readOnlyHint=tool.side_effect == "none", destructiveHint=False,
                                                    idempotentHint=tool.side_effect == "none", openWorldHint=False))
    return server


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - wiring for real use
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True, choices=["corvane-freight", "tallowmere-health"])
    parser.add_argument("--http", action="store_true")
    args = parser.parse_args(argv)
    from fieldcopilot.wiring import build_tenant_tools

    tools, approvals, allowlist = build_tenant_tools(args.tenant)
    verifier = auth = None
    if args.http:
        from mcp.server.auth.settings import AuthSettings

        entries = json.loads(open(os.environ["FIELD_MCP_TOKENS"], encoding="utf-8").read())
        verifier = HashedTokenVerifier(args.tenant, entries)
        url = os.environ.get("FIELD_MCP_URL", "http://127.0.0.1:8765")
        auth = AuthSettings(issuer_url=url, resource_server_url=url, required_scopes=["copilot"])
    server = build_server(args.tenant, tools, approvals, allowlist, token_verifier=verifier, auth=auth)
    server.run("streamable-http" if args.http else "stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
