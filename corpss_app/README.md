# CORPSS Application — AWS GenAI Well-Architected Framework

A production-ready Python application covering all **6 CORPSS pillars** for enterprise GenAI on AWS (ap-southeast-2 Sydney).

```
corpss_app/
├── config.py                          ← Centralised ARNs & model IDs
├── orchestrator.py                    ← Main pipeline entry-point
├── requirements.txt
├── pillars/
│   ├── gencost.py   C · Cost Optimisation    Bedrock Batch (50% cheaper async jobs)
│   ├── genops.py    O · Operational Excel.   Bedrock Prompt Aliases (OCP-compliant)
│   ├── genrel.py    R · Reliability          SNS + SQS Fan-Out blast isolation
│   ├── genperf.py   P · Performance Eff.     WebSocket streaming + Provisioned PT
│   ├── gensec.py    S · Security             Dual-sided Bedrock Guardrails
│   └── gensust.py   S · Sustainability       Right-sized model routing (Nova Micro → Sonnet)
└── lambda_handlers/
    ├── websocket_handler.py           ← GENPERF: API GW WebSocket Lambda
    └── worker_handler.py              ← GENREL: SQS consumer Lambda (fraud / compliance)
```

---

## Pillar Summary

| # | Pillar | Module | AWS Service | Key Technique |
|---|--------|--------|-------------|---------------|
| C | Cost Optimisation | `gencost.py` | Bedrock Batch Inference | 50% discount via async `create_model_invocation_job` |
| O | Operational Excellence | `genops.py` | Bedrock Prompt Management | Version-locked alias ARN, `{{variable}}` hydration |
| R | Reliability | `genrel.py` | SNS + SQS | Fan-out: N independent queues, blast radius isolation |
| P | Performance Efficiency | `genperf.py` | API GW WebSocket + Bedrock Provisioned Throughput | `converse_stream` token-by-token push |
| S | Security | `gensec.py` | Bedrock Guardrails | Dual-sided sync filter: prompt injection + PII masking |
| S | Sustainability | `gensust.py` | Amazon Nova Micro + Claude 3.5 Sonnet | Right-size: lightweight model for SIMPLE, frontier for COMPLEX |

---

## Pipeline Flow (Real-Time Query)

```
User Query
   │
   ▼
[S · GENSEC]    Guardrail input scan — blocks prompt injection & PII
   │  PASS
   ▼
[S · GENSUST]   Nova Micro classifies intent → SIMPLE or COMPLEX
   │
   ├─ SIMPLE ──► Nova Micro answers directly (low-power track) ──► Response
   │
   └─ COMPLEX
        │
        ▼
   [O · GENOPS]   Fetch PROD prompt alias, hydrate {{variables}}
        │
        ▼
   [P · GENPERF]  Provisioned Throughput inference (no noisy-neighbour lag)
        │         (WebSocket streaming to browser if connection available)
        │
        ▼
   [R · GENREL]   SNS fan-out → fraud-check-queue + compliance-check-queue
        │         (parallel Lambda workers process independently)
        │
        ▼
       Response  (guardrail already applied dual-sided on output)
```

## Batch Pipeline (Nightly)

```
[C · GENCOST]   submit_batch_audit()
                → Bedrock Batch job on S3 manifest
                → 50% cheaper than synchronous On-Demand
                → Results written back to S3
```

---

## Quick Start

```bash
pip install -r requirements.txt
export AWS_ACCOUNT_ID=123456789012
python orchestrator.py
```

> **Pre-requisites:** Update `config.py` with your actual ARNs before running against live AWS resources.

---

## AWS Console Setup Checklist

| Pillar | Console Path | Action |
|--------|-------------|--------|
| GENCOST | Bedrock → Batch inference | Create batch job, point to S3 manifest |
| GENCOST | CloudWatch → Application Signals | Set trace indexing to **1%** |
| GENOPS | Bedrock → Prompt Management | Create prompt, add `{{user_query}}` token, publish v2, create `PROD` alias |
| GENREL | SNS → Topics | Create `AgentTransactionStream` (Standard) |
| GENREL | SQS → Queues | Create `fraud-check-queue`, `compliance-check-queue`, subscribe to SNS |
| GENPERF | Bedrock → Provisioned throughput | Purchase MU, copy ARN to `config.py` |
| GENSEC | Bedrock → Guardrails | Create guardrail: Prompt attacks = HIGH, PII (Email/SSN/IP) = Block/Mask |
| GENSUST | Bedrock → Model catalog | Nova Micro for baseline, Claude 3.5 Sonnet for escalation |
