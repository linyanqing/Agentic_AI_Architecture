"""
P · GENPERF — Performance Efficiency
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy:
  • Eliminate synchronous request-response bottlenecks with
    API Gateway WebSocket bi-directional token streaming.
  • Use Bedrock Provisioned Throughput (dedicated MU) to guarantee
    a latency SLA and eliminate "noisy-neighbour" region surges.
  • Sub-200 ms time-to-first-token perceived by the end user.

Note: This module runs as a Lambda handler behind an API Gateway
      WebSocket route ($default).  See lambda_handlers/websocket_handler.py
      for the full Lambda entry-point wrapper.
"""
import json
import logging
import boto3

from config import AWS_REGION, PROVISIONED_PT_ARN

logger = logging.getLogger(__name__)


class GENPERFStreamHandler:
    """
    Streams Bedrock inference tokens directly to a connected WebSocket client.
    Each token chunk is pushed as soon as it arrives — no buffering.
    """

    def __init__(self, provisioned_arn: str = PROVISIONED_PT_ARN) -> None:
        self._model_arn    = provisioned_arn
        self._bedrock_rt   = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    # ── Public API ────────────────────────────────────────────────────────────

    def stream_to_websocket(
        self,
        connection_id: str,
        domain_name: str,
        stage: str,
        user_prompt: str,
    ) -> int:
        """
        Stream inference tokens to a live WebSocket connection.

        Returns the total number of tokens pushed.
        """
        gateway_api = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=f"https://{domain_name}/{stage}",
            region_name=AWS_REGION,
        )

        logger.info(
            "[GENPERF] Opening stream to connection=%s via Provisioned PT",
            connection_id,
        )

        stream_response = self._bedrock_rt.converse_stream(
            modelId=self._model_arn,
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        )

        token_count = 0
        for chunk in stream_response["stream"]:
            if "contentBlockDelta" in chunk:
                live_token = chunk["contentBlockDelta"]["delta"]["text"]
                gateway_api.post_to_connection(
                    ConnectionId=connection_id,
                    Data=json.dumps({"token": live_token}),
                )
                token_count += 1

        # Signal end-of-stream to the client
        gateway_api.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps({"done": True, "total_tokens": token_count}),
        )

        logger.info("[GENPERF] ✅ Stream complete. Tokens pushed: %d", token_count)
        return token_count

    def converse_sync(self, user_prompt: str) -> str:
        """Synchronous fallback when a WebSocket connection is not available."""
        response = self._bedrock_rt.converse(
            modelId=self._model_arn,
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        )
        return response["output"]["message"]["content"][0]["text"]
