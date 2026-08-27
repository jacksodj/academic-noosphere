"""aws_setup: catalog verdicts, gateway listing shape, template resolution.

boto3 is faked at the module seam — no AWS calls, no credentials needed.
"""

from types import SimpleNamespace

import pytest

from noosphere import aws_setup


class FakeBedrock:
    def __init__(self, availability: dict | Exception | None = None):
        self._availability = availability

    def list_inference_profiles(self):
        return {
            "inferenceProfileSummaries": [
                {"inferenceProfileId": "us.anthropic.claude-opus-4-6-v1"}
            ]
        }

    def list_foundation_models(self):
        return {"modelSummaries": [{"modelId": "anthropic.claude-haiku-4-5-20251001-v1:0"}]}

    def get_foundation_model_availability(self, modelId):
        if isinstance(self._availability, Exception):
            raise self._availability
        if self._availability is None:
            raise AttributeError("api not available")
        return self._availability


def _patch_boto3(monkeypatch, client):
    import sys

    session_cls = lambda: SimpleNamespace(client=lambda *a, **kw: client)  # noqa: E731
    fake_session_mod = SimpleNamespace(Session=session_cls)
    fake_boto3 = SimpleNamespace(client=lambda *a, **kw: client, session=fake_session_mod)
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "boto3.session", fake_session_mod)


def test_catalog_authorized(monkeypatch):
    _patch_boto3(
        monkeypatch,
        FakeBedrock(
            {"entitlementAvailability": "AVAILABLE", "agreementAvailability": {"status": "AVAILABLE"}}
        ),
    )
    out = aws_setup.model_catalog_status(
        "us-east-1",
        {
            "opus": "us.anthropic.claude-opus-4-6-v1",
            "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        },
    )
    assert [m["listed"] for m in out["models"]] == [True, True]  # profile id + stripped id
    assert all(m["authorized"] is True for m in out["models"])
    assert "modelaccess" in out["console_url"]


def test_catalog_availability_api_missing_falls_back_to_listing(monkeypatch):
    _patch_boto3(monkeypatch, FakeBedrock(None))
    out = aws_setup.model_catalog_status(
        "us-east-1", {"opus": "us.anthropic.claude-opus-4-6-v1", "haiku": "nope.model"}
    )
    opus, haiku = out["models"]
    assert opus["listed"] is True and opus["authorized"] is None
    assert haiku["listed"] is False


def test_strip_region_prefix():
    assert aws_setup._strip_region_prefix("us.anthropic.x") == "anthropic.x"
    assert aws_setup._strip_region_prefix("anthropic.x") == "anthropic.x"
    assert aws_setup._strip_region_prefix("global.anthropic.x") == "anthropic.x"


class FakeAgentCore:
    def list_gateways(self):
        return {"items": [{"gatewayId": "gw-1", "name": "noosphere-websearch", "status": "READY"}]}

    def get_gateway(self, gatewayIdentifier):
        return {"gatewayUrl": f"https://{gatewayIdentifier}.gateway.example/mcp"}

    def list_gateway_targets(self, gatewayIdentifier):
        return {"items": [{"name": "web-search-tool", "status": "READY"}]}


def test_list_websearch_gateways_flags_target(monkeypatch):
    _patch_boto3(monkeypatch, FakeAgentCore())
    out = aws_setup.list_websearch_gateways("us-east-1")
    assert out == [
        {
            "id": "gw-1",
            "name": "noosphere-websearch",
            "status": "READY",
            "url": "https://gw-1.gateway.example/mcp",
            "web_search": True,
        }
    ]


def test_gateway_template_exists_in_dev_checkout():
    path = aws_setup.gateway_template()
    assert path.name == "gateway.yaml"
    assert path.is_file()
    body = path.read_text()
    assert "AWS::BedrockAgentCore::Gateway" in body
    assert "QueryRunner" not in body  # spike Lambda must not ride along


@pytest.mark.asyncio
async def test_gateway_create_single_flight(monkeypatch):
    creator = aws_setup.GatewayCreate()
    monkeypatch.setattr(
        aws_setup.GatewayCreate, "_create", lambda self, region: "https://gw.example/mcp"
    )
    assert creator.start("us-east-1") is True
    assert creator.start("us-east-1") is False  # second start refused while running
    while creator.status == "creating":
        import asyncio

        await asyncio.sleep(0.01)
    assert creator.status == "done"
    assert creator.snapshot()["gateway_url"] == "https://gw.example/mcp"
