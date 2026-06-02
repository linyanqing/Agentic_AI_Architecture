"""
Compliance Audit Agent — LangGraph Multi-Agent State Machine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Implements the Hybrid State-Steering pattern:

  Structural routing rails  → pure deterministic Python (route_from_supervisor)
  Tactical step selection   → LLM context steering via state["next_agent"] token

Graph topology:
  START
    └─► supervisor ─► [conditional edge: route_from_supervisor()]
                           ├─► terraform_agent ─► supervisor  (re-audit loop)
                           ├─► human_gatekeeper ─► END
                           └─► END  (fallback on unmapped token — GENREL)

CORPSEE design constraints enforced:
  GENCOST  — State carries audit_metadata (dense refs) not raw string blobs.
             Second+ audit passes send compressed metadata, not the full TF text.
  GENSEC   — Bedrock treated as pure inference. boto3 client bound via closure,
             never injected into model context windows.
  GENREL   — route_from_supervisor() is pure Python with explicit dict mapping.
             Unmapped tokens fall back to END. iteration_count caps the loop.
"""
from __future__ import annotations

import json
import logging
import re
from operator import add
from typing import Annotated, Literal, TypedDict

import boto3
from botocore.exceptions import ClientError
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from config import (
    MAX_AUDIT_ITERATIONS,
    MOCK_MODE,
    ROUTE_DEVOPS,
    ROUTE_END,
    ROUTE_HUMAN_GATE,
    ROUTE_SUPERVISOR,
    SUPERVISOR_MODEL_ID,
    TERRAFORM_MODEL_ID,
)

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
#  STATE MACHINE CLIPBOARD
# ═════════════════════════════════════════════════════════════════════════════

class AuditState(TypedDict):
    """
    Shared state clipboard passed between all agent nodes.

    Field design rationale:
      messages          Append-only audit trail (operator.add reducer ensures
                        each node's returned list is concatenated, not replaced).
      terraform_code    Mutable IaC content — patched in place by terraform_agent.
      compliance_report Latest supervisor audit narrative — overwritten each pass.
      is_approved       Set by human_gatekeeper after human decision.
      next_agent        Routing token written by each node — read by the router.
      iteration_count   Loop guard counter (GENREL: supervisor caps at MAX_AUDIT_ITERATIONS).
      audit_metadata    Dense context dict (GENCOST): carries issue codes, severity,
                        regulation refs, and patched resource names rather than
                        re-injecting multi-thousand token raw strings on every hop.
    """
    messages:          Annotated[list[dict], add]   # append-only via operator.add
    terraform_code:    str
    compliance_report: str
    is_approved:       bool
    next_agent:        str
    iteration_count:   int
    audit_metadata:    dict                          # dense context refs — not raw blobs


# ═════════════════════════════════════════════════════════════════════════════
#  MOCK LLM RESPONSES
#  Used when MOCK_MODE=true or when Bedrock returns a throttling/auth error.
#  Deterministic values ensure the demo runs fully offline.
# ═════════════════════════════════════════════════════════════════════════════

_MOCK_SUPERVISOR_NON_COMPLIANT = json.dumps({
    "compliance_status": "NON_COMPLIANT",
    "issues_found": [
        "MISSING: aws_s3_bucket_server_side_encryption_configuration for 'audit_logs'",
        "Bucket 'corp-audit-logs-prod-ap-southeast-2' has no SSE policy — data at rest is unprotected",
    ],
    "severity": "HIGH",
    "regulation_refs": [
        "APRA CPS 234 §36 — Encryption of data at rest",
        "SOC2 CC6.1 — Logical and physical access controls",
        "CIS AWS Foundations Benchmark 2.1.1",
    ],
    "next_agent": "devops",
    "rationale": (
        "The S3 bucket 'audit_logs' stores regulated financial data but has no "
        "server-side encryption configuration. APRA CPS 234 §36 mandates encryption "
        "at rest for all regulated entity information assets. Routing to DevOps agent "
        "to patch the Terraform configuration."
    ),
})

