# Phase-0 spike: academic coverage of AgentCore Web Search (ticket #7)

Closes the largest open technical question: whether the Amazon web index covers
academic sources at useful depth. Cost ≈ $0.21 (~30 queries at $7/1,000).

## 1. One-time AWS setup (us-east-1)

Prereq: an AWS profile with permissions to create AgentCore gateways and IAM roles
(`aws sso login --profile noosphere`).

1. **Gateway service role** — IAM role trusted by `bedrock-agentcore.amazonaws.com`
   with this permissions policy (account ID and gateway ID filled in after step 2):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {"Sid": "InvokeWebSearch", "Effect": "Allow",
        "Action": "bedrock-agentcore:InvokeWebSearch",
        "Resource": "arn:aws:bedrock-agentcore:us-east-1:aws:tool/web-search.v1"}
     ]
   }
   ```

2. **Gateway** — console (Bedrock AgentCore → Gateways → Create) or API:
   protocol **MCP**, inbound authorizer **AWS IAM**, the service role above.
   Note the MCP endpoint URL: `https://gateway-<id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp`.

3. **Web-search target** — add a connector target with **`connectorId: "web-search"`
   pinned to `version: "1.2.0"`** (the default is 1.1.0, which has no domain/date
   filters — the pin is load-bearing):

   ```python
   import boto3
   gw = boto3.client("bedrock-agentcore-control", region_name="us-east-1")
   gw.create_gateway_target(
       gatewayIdentifier="<gateway-id>",
       name="web-search-tool",
       targetConfiguration={"mcp": {"connector": {
           "source": {"connectorId": "web-search", "version": "1.2.0"},
           "configurations": [{"name": "WebSearch", "parameterValues": {}}]}}},
       credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
   )
   ```

4. **Caller permission** — the identity running the spike needs
   `bedrock-agentcore:InvokeGateway` on the gateway ARN.

Exact request shapes come from the AgentCore Developer Guide
(`gateway-add-target-api-target-config.html`) — treat this page as authoritative
if the console differs.

## 2. Run

```bash
AWS_PROFILE=noosphere uv run scripts/phase0_spike.py \
    --gateway-url https://gateway-<id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp
```

Outputs `phase0_results.jsonl` (raw results) and a coverage summary: results per
domain, per-query counts, `publishedDate` presence, zero-result queries. The ~30
queries span both sides of the first Field — agent-memory (arXiv-side), memory
science (PubMed-side), and the intersection.

## 3. Verdict

Record on ticket #7:

- **Healthy**: most queries return >5 results; `arxiv.org` and
  `pubmed.ncbi.nlm.nih.gov` both well represented → Web Search stays the
  discovery front door as designed.
- **Thin**: many zero-result queries or one domain absent → Web Search demotes
  to enrichment-only and discovery shifts fully to scholarly APIs — this
  materially changes the gap-analysis design (#12), so record the verdict
  before working that ticket.

Also note snippet quality and `publishedDate` format oddities — the tolerant
date parser (query-planner fog item) is calibrated from this sample.
