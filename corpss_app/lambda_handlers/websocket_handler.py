"""
GENPERF — Lambda Entry-Point: API Gateway WebSocket Handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Route: $default (all message events after $connect)

Deploy this function behind an API Gateway WebSocket API.
The client connects, sends {"prompt": "..."}, and receives
a stream of {"token": "..."} frames followed by {"done": true}.
"""
import json
import sys
import os

# Allow imports from the parent package when deployed as a Lambda layer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pillars.genperf import GENPERFStreamHandler

_handler = GENPERFStreamHandler()


def lambda_handler(event: dict, context) -> dict:
    """AWS Lambda entry-point for the WebSocket $default route."""
    req_ctx       = event["requestContext"]
    connection_id = req_ctx["connectionId"]
    domain_name   = req_ctx["domainName"]
    stage         = req_ctx["stage"]

    body        = json.loads(event.get("body") or "{}")
    user_prompt = body.get("prompt", "")

    if not user_prompt:
        return {"statusCode": 400, "body": "Missing 'prompt' in request body."}

    _handler.stream_to_websocket(
        connection_id=connection_id,
        domain_name=domain_name,
        stage=stage,
        user_prompt=user_prompt,
    )

    return {"statusCode": 200, "body": "Stream complete."}
