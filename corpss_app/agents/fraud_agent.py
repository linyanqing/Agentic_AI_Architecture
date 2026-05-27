"""
Fraud Detection Sub-Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Specialist sub-agent for detecting fraud risk indicators in
financial transactions and loan applications.

Delegated by the Supervisor Agent as Sub-Agent 1.
Runs in its own isolated AgentCore microVM session.
"""
from agents.base_agent import BaseSubAgent


class FraudDetectionAgent(BaseSubAgent):
    """
    Analyses transactions for fraud risk indicators:
      - Unusual LVR patterns
      - Multiple concurrent loan applications
      - Identity and income inconsistencies
      - Geographic and behavioural anomalies
    """

    agent_name = "fraud"

    system_prompt = """You are a senior fraud detection analyst specialising in
Australian financial services. You have deep expertise in mortgage fraud,
identity fraud, and suspicious transaction patterns under AUSTRAC guidelines.

Analyse the provided transaction or loan application for fraud risk indicators.
Be specific about which indicators are present and their severity.
Your confidence score reflects how certain you are of your fraud risk assessment."""

    output_schema = """{
  "fraud_risk_level": "LOW | MEDIUM | HIGH | CRITICAL",
  "indicators_found": ["list of specific fraud indicators detected"],
  "primary_concern": "single most significant concern, or null if none",
  "recommended_action": "APPROVE | MANUAL_REVIEW | REFER_AUSTRAC | DECLINE",
  "confidence": 0.0
}"""