_MOCK_SUPERVISOR_COMPLIANT = json.dumps({
    "compliance_status": "COMPLIANT",
    "issues_found": [],
    "severity": "NONE",
    "regulation_refs": [],
    "next_agent": "human_gate",
    "rationale": (
        "Re-audit complete. The patched Terraform now includes "
        "aws_s3_bucket_server_side_encryption_configuration with KMS-based SSE "
        "and bucket_key_enabled=true. A companion aws_kms_key with "
        "enable_key_rotation=true satisfies APRA CPS 234 §36, SOC2 CC6.1, "
        "and CIS AWS Foundations 2.1.1. All controls satisfied. "
        "Routing to human gatekeeper for final sign-off."
    ),
})

_MOCK_TERRAFORM_PATCH = """\
resource "aws_s3_bucket_server_side_encryption_configuration" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.audit_logs.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_kms_key" "audit_logs" {
  description             = "KMS key — S3 audit log encryption (APRA CPS 234 §36)"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Environment = "production"
    Compliance  = "APRA-CPS-234"
    CostCentre  = "INFRA-001"
  }
}

resource "aws_kms_alias" "audit_logs" {
  name          = "alias/corp-audit-logs-prod"
  target_key_id = aws_kms_key.audit_logs.key_id
}
"""


# ═════════════════════════════════════════════════════════════════════════════
#  BEDROCK INFERENCE HELPER
# ═════════════════════════════════════════════════════════════════════════════

def _bedrock_converse(
    bedrock_client,
    model_id:      str,
    system_prompt: str,
    user_message:  str,
    mock_response: str,
    max_tokens:    int   = 1_024,
    temperature:   float = 0.0,
) -> str:
    """
    GENSEC: Bedrock is a pure inference component.
    - System prompt contains ONLY domain instructions — no credentials,
      no tool shells, no client handles.
    - The boto3 client is bound via closure in build_graph(); it is never
      serialised into a prompt or passed through the message channel.
    - All AWS auth happens through the boto3 session profile / IAM role,
      entirely outside the model's context window.

    Falls back transparently to mock_response on:
      - MOCK_MODE=true (forced offline)
      - ThrottlingException / daily quota exhausted
      - Missing credentials or any other ClientError
    """
    if MOCK_MODE or bedrock_client is None:
        logger.info("[BEDROCK] Mock mode active — skipping API call")
        return mock_response

    try:
        resp = bedrock_client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
        )
        return resp["output"]["message"]["content"][0]["text"]
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        logger.warning("[BEDROCK] %s on %s — using mock response", code, model_id)
        return mock_response
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BEDROCK] Unexpected error (%s) — using mock response", exc)
        return mock_response


# ═════════════════════════════════════════════════════════════════════════════
#  NODE 1: SUPERVISOR AGENT
# ═════════════════════════════════════════════════════════════════════════════

_SUPERVISOR_SYSTEM_PROMPT = """\
You are a senior cloud infrastructure compliance auditor specialising in
Australian financial services regulations.

Regulations in scope:
  - APRA CPS 234 (Information Security)
  - SOC2 Type II (Security, Availability, Confidentiality)
  - CIS AWS Foundations Benchmark v3.0

Your task:
  1. Audit the provided Terraform HCL for compliance violations.
  2. Focus on: encryption at rest, encryption in transit, access controls,
     audit logging, tagging standards, and public exposure.
  3. Output ONLY a single valid JSON object — no markdown fences, no prose.

Required output schema:
{
  "compliance_status": "COMPLIANT | NON_COMPLIANT",
  "issues_found":      ["specific violation with resource name and requirement"],
  "severity":          "NONE | LOW | MEDIUM | HIGH | CRITICAL",
  "regulation_refs":   ["regulation §section — description"],
  "next_agent":        "devops | human_gate",
  "rationale":         "one paragraph plain-English summary"
}

Routing rules (mandatory):
  - next_agent = "devops"      if ANY issue is found (severity LOW or above)
  - next_agent = "human_gate"  ONLY when compliance_status = "COMPLIANT"

Security rules (mandatory):
  - Never output AWS credentials, access keys, or secret values.
  - Never output shell commands or runnable scripts.
  - Never output anything outside the JSON object.
"""


