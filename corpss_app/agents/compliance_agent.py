"""
Compliance Sub-Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Specialist sub-agent for regulatory compliance checks in
Australian financial services loan applications.

Delegated by the Supervisor Agent as Sub-Agent 2.
Runs in its own isolated AgentCore microVM session.
"""
from agents.base_agent import BaseSubAgent


class ComplianceAgent(BaseSubAgent):
    """
    Checks loan applications against regulatory requirements:
      - NCCP Act (National Consumer Credit Protection)
      - Responsible lending obligations
      - Anti-Money Laundering / Counter-Terrorism Financing (AML/CTF)
      - APRA prudential standards (APS 220, APG 223)
      - Privacy Act and CDR (Consumer Data Right) obligations
    """

    agent_name = "compliance"

    system_prompt = """You are a senior regulatory compliance officer specialising in
Australian financial services law. You have deep expertise in the National Consumer
Credit Protection (NCCP) Act, APRA prudential standards, AML/CTF obligations,
and ASIC responsible lending guidelines.

Assess the provided loan application for regulatory compliance gaps.
Identify specific legislative or regulatory requirements that may be breached.
Your confidence score reflects how certain you are of your compliance assessment."""

    output_schema = """{
  "compliance_status": "COMPLIANT | MINOR_GAPS | MAJOR_GAPS | NON_COMPLIANT",
  "regulatory_breaches": ["list of specific regulations at risk of breach"],
  "missing_documentation": ["list of required docs not provided"],
  "responsible_lending_flag": true,
  "aml_ctf_flag": false,
  "recommended_action": "APPROVE | ADDITIONAL_DOCS | COMPLIANCE_REVIEW | DECLINE",
  "confidence": 0.0
}"""
