"""Client for invoking the headless Lambda worker."""
from __future__ import annotations

import json
from typing import Any

import boto3

from crawler.config import load_settings


def invoke_headless(url: str, *, persist: bool = False) -> dict[str, Any]:
    """Synchronously invoke the headless Lambda and return the deserialized result."""
    s = load_settings()
    if not s.headless_function_name:
        raise RuntimeError("HEADLESS_FUNCTION_NAME not configured")
    client = boto3.client("lambda")
    response = client.invoke(
        FunctionName=s.headless_function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({"url": url, "persist": persist}).encode("utf-8"),
    )
    payload = response["Payload"].read()
    return json.loads(payload)