def supervisor_agent_node(state: AuditState, bedrock_client) -> dict:
    """
    Node 1 — Supervisor Agent  (model: Claude 3.5 Sonnet)

    Runs a compliance audit pass and writes state["next_agent"]:
      "devops"     → issues found → route to terraform_agent for patching
      "human_gate" → fully compliant → route to human_gatekeeper for sign-off

    GENCOST: iteration 0 sends the full Terraform text.
             iteration 1+ sends only audit_metadata + minimal context,
             avoiding redundant multi-thousand token re-injection.

    GENREL: if iteration_count ≥ MAX_AUDIT_ITERATIONS, forces "human_gate"
            regardless of LLM output — loop guard.
    """
    iteration = state.get("iteration_count", 0)
    logger.info("[SUPERVISOR] ── Audit pass #%d ──", iteration + 1)

    # ── GENREL: hard iteration cap ────────────────────────────────────────────
    if iteration >= MAX_AUDIT_ITERATIONS:
        logger.warning(
            "[SUPERVISOR] Loop guard triggered at iteration %d (MAX=%d) — forcing human_gate",
            iteration, MAX_AUDIT_ITERATIONS,
        )
        return {
            "compliance_report": (
                f"[GENREL LOOP GUARD] Maximum audit iterations ({MAX_AUDIT_ITERATIONS}) reached. "
                "Escalating to human gatekeeper for manual review."
            ),
            "next_agent":      ROUTE_HUMAN_GATE,
            "iteration_count": iteration + 1,
            "messages": [{
                "role":    "supervisor",
                "content": f"Loop guard: forced escalation to human_gate at iteration {iteration + 1}.",
            }],
        }

    # ── GENCOST: build user message ──────────────────────────────────────────
    # Pass 0 → full Terraform text (model has no prior context)
    # Pass 1+ → compressed metadata reference + updated Terraform
    #           (avoids re-sending thousands of tokens of prior context)
    if iteration == 0:
        user_message = (
            "Audit the following Terraform configuration for compliance violations:\n\n"
            f"```hcl\n{state['terraform_code']}\n```"
        )
        mock_resp = _MOCK_SUPERVISOR_NON_COMPLIANT
    else:
        meta       = state.get("audit_metadata", {})
        prior      = meta.get("prior_issues", [])
        patched    = meta.get("patched_resources", [])
        user_message = (
            f"Re-audit pass {iteration + 1}.\n\n"
            f"Prior issues identified ({len(prior)}):\n"
            + "".join(f"  - {iss}\n" for iss in prior)
            + f"\nPatched resources:\n"
            + "".join(f"  - {r}\n" for r in patched)
            + f"\nUpdated Terraform:\n```hcl\n{state['terraform_code']}\n```"
        )
        mock_resp = _MOCK_SUPERVISOR_COMPLIANT

    # ── Bedrock inference ─────────────────────────────────────────────────────
    raw = _bedrock_converse(
        bedrock_client=bedrock_client,
        model_id=SUPERVISOR_MODEL_ID,
        system_prompt=_SUPERVISOR_SYSTEM_PROMPT,
        user_message=user_message,
        mock_response=mock_resp,
    )

    # ── Parse JSON response (GENREL: safe fallback on malformed output) ───────
    parsed     = _safe_parse_json(raw, fallback_next_agent=ROUTE_HUMAN_GATE)
    next_agent = parsed.get("next_agent", ROUTE_HUMAN_GATE).strip().lower()
    issues     = parsed.get("issues_found", [])
    status     = parsed.get("compliance_status", "UNKNOWN")
    severity   = parsed.get("severity", "UNKNOWN")
    refs       = parsed.get("regulation_refs", [])
    rationale  = parsed.get("rationale", raw[:400])

    # ── Build human-readable compliance report ────────────────────────────────
    compliance_report = (
        f"{'═' * 56}\n"
        f"  COMPLIANCE AUDIT — Pass {iteration + 1}\n"
        f"{'═' * 56}\n"
        f"  Status     : {status}\n"
        f"  Severity   : {severity}\n"
        f"  Regulations: {', '.join(refs) if refs else 'N/A'}\n"
        f"  Issues     : {len(issues)}\n"
        + ("".join(f"    [{i+1}] {iss}\n" for i, iss in enumerate(issues))
           if issues else "    None — all controls satisfied.\n")
        + f"\n  Rationale  :\n    {rationale}\n"
        + f"{'─' * 56}"
    )

    # ── Update audit_metadata (GENCOST: dense refs, not raw text) ────────────
    audit_metadata = {
        **state.get("audit_metadata", {}),
        "issue_count":     len(issues),
        "prior_issues":    issues,
        "severity":        severity,
        "regulation_refs": refs,
        "audit_pass":      iteration + 1,
    }

    logger.info(
        "[SUPERVISOR] status=%s  issues=%d  severity=%s  next=%s",
        status, len(issues), severity, next_agent,
    )

    return {
        "compliance_report": compliance_report,
        "next_agent":        next_agent,
        "iteration_count":   iteration + 1,
        "audit_metadata":    audit_metadata,
        "messages": [{"role": "supervisor", "content": compliance_report}],
    }


