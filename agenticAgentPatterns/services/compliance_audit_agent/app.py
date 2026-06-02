#!/usr/bin/env python3
"""
Compliance Audit Agent — CLI Entry Point
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Demonstrates the full Hybrid State-Steering lifecycle:

  Phase 1  — Graph runs: supervisor detects missing S3 encryption → devops
  Phase 2  — Terraform agent patches the HCL → routes back to supervisor
  Phase 3  — Supervisor re-audits patched code → compliant → human_gate
  Phase 4  — Graph pauses at human_gatekeeper (LangGraph interrupt())
  Phase 5  — CLI simulates operator approval via Command(resume={...})
  Phase 6  — Graph resumes, human gate records decision → END

Usage:
  python app.py             # Live Bedrock (rackspace-sydney profile)
  python app.py --mock      # Fully offline — deterministic mock responses
  MOCK_MODE=true python app.py
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import boto3
from botocore.exceptions import NoCredentialsError, ProfileNotFound
from langgraph.types import Command

# Ensure the service package is on the path when run directly
sys.path.insert(0, os.path.dirname(__file__))

from agent import AuditState, build_graph
from config import AWS_PROFILE, AWS_REGION, MOCK_MODE

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-22s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
for _noisy in ("boto3", "botocore", "urllib3", "s3transfer", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("app")


# ═════════════════════════════════════════════════════════════════════════════
#  SAMPLE NON-COMPLIANT TERRAFORM PAYLOAD
#  Represents a realistic infrastructure-as-code file submitted for audit.
#  Violations:
#    - Missing aws_s3_bucket_server_side_encryption_configuration
#      (APRA CPS 234 §36 / SOC2 CC6.1 / CIS 2.1.1)
# ═════════════════════════════════════════════════════════════════════════════

MOCK_NON_COMPLIANT_TERRAFORM = """\
# ──────────────────────────────────────────────────────────────
#  Audit Log S3 Infrastructure — NON-COMPLIANT
#  Team: Platform Engineering  |  Env: production
#  Submitted for CORPSEE compliance review — ap-southeast-2
# ──────────────────────────────────────────────────────────────

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "ap-southeast-2"
}

# ── S3 Bucket ──────────────────────────────────────────────────
resource "aws_s3_bucket" "audit_logs" {
  bucket = "corp-audit-logs-prod-ap-southeast-2"

  tags = {
    Environment = "production"
    Owner       = "platform-engineering"
    CostCentre  = "INFRA-001"
    Compliance  = "SOC2"
  }
}

# ── Versioning ─────────────────────────────────────────────────
resource "aws_s3_bucket_versioning" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ── Block public access ────────────────────────────────────────
resource "aws_s3_bucket_public_access_block" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Lifecycle policy ───────────────────────────────────────────
resource "aws_s3_bucket_lifecycle_configuration" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id

  rule {
    id     = "archive-after-90d"
    status = "Enabled"

    transition {
      days          = 90
      storage_class = "GLACIER"
    }

    expiration {
      days = 2555  # 7 years — APRA record retention
    }
  }
}

