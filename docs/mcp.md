# Using the copilot's tools from Claude Desktop or Claude Code (MCP)

Each tenant has its own MCP server exposing that tenant's tools. Read-only tools work as
usual. Proposal tools (`propose_rollback`, `propose_rfi_letter`, ...) only create an
approval request; nothing runs until a person with the tenant's approver role approves it
in the copilot. The tools that actually act are never exposed over MCP.

## Local (stdio)

The caller's identity comes from the environment of the person running the client.

```json
{
  "mcpServers": {
    "corvane-copilot": {
      "command": "python",
      "args": ["-m", "fieldcopilot.mcp_server", "--tenant", "corvane-freight"],
      "env": { "FIELD_MCP_USER": "asha", "FIELD_CLUSTER": "kube", "FIELD_NAMESPACE": "freight" }
    }
  }
}
```

For Claude Code: `claude mcp add corvane-copilot -- python -m fieldcopilot.mcp_server --tenant corvane-freight`.

## Shared server (Streamable HTTP)

```bash
FIELD_MCP_TOKENS=/etc/field-copilot/mcp-tokens.json \
FIELD_MCP_URL=https://copilot.corvane.example/mcp \
python -m fieldcopilot.mcp_server --tenant corvane-freight --http
```

`mcp-tokens.json` holds SHA-256 hashes only, each bound to one user and tenant:

```json
{ "<sha256 of the token>": { "user": "ravi", "tenant": "corvane-freight" } }
```

A token issued for another tenant is rejected. Every call is audited with the user and
arguments. In a real deployment this is replaced by the customer's identity provider
(OAuth), which the MCP SDK's `AuthSettings` supports; static hashed tokens are the
pilot-stage option.