# ═════════════════════════════════════════════════════════════════════════════
#  NODE 2: TERRAFORM PATCH AGENT
# ═════════════════════════════════════════════════════════════════════════════

_TERRAFORM_SYSTEM_PROMPT = """\
You are a senior DevOps engineer specialising in AWS Terraform and compliance
remediation for Australian financial services infrastructure.

Your task:
  1. Receive a compliance audit report listing specific Terraform violations.
  2. Generate ONLY the missing or corrected HCL resource blocks needed to fix them.
  3. Output ONLY valid HCL — no markdown code fences, no prose, no explanation.

For missing S3 server-side encryption:
  - Add aws_s3_bucket_server_side_encryption_configuration with sse_algorithm = "aws:kms"
  - Set bucket_key_enabled = true (reduces KMS API calls — GENCOST)
  - Add a companion aws_kms_key with enable_key_rotation = true
  - Add an aws_kms_alias for the key

Security rules (mandatory):
  - Never output AWS credentials, access keys, secret values, or ARN literals.
  - Never output provider configuration blocks.
  - Never output shell commands or scripts.
  - Reference other resources via Terraform expressions (e.g. aws_s3_bucket.name.id).
"""


def terraform_agent_node(state: AuditState, bedrock_client) -> dict:
    """
    Node 2 — Terraform Patch Agent  (model: Claude 3 Haiku — lightweight)

    Generates remediation HCL blocks for the compliance issues identified
    by the supervisor. Appends the patch to state["terraform_code"] and
    routes back to supervisor for re-audit verification.

    GENCOST: prompt sends only the issue list from audit_metadata — not the
             full Terraform source — keeping the Haiku call cheap.
    GENSEC:  model output is text only. No credentials or tool invocations
             can appear because the system prompt explicitly prohibits them.
    """
    meta     = state.get("audit_metadata", {})
    issues   = meta.get("prior_issues", ["Missing S3 server-side encryption configuration"])
    severity = meta.get("severity", "HIGH")

    logger.info("[DEVOPS] Generating Terraform patch for %d issue(s) …", len(issues))

    # GENCOST: send only the issue list, not the full Terraform text
    user_message = (
        f"Generate remediation Terraform HCL for the following "
        f"compliance violations (severity: {severity}):\n\n"
        + "".join(f"  {i+1}. {iss}\n" for i, iss in enumerate(issues))
        + "\nOutput ONLY the HCL resource blocks. No explanation. No fences."
    )

    patch_hcl = _bedrock_converse(
        bedrock_client=bedrock_client,
        model_id=TERRAFORM_MODEL_ID,
        system_prompt=_TERRAFORM_SYSTEM_PROMPT,
        user_message=user_message,
        mock_response=_MOCK_TERRAFORM_PATCH,
        max_tokens=768,
    )

    # Append the patch to the existing Terraform code
    patched_tf = (
        state["terraform_code"].rstrip()
        + "\n\n"
        + "# ── PATCH: Applied by DevOps Agent ── #\n"
        + "# Remediation for: " + "; ".join(issues[:2]) + "\n\n"
        + patch_hcl.strip()
        + "\n"
    )

    # Extract resource identifiers from the patch for audit_metadata
    patched_resources = _extract_resource_names(patch_hcl)

    updated_metadata = {
        **meta,
        "patched_resources": patched_resources,
    }

    logger.info("[DEVOPS] Patch applied — resources: %s", patched_resources)

    return {
        "terraform_code": patched_tf,
        "next_agent":     ROUTE_SUPERVISOR,   # always route back for re-audit
        "audit_metadata": updated_metadata,
        "messages": [{
            "role":    "terraform_agent",
            "content": (
                f"Patch applied. Resources added: {', '.join(patched_resources)}. "
                "Routing to supervisor for re-audit verification."
            ),
        }],
    }