# ──────────────────────────────────────────────────────────────
#  COMPLIANCE VIOLATION ▼
#  Missing: aws_s3_bucket_server_side_encryption_configuration
#  Required by: APRA CPS 234 §36, SOC2 CC6.1, CIS AWS 2.1.1
#  All data in this bucket is UNENCRYPTED AT REST.
# ──────────────────────────────────────────────────────────────
"""


# ═════════════════════════════════════════════════════════════════════════════
#  PRINT HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _banner(title: str, icon: str = "▶", width: int = 64) -> None:
    bar = "═" * width
    print(f"\n{bar}\n  {icon}  {title}\n{bar}")


def _section(title: str, width: int = 56) -> None:
    print(f"\n  {'─' * width}")
    print(f"  {title}")
    print(f"  {'─' * width}")


def _print_state_snapshot(label: str, state: dict) -> None:
    """Print a concise state snapshot for runtime tracing."""
    _section(f"📋 STATE SNAPSHOT — {label}")
    print(f"  next_agent       : {state.get('next_agent', '—')}")
    print(f"  iteration_count  : {state.get('iteration_count', 0)}")
    print(f"  is_approved      : {state.get('is_approved', False)}")
    meta = state.get("audit_metadata", {})
    print(f"  audit_pass       : {meta.get('audit_pass', '—')}")
    print(f"  issue_count      : {meta.get('issue_count', '—')}")
    print(f"  severity         : {meta.get('severity', '—')}")
    print(f"  patched_resources: {meta.get('patched_resources', [])}")
    msgs = state.get("messages", [])
    print(f"  message_count    : {len(msgs)}")


def _print_audit_trail(messages: list[dict]) -> None:
    """Print the full agent message audit trail."""
    _section("📜 AUDIT TRAIL")
    for i, msg in enumerate(messages, 1):
        role    = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        print(f"\n  [{i}] {role}")
        for line in str(content).splitlines()[:12]:
            print(f"      {line}")
        if len(str(content).splitlines()) > 12:
            print(f"      … ({len(str(content).splitlines()) - 12} more lines)")


def _print_interrupt_payload(snapshot) -> None:
    """Extract and display the interrupt review payload from the graph snapshot."""
    for task in getattr(snapshot, "tasks", []):
        for intr in getattr(task, "interrupts", []):
            payload = getattr(intr, "value", {})
            _section("⏸️  INTERRUPT PAYLOAD — Human Review Required")
            print(f"  checkpoint_type : {payload.get('checkpoint_type')}")
            print(f"  audit_pass      : {payload.get('audit_pass')}")
            print(f"  iteration_count : {payload.get('iteration_count')}")
            print(f"  patched_resources: {payload.get('patched_resources', [])}")
            print(f"\n  Compliance Report:")
            for line in str(payload.get("compliance_report", "")).splitlines():
                print(f"    {line}")
            print(f"\n  Instructions: {payload.get('instructions', '')}")


# ═════════════════════════════════════════════════════════════════════════════
#  BEDROCK CLIENT FACTORY
# ═════════════════════════════════════════════════════════════════════════════

def _build_bedrock_client(mock: bool):
    """
    Build a boto3 Bedrock runtime client using the configured AWS profile.

    Returns None if credentials/profile are unavailable — nodes will fall
    back to mock responses automatically in that case.
    """
    if mock:
        logger.info("[APP] Mock mode — Bedrock client not instantiated")
        return None
    try:
        session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        client  = session.client("bedrock-runtime")
        logger.info("[APP] Bedrock client ready — profile=%s region=%s", AWS_PROFILE, AWS_REGION)
        return client
    except (ProfileNotFound, NoCredentialsError) as exc:
        logger.warning("[APP] AWS credentials unavailable (%s) — using mock responses", exc)
        return None


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN LIFECYCLE
# ═════════════════════════════════════════════════════════════════════════════

def main(use_mock: bool = False) -> None:
    """
    Full compliance audit lifecycle:

    Phase 1  Initial graph run — supervisor → terraform → supervisor → human gate
    Phase 4  Graph pauses at interrupt() in human_gatekeeper_node
    Phase 5  CLI simulates operator review and approval
    Phase 6  Graph resumes via Command(resume=...) and terminates
    """
    effective_mock = use_mock or MOCK_MODE

    _banner(
        "COMPLIANCE AUDIT AGENT — Hybrid State-Steering Demo",
        icon="🔍",
    )
    print(f"\n  Mode    : {'🔧 MOCK (offline)' if effective_mock else '☁️  LIVE (Amazon Bedrock)'}")
    print(f"  Profile : {AWS_PROFILE}")
    print(f"  Region  : {AWS_REGION}")

    # ── Phase 0: Setup ────────────────────────────────────────────────────────
    bedrock_client = _build_bedrock_client(mock=effective_mock)
    graph          = build_graph(bedrock_client)

    thread_config = {"configurable": {"thread_id": "audit-demo-001"}}

    initial_state: AuditState = {
        "messages":          [{"role": "user", "content": "Audit Terraform for APRA CPS 234 compliance."}],
        "terraform_code":    MOCK_NON_COMPLIANT_TERRAFORM,
        "compliance_report": "",
        "is_approved":       False,
        "next_agent":        "",
        "iteration_count":   0,
        "audit_metadata":    {},
    }

    # ── Phase 1: Show the non-compliant input ─────────────────────────────────
    _banner("Phase 1 · Input — Non-Compliant Terraform Payload", icon="📄")
    print(f"\n  Lines          : {len(MOCK_NON_COMPLIANT_TERRAFORM.splitlines())}")
    print(f"  Known violation: Missing S3 server-side encryption")
    print(f"  Regulations    : APRA CPS 234 §36 | SOC2 CC6.1 | CIS AWS 2.1.1")

    # ── Phase 2: First graph invocation ──────────────────────────────────────
    #  Runs: supervisor → terraform_agent → supervisor → human_gatekeeper
    #  Pauses when human_gatekeeper_node calls interrupt()
    _banner("Phase 2 · Graph Execution — Supervisor + Terraform + Re-Audit", icon="🔄")
    print("\n  Running graph … (supervisor audit → devops patch → re-audit → human gate)\n")

    t0           = time.time()
    state_at_pause = graph.invoke(initial_state, config=thread_config)
    elapsed_ms   = (time.time() - t0) * 1000

    print(f"\n  Graph paused in {elapsed_ms:.0f}ms")
    _print_state_snapshot("After First Invocation", state_at_pause)

    # ── Phase 3: Check for human gate interrupt ───────────────────────────────
    _banner("Phase 3 · Human Gate — Graph Paused for Operator Review", icon="⏸️ ")

    snapshot = graph.get_state(thread_config)

    if snapshot.next:
        print(f"\n  ✅  Graph interrupted at node(s): {snapshot.next}")
        _print_interrupt_payload(snapshot)

        # ── Phase 4: Simulate operator review ────────────────────────────────
        _banner("Phase 4 · Operator Review — Simulating Human Approval", icon="👤")
        print("\n  Operator is reviewing the compliance report …")
        time.sleep(1)   # simulate review time
        print("  Decision: APPROVED ✅")
        print("  Reason  : All critical violations remediated by DevOps agent.")
        print("  Action  : Resuming graph with Command(resume={'approved': True})")

        # ── Phase 5: Resume graph with human decision ─────────────────────────
        _banner("Phase 5 · Graph Resume — Final State", icon="▶️ ")
        t1         = time.time()
        final_state = graph.invoke(
            Command(resume={"approved": True}),
            config=thread_config,
        )
        resume_ms = (time.time() - t1) * 1000
        print(f"\n  Graph completed in {resume_ms:.0f}ms after resume")

    else:
        # Graph completed without interrupt (e.g. GENREL loop guard triggered)
        _banner("Phase 3 · Graph Completed Without Human Gate", icon="✅")
        print("\n  Graph reached END without interrupt (possible loop guard).")
        final_state = state_at_pause

    # ── Phase 6: Final output ─────────────────────────────────────────────────
    _banner("Phase 6 · Final Outcome", icon="🏁")
    _print_state_snapshot("Final State", final_state)

    print(f"\n  ┌──────────────────────────────────────────────────────┐")
    print(f"  │  COMPLIANCE DECISION                                  │")
    print(f"  │  is_approved    : {str(final_state.get('is_approved', False)):<35}│")
    print(f"  │  iteration_count: {final_state.get('iteration_count', 0):<35}│")
    meta = final_state.get("audit_metadata", {})
    print(f"  │  final severity : {meta.get('severity', 'N/A'):<35}│")
    print(f"  │  issues found   : {meta.get('issue_count', 'N/A'):<35}│")
    patched = meta.get("patched_resources", [])
    for i, r in enumerate(patched):
        label = "patched        :" if i == 0 else "               :"
        print(f"  │  {label} {r:<33}│")
    print(f"  └──────────────────────────────────────────────────────┘")

    # ── Full audit trail ──────────────────────────────────────────────────────
    _print_audit_trail(final_state.get("messages", []))

    _banner("Demo Complete", icon="✅")
    print()


# ═════════════════════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compliance Audit Agent — Hybrid State-Steering Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python app.py              # Live Bedrock (rackspace-sydney profile)\n"
            "  python app.py --mock       # Offline — deterministic mock responses\n"
            "  MOCK_MODE=true python app.py\n"
        ),
    )
    parser.add_argument(
        "--mock", "-m",
        action="store_true",
        help="Run in mock mode (no AWS calls — deterministic canned responses)",
    )
    args = parser.parse_args()

    try:
        main(use_mock=args.mock)
    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.\n")
        sys.exit(0)
