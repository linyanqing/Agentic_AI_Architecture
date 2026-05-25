"""
C · GENCOST — Cost Optimisation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy:
  • 1 % CloudWatch trace-indexing to suppress runaway logging costs.
  • Shift non-time-sensitive workloads to Bedrock Batch Inference
    (flat 50 % discount vs On-Demand per-token pricing).
"""
import logging
import boto3

from config import (
    AWS_REGION,
    BATCH_ROLE_ARN,
    BATCH_INPUT_S3,
    BATCH_OUTPUT_S3,
    BATCH_MODEL_ID,
)

logger = logging.getLogger(__name__)


class GENCOSTBatchProcessor:
    """Submits nightly compliance payloads as asynchronous Bedrock Batch jobs."""

    def __init__(self) -> None:
        self._bedrock = boto3.client("bedrock", region_name=AWS_REGION)

    # ── Public API ────────────────────────────────────────────────────────────

    def submit_batch_job(
        self,
        job_name: str = "Nightly_Compliance_Bulk_Audit",
        input_s3: str = BATCH_INPUT_S3,
        output_s3: str = BATCH_OUTPUT_S3,
    ) -> str:
        """
        Kick off a Bedrock Model Invocation Batch job.

        Returns the JobArn so the caller can poll status.
        50 % cheaper than synchronous On-Demand calls — ideal for
        bulk loan audits, nightly compliance sweeps, etc.
        """
        logger.info("[GENCOST] Submitting async batch job: %s", job_name)

        response = self._bedrock.create_model_invocation_job(
            jobName=job_name,
            modelId=BATCH_MODEL_ID,
            roleArn=BATCH_ROLE_ARN,
            inputDataConfig={"s3InputUri": input_s3},
            outputDataConfig={"s3OutputUri": output_s3},
        )

        job_arn = response["jobArn"]
        logger.info("[GENCOST] ✅ Batch job created. ARN: %s", job_arn)
        return job_arn

    def get_job_status(self, job_arn: str) -> dict:
        """Poll the status of a running batch job."""
        response = self._bedrock.get_model_invocation_job(jobIdentifier=job_arn)
        status = response.get("status", "UNKNOWN")
        logger.info("[GENCOST] Batch job status: %s", status)
        return {"jobArn": job_arn, "status": status}