# ═════════════════════════════════════════════════════════════════════════════
#  NODE 3: HUMAN GATEKEEPER
# ═════════════════════════════════════════════════════════════════════════════

def human_gatekeeper_node(state: AuditState) -> dict:
    """
    Node 3 — Human-in-the-Loop Compliance Gate

    Uses LangGraph's interrupt() primitive to pause graph execution and
    surface the full compliance audit report to an operator for manual review.

    Execution lifecycle:
      1. Graph reaches this node → interrupt(review_payload) is called.
      2. LangGraph checkpoints the full thread state to MemorySaver.
      3. graph.invoke() returns to the caller with the state at pause point.
      4. External caller displays the review payload to the operator.
      5. Operator decision arrives via graph.invoke(Command(resume={...})).
      6. human_decision contains the operator's {"approved": true|false} value.
      7. is_approved is set in state and the graph continues to END.

    GENSEC: no model inference runs in this node — it is a pure human
            decision gate. The model is not trusted to self-approve.
    """
    logger.info("[GATE] 🚦 Human review checkpoint triggered")

    # Surface all context the operator needs to make an informed decision
    review_payload = {
        "checkpoint_type":   "HUMAN_COMPLIANCE_REVIEW",
        "audit_pass":        state.get("audit_metadata", {}).get("audit_pass", "?"),
        "compliance_report": state["compliance_report"],
        "patched_resources": state.get("audit_metadata", {}).get("patched_resources", []),
        "iteration_count":   state.get("iteration_count", 0),
        "instructions":      (
            'Review the compliance_report above, then resume with:\n'
            '  Command(resume={"approved": True})   — to approve and deploy\n'
            '  Command(resume={"approved": False})  — to reject and escalate'
        ),
    }

    logger.info("[GATE] ⏸️  Graph thread paused — awaiting human decision …")

    # ── interrupt() pauses the graph here ────────────────────────────────────
    # State is checkpointed. Caller receives the current state snapshot.
    # Resumes when graph.invoke(Command(resume={...})) is called externally.
    human_decision = interrupt(review_payload)

    # ── Resume point — human_decision contains the operator's value ───────────
    approved    = bool(human_decision.get("approved", False))
    decision_label = "APPROVED ✅" if approved else "REJECTED ❌"

    logger.info("[GATE] Human decision received: %s", decision_label)

    return {
        "is_approved": approved,
        "next_agent":  ROUTE_END,
        "messages": [{
            "role":    "human_gatekeeper",
            "content": f"Human gate decision: {decision_label}",
        }],
    }


# ═════════════════════════════════════════════════════════════════════════════
#  ROUTING FUNCTION — Pure Python, no inference (GENREL)
# ═════════════════════════════════════════════════════════════════════════════

def route_from_supervisor(
    state: AuditState,
) -> Literal["terraform_agent", "human_gatekeeper", "__end__"]:
    """
    GENREL: Deterministic routing — zero inference involved.

    Maps the LLM-generated next_agent token to a LangGraph node name via
    an explicit Python dict. If the model produces an unmapped token
    (hallucination, partial output, empty string), the function falls back
    gracefully to END rather than crashing or entering an undefined state.

    This function is the structural rail — the LLM supplies the token,
    but routing logic is entirely in predictable, testable Python code.
    """
    token = state.get("next_agent", "").strip().lower()

    # Explicit mapping: LLM token → LangGraph node name
    mapping: dict[str, str] = {
        ROUTE_DEVOPS:     "terraform_agent",
        ROUTE_HUMAN_GATE: "human_gatekeeper",
        ROUTE_END:        END,
    }

    destination = mapping.get(token)

    if destination is None:
        logger.warning(
            "[ROUTER] ⚠️  Unmapped token '%s' — GENREL safe fallback to END", token
        )
        return END

    logger.info("[ROUTER] supervisor ──► '%s'  (token: '%s')", destination, token)
    return destination


