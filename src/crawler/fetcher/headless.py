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
    # When the headless handler raises, the Lambda control plane returns a
    # 200 with `FunctionError` set and an error-shaped payload (errorType /
    # errorMessage / stackTrace), NOT an ExtractResult. Surface that as an
    # exception so callers don't blindly treat the error payload as success.
    if response.get("FunctionError"):
        excerpt = payload.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(
            f"headless Lambda errored ({response['FunctionError']}): {excerpt}"
        )
    return json.loads(payload)
