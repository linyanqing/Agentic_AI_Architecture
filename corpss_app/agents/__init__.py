"""
CORPSEE Multi-Agent Package
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Specialist sub-agents for loan application assessment.

Macro Orchestration Pattern:
  SupervisorAgent
    ├── FraudDetectionAgent  (Sub-Agent 1)
    ├── ComplianceAgent      (Sub-Agent 2)
    └── RiskScoringAgent     (Sub-Agent 3)
"""
from agents.base_agent import BaseSubAgent, SubAgentResult
from agents.compliance_agent import ComplianceAgent
from agents.fraud_agent import FraudDetectionAgent
from agents.risk_agent import RiskScoringAgent
from agents.supervisor import SupervisorAgent, SupervisorDecision

__all__ = [
    "BaseSubAgent",
    "SubAgentResult",
    "FraudDetectionAgent",
    "ComplianceAgent",
    "RiskScoringAgent",
    "SupervisorAgent",
    "SupervisorDecision",
]
