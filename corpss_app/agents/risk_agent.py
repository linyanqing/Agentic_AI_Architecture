"""
Risk Scoring Sub-Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Specialist sub-agent for credit risk scoring and serviceability
assessment of mortgage loan applications.

Delegated by the Supervisor Agent as Sub-Agent 3.
Runs in its own isolated AgentCore microVM session.
"""
from agents.base_agent import BaseSubAgent


class RiskScoringAgent(BaseSubAgent):
    """
    Scores credit risk and loan serviceability:
      - Debt-to-income ratio (DTI) analysis
      - Loan-to-value ratio (LVR) stress testing
      - Employment stability and income verification
      - Repayment capacity under rate stress scenarios (+3%)
      - Portfolio concentration risk
    """

    agent_name = "risk"

    system_prompt = """You are a senior credit risk analyst specialising in Australian
residential and commercial mortgage lending. You apply APRA's APS 220 Credit Risk
Management guidelines and conduct rigorous serviceability assessments using the
Australian Prudential standard 3% interest rate stress buffer.

Evaluate the provided loan application for credit risk and serviceability.
Calculate key risk ratios (DTI, LVR) and stress-test repayment capacity.
Your confidence score reflects how certain you are of your risk assessment."""

    output_schema = """{
  "credit_risk_rating": "AAA | AA | A | BBB | BB | B | CCC | D",
  "risk_score": 0,
  "lvr": 0.0,
  "dti_ratio": 0.0,
  "serviceability_buffer_pass": true,
  "risk_factors": ["list of identified risk factors"],
  "mitigants": ["list of risk mitigants present"],
  "recommended_action": "APPROVE | APPROVE_WITH_CONDITIONS | REFER | DECLINE",
  "max_loan_amount": 0,
  "confidence": 0.0
}"""
