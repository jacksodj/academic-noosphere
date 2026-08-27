"""In-app AWS setup (issue #28): Bedrock model-catalog verification and Web
Search Gateway discovery/creation, so onboarding never points users at repo
scripts. All boto3 work is blocking — callers run it via asyncio.to_thread;
GatewayCreate owns its own thread offload like pipeline.model_fetch.ModelFetch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

STACK_NAME = "noosphere-websearch"
TARGET_NAME = "web-search-tool"
CONNECTOR_VERSION = "1.2.0"  # load-bearing pin (see docs/reference-notes.md)


def gateway_template() -> Path:
    """The bundled CFN template: sys._MEIPASS data when frozen, repo file in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "infra" / "gateway.yaml"
    return Path(__file__).resolve().parents[2] / "packaging" / "gateway.yaml"


def _boto_config():
    from botocore.config import Config as BotoConfig

    return BotoConfig(connect_timeout=5, read_timeout=20, retries={"max_attempts": 1})


def _strip_region_prefix(model_id: str) -> str:
    for prefix in ("us.", "eu.", "apac.", "global."):
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    return model_id


def model_catalog_status(region: str, models: dict[str, str]) -> dict:
    """Whether each configured model (role -> id) is usable in this account.

    ``authorized`` is the console Model-access verdict when the account/API
    supports GetFoundationModelAvailability, else None (fall back to whether
    the id is listed at all). BLOCKING — run via to_thread.
    """
    import boto3.session

    bedrock = boto3.session.Session().client("bedrock", region_name=region, config=_boto_config())
    listed_ids: set[str] = set()
    try:
        for p in bedrock.list_inference_profiles().get("inferenceProfileSummaries", []):
            listed_ids.add(p.get("inferenceProfileId", ""))
    except Exception as e:
        logger.warning("list_inference_profiles failed: %s", e)
    try:
        for m in bedrock.list_foundation_models().get("modelSummaries", []):
            listed_ids.add(m.get("modelId", ""))
    except Exception as e:
        logger.warning("list_foundation_models failed: %s", e)

    rows = []
    for role, model_id in models.items():
        authorized = None
        try:
            avail = bedrock.get_foundation_model_availability(
                modelId=_strip_region_prefix(model_id)
            )
            entitled = avail.get("entitlementAvailability") == "AVAILABLE"
            agreement = avail.get("agreementAvailability", {}).get("status")
            authorized = entitled and agreement in (None, "AVAILABLE")
        except Exception:
            pass  # older botocore or denied — fall back to listing only
        rows.append(
            {
                "role": role,
                "model_id": model_id,
                "listed": model_id in listed_ids
                or _strip_region_prefix(model_id) in listed_ids,
                "authorized": authorized,
            }
        )
    return {
        "region": region,
        "models": rows,
        "console_url": f"https://{region}.console.aws.amazon.com/bedrock/home?region={region}#/modelaccess",
    }


def list_websearch_gateways(region: str) -> list[dict]:
    """Existing AgentCore gateways, flagged for a web-search target. BLOCKING."""
    import boto3.session

    gw = boto3.session.Session().client("bedrock-agentcore-control", region_name=region, config=_boto_config())
    out = []
    for g in gw.list_gateways().get("items", []):
        gid = g.get("gatewayId") or g.get("gatewayIdentifier")
        if not gid:
            continue
        url = g.get("gatewayUrl")
        if not url:
            try:
                url = gw.get_gateway(gatewayIdentifier=gid).get("gatewayUrl")
            except Exception:
                url = None
        has_ws = False
        try:
            targets = gw.list_gateway_targets(gatewayIdentifier=gid).get("items", [])
            has_ws = any(
                "web-search" in json.dumps(t, default=str).lower()
                or "websearch" in (t.get("name") or "").lower()
                for t in targets
            )
        except Exception:
            pass
        out.append(
            {
                "id": gid,
                "name": g.get("name"),
                "status": g.get("status"),
                "url": url,
                "web_search": has_ws,
            }
        )
    return out


@dataclass
class GatewayCreate:
    """One in-process gateway creation (CFN stack + pinned connector target);
    the API polls snapshot(). Mirrors pipeline.model_fetch.ModelFetch."""

    status: str = "idle"  # idle | creating | done | failed
    step: str = ""
    gateway_url: str | None = None
    error: str | None = None
    _task: asyncio.Task | None = field(default=None, repr=False)

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "step": self.step,
            "gateway_url": self.gateway_url,
            "error": self.error,
        }

    def start(self, region: str) -> bool:
        if self._task is not None and not self._task.done():
            return False
        self.status, self.step, self.error, self.gateway_url = "creating", "starting", None, None
        self._task = asyncio.get_running_loop().create_task(self._run(region))
        return True

    async def _run(self, region: str) -> None:
        try:
            self.gateway_url = await asyncio.to_thread(self._create, region)
            self.status = "done"
        except Exception as e:
            self.status, self.error = "failed", str(e)
            logger.warning("gateway creation failed: %s", e)

    def _create(self, region: str) -> str:
        import boto3.session

        session = boto3.session.Session()
        cfn = session.client("cloudformation", region_name=region)
        try:
            self.step = "creating CloudFormation stack (gateway + role)"
            cfn.create_stack(
                StackName=STACK_NAME,
                TemplateBody=gateway_template().read_text(),
                Capabilities=["CAPABILITY_NAMED_IAM"],
            )
            cfn.get_waiter("stack_create_complete").wait(StackName=STACK_NAME)
        except cfn.exceptions.AlreadyExistsException:
            self.step = "stack already exists — reusing it"
        outs = {
            o["OutputKey"]: o["OutputValue"]
            for o in cfn.describe_stacks(StackName=STACK_NAME)["Stacks"][0].get("Outputs", [])
        }
        gateway_id, gateway_url = outs["GatewayId"], outs["GatewayUrl"]

        gw = session.client("bedrock-agentcore-control", region_name=region)
        targets = gw.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
        if not any((t.get("name") or "") == TARGET_NAME for t in targets):
            self.step = f"attaching web-search connector (pinned {CONNECTOR_VERSION})"
            gw.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name=TARGET_NAME,
                targetConfiguration={
                    "mcp": {
                        "connector": {
                            "source": {
                                "connectorId": "web-search",
                                "version": CONNECTOR_VERSION,
                            },
                            "configurations": [
                                {"name": "WebSearch", "parameterValues": {}}
                            ],
                        }
                    }
                },
                credentialProviderConfigurations=[
                    {"credentialProviderType": "GATEWAY_IAM_ROLE"}
                ],
            )
        self.step = "waiting for the target to be READY"
        for _ in range(60):
            items = gw.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])
            target = next((t for t in items if t.get("name") == TARGET_NAME), None)
            if target and target.get("status") == "READY":
                break
            time.sleep(5)
        else:
            raise RuntimeError(f"target {TARGET_NAME} never reached READY")
        self.step = "ready"
        return gateway_url


creator = GatewayCreate()
