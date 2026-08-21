# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3"]
# ///
"""Phase-0 spike infra (ticket #7): stand up and tear down the Gateway.

  up:   validate the CFN schema for AWS::BedrockAgentCore::Gateway (property
        names may drift from the template's best-known guesses), create the
        stack from infra/phase0-spike.yaml, then attach the web-search
        connector target PINNED TO 1.2.0 via the control-plane API.
        Prints the Gateway MCP URL to pass to scripts/phase0_spike.py.
  down: delete the connector target, then delete the stack.

Usage:
  AWS_PROFILE=<profile> uv run scripts/phase0_infra.py up
  AWS_PROFILE=<profile> uv run scripts/phase0_infra.py down
"""

import argparse
import json
import pathlib
import sys
import time

import boto3

REGION = "us-east-1"
STACK = "noosphere-phase0-spike"
TARGET_NAME = "web-search-tool"
TEMPLATE = pathlib.Path(__file__).parent.parent / "infra" / "phase0-spike.yaml"


def check_cfn_schema(cfn) -> None:
    """Warn early if the template's Gateway property names drift from the live schema."""
    try:
        t = cfn.describe_type(Type="RESOURCE", TypeName="AWS::BedrockAgentCore::Gateway")
        props = set(json.loads(t["Schema"]).get("properties", {}))
        expected = {"Name", "ProtocolType", "AuthorizerType", "RoleArn"}
        missing = expected - props
        if missing:
            print(f"WARNING: live CFN schema lacks {sorted(missing)}; "
                  f"available properties: {sorted(props)}", file=sys.stderr)
            print("Edit infra/phase0-spike.yaml to match before continuing.", file=sys.stderr)
            sys.exit(2)
        tgt = cfn.describe_type(Type="RESOURCE", TypeName="AWS::BedrockAgentCore::GatewayTarget")
        if "connector" in tgt["Schema"].lower():
            print("NOTE: GatewayTarget CFN schema mentions connectors — the target "
                  "could be folded into the template; see ticket #7.", file=sys.stderr)
    except cfn.exceptions.TypeNotFoundException:
        print("ERROR: AWS::BedrockAgentCore::Gateway not registered in this region.", file=sys.stderr)
        sys.exit(2)


def stack_outputs(cfn) -> dict:
    stacks = cfn.describe_stacks(StackName=STACK)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def up() -> None:
    cfn = boto3.client("cloudformation", region_name=REGION)
    check_cfn_schema(cfn)
    cfn.create_stack(
        StackName=STACK,
        TemplateBody=TEMPLATE.read_text(),
        Capabilities=["CAPABILITY_NAMED_IAM"],
    )
    print(f"creating stack {STACK} ...", file=sys.stderr)
    cfn.get_waiter("stack_create_complete").wait(StackName=STACK)
    outs = stack_outputs(cfn)
    gateway_id = outs["GatewayId"]

    gw = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gw.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=TARGET_NAME,
        targetConfiguration={"mcp": {"connector": {
            "source": {"connectorId": "web-search", "version": "1.2.0"},
            "configurations": [{"name": "WebSearch", "parameterValues": {}}],
        }}},
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )
    print(f"waiting for target {TARGET_NAME} ...", file=sys.stderr)
    for _ in range(60):
        items = gw.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        target = next((t for t in items if t.get("name") == TARGET_NAME), None)
        if target and target.get("status") == "READY":
            break
        time.sleep(5)
    print(json.dumps({"gateway_id": gateway_id, "gateway_url": outs["GatewayUrl"]}))
    print(f"\nNext: AWS_PROFILE=$AWS_PROFILE uv run scripts/phase0_spike.py "
          f"--gateway-url {outs['GatewayUrl']}", file=sys.stderr)


def down() -> None:
    cfn = boto3.client("cloudformation", region_name=REGION)
    gw = boto3.client("bedrock-agentcore-control", region_name=REGION)
    try:
        gateway_id = stack_outputs(cfn)["GatewayId"]
        for t in gw.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", []):
            if t.get("name") == TARGET_NAME:
                gw.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
                print(f"deleted target {t['targetId']}", file=sys.stderr)
                time.sleep(10)
    except Exception as e:  # stack may already be half-gone; still try delete
        print(f"target cleanup: {e}", file=sys.stderr)
    cfn.delete_stack(StackName=STACK)
    cfn.get_waiter("stack_delete_complete").wait(StackName=STACK)
    print("stack deleted", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["up", "down"])
    args = ap.parse_args()
    up() if args.action == "up" else down()


if __name__ == "__main__":
    main()