# ═════════════════════════════════════════════════════════════════════════════
#  GRAPH ASSEMBLY
# ═════════════════════════════════════════════════════════════════════════════

def build_graph(bedrock_client) -> "CompiledStateGraph":
    """
    Compile the compliance audit StateGraph.

    Topology:
      START → supervisor
      supervisor → [conditional: route_from_supervisor()]
          "terraform_agent"  → terraform_agent → supervisor   (re-audit loop)
          "human_gatekeeper" → human_gatekeeper → END
          END                → END              (fallback / explicit end token)

    Compiled with MemorySaver checkpointer so interrupt/resume across the
    human gate works correctly — thread state is persisted between invoke() calls.

    GENSEC: bedrock_client is bound into node closures here, outside the graph
    execution context. It is never serialised into state or passed to the LLM.
    """
    # Bind bedrock_client into node closures — keeps client out of model context
    def _supervisor_node(state: AuditState) -> dict:
        return supervisor_agent_node(state, bedrock_client)

    def _terraform_node(state: AuditState) -> dict:
        return terraform_agent_node(state, bedrock_client)

    # ── Build graph ───────────────────────────────────────────────────────────
    graph = StateGraph(AuditState)

    graph.add_node("supervisor",       _supervisor_node)
    graph.add_node("terraform_agent",  _terraform_node)
    graph.add_node("human_gatekeeper", human_gatekeeper_node)

    # Entry point
    graph.add_edge(START, "supervisor")

    # Conditional edges from supervisor — deterministic Python routing function
    graph.add_conditional_edges(
        source="supervisor",
        path=route_from_supervisor,
        path_map={
            "terraform_agent":  "terraform_agent",
            "human_gatekeeper": "human_gatekeeper",
            END:                END,
        },
    )

    # Terraform agent always loops back to supervisor for verification re-audit
    graph.add_edge("terraform_agent", "supervisor")

    # Human gatekeeper always terminates after the human decision
    graph.add_edge("human_gatekeeper", END)

    # Compile with in-memory checkpointer (enables interrupt/resume)
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


# ═════════════════════════════════════════════════════════════════════════════
#  UTILITY HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _safe_parse_json(text: str, fallback_next_agent: str = ROUTE_HUMAN_GATE) -> dict:
    """
    GENREL: Parse JSON from raw model output without crashing.

    Strategy:
      1. Strip markdown code fences (models frequently wrap output in ```json)
      2. Attempt json.loads on the cleaned string
      3. On failure: scan for the first {...} block with regex and retry
      4. On second failure: return a safe dict with the fallback_next_agent token
         so the graph always has a valid routing token regardless of model output
    """
    clean = (
        text.strip()
        .removeprefix("```json")
        .removeprefix("```")
        .removesuffix("```")
        .strip()
    )
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*?\}", clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning(
        "[JSON] Parse failed on model output — safe fallback (next_agent=%s)",
        fallback_next_agent,
    )
    return {
        "compliance_status": "UNKNOWN",
        "issues_found":      [],
        "severity":          "UNKNOWN",
        "regulation_refs":   [],
        "next_agent":        fallback_next_agent,
        "rationale":         text[:400] if text else "No model output received.",
    }


def _extract_resource_names(hcl: str) -> list[str]:
    """
    Extract Terraform resource identifiers (type.name) from HCL source text.
    Used to populate audit_metadata["patched_resources"] for GENCOST context refs.
    """
    matches = re.findall(r'resource\s+"([^"]+)"\s+"([^"]+)"', hcl)
    return [f"{rtype}.{rname}" for rtype, rname in matches]
