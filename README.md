# AWS AI — CORPSS GenAI Framework

> **Enterprise GenAI on AWS**, architected across 6 well-designed pillars for the Sydney (`ap-southeast-2`) region.

---

## What is CORPSS?

**CORPSS** (pronounced *"Corpus"*) is a GenAI-specific architectural framework that extends the AWS Well-Architected principles to production-grade AI workloads. Each pillar addresses a distinct class of risk and cost that emerges when running large language models at enterprise scale.

| # | Pillar | Code | One-line summary |
|---|--------|------|-----------------|
| **C** | Cost Optimisation | `GENCOST` | Shift non-time-sensitive workloads to Bedrock Batch Inference for a flat **50 % discount** vs On-Demand |
| **O** | Operational Excellence | `GENOPS` | Enforce the Open-Closed Principle — prompts live as version-locked artefacts in Bedrock Prompt Management; core code never changes |
| **R** | Reliability | `GENREL` | Blast-radius isolation via **SNS + SQS Fan-Out** — if one agent queue crashes, every other continues unaffected |
| **P** | Performance Efficiency | `GENPERF` | **API Gateway WebSocket** bi-directional token streaming + Bedrock Provisioned Throughput for guaranteed latency SLAs |
| **S** | Security | `GENSEC` | **Dual-sided Bedrock Guardrails** — synchronously blocks prompt injection on input and masks PII on output, locked to Sydney data-centre boundaries |
| **S** | Sustainability | `GENSUST` | **Right-size every request** — Amazon Nova Micro handles SIMPLE tasks on low-power Trainium/Inferentia chips; only COMPLEX reasoning escalates to Claude 3.5 Sonnet |

> 📄 Full framework blueprint → [`architecture/AI_CORPSS_Lens.txt`](architecture/AI_CORPSS_Lens.txt)

---

## Architecture Diagrams

| Diagram | Description |
|---------|-------------|
| 📐 [`Bedrock_AgentCore_ReferenceArchitecture.md`](architecture/Bedrock_AgentCore_ReferenceArchitecture.md) | **Full reference architecture** — all 7 layers from Client → API Gateway → Step Functions → AgentCore Runtime → Bedrock → SNS/SQS → Observability, with component descriptions and CORPSS pillar mapping |
| 🔀 [`LanggraphGraphDiagram.md`](architecture/LanggraphGraphDiagram.md) | LangGraph state-machine — micro-orchestration flow (Router → Simple/Primary/Fallback nodes) |
| 🪜 [`AWS_Step_StateDiagram.md`](architecture/AWS_Step_StateDiagram.md) | AWS Step Functions state diagram — macro workflow (Choice states, Catch blocks, Succeed terminal) |

---

## Repository Structure

```
AWS_AI/
├── architecture/
│   ├── Bedrock_AgentCore_ReferenceArchitecture.md  ← Full reference architecture + component guide
│   ├── AI_CORPSS_Lens.txt                          ← Master CORPSS framework blueprint
│   ├── LanggraphGraphDiagram.md                    ← LangGraph state-machine diagram
│   └── AWS_Step_StateDiagram.md                    ← AWS Step Functions state diagram
│
└── corpss_app/                     ← Production Python application
    ├── README.md                   ← App-level docs & AWS Console checklist
    ├── config.py                   ← Centralised ARNs & model IDs
    ├── orchestrator.py             ← Main pipeline wiring all 6 pillars
    ├── demo.py                     ← Live AWS demo runner (setup/run/teardown)
    ├── requirements.txt
    ├── pillars/
    │   ├── gencost.py              ← C · Bedrock Batch Inference
    │   ├── genops.py               ← O · Bedrock Prompt Management
    │   ├── genrel.py               ← R · SNS + SQS Fan-Out
    │   ├── genperf.py              ← P · WebSocket streaming + Provisioned Throughput
    │   ├── gensec.py               ← S · Bedrock Guardrails
    │   └── gensust.py              ← S · Right-sized model routing
    └── lambda_handlers/
        ├── websocket_handler.py    ← GENPERF: API GW WebSocket Lambda
        └── worker_handler.py       ← GENREL: SQS consumer Lambda (fraud/compliance)
```

---

## Pipeline Architecture

```
User Query
   │
   ▼
[S · GENSEC]     Guardrail input scan — blocks prompt injection & masks PII
   │  PASS
   ▼
[S · GENSUST]    Nova Micro classifies intent → SIMPLE or COMPLEX
   │
   ├─ SIMPLE ──► Nova Micro answers directly (low-power Trainium track) ──► Response
   │
   └─ COMPLEX
        │
        ▼
   [O · GENOPS]    Fetch PROD prompt alias → hydrate {{variables}}
        │
        ▼
   [P · GENPERF]   Provisioned Throughput inference + WebSocket token streaming
        │
        ▼
   [R · GENREL]    SNS fan-out → fraud-check-queue + compliance-check-queue
        │           (parallel Lambda workers, independent failure domains)
        ▼
       Response     (guardrail dual-sided: output already scanned)

─────────────────────────────────────────
Nightly / background path:

[C · GENCOST]    Bedrock Batch job on S3 manifest → 50% cheaper
```

---

## Quick Start

### Prerequisites
- Python 3.11+
- AWS credentials configured for `ap-southeast-2`
- Bedrock model access: `amazon.nova-micro-v1:0` + `anthropic.claude-3-5-sonnet-20241022-v2:0`

```bash
git clone https://github.com/linyanqing/AWS_AI.git
cd AWS_AI/corpss_app
pip install -r requirements.txt
```

### Run the live demo

```bash
# Step 1 — provision all AWS resources (Guardrail, SNS, SQS, Prompt, S3 manifest)
python demo.py setup

# Step 2 — execute the full 6-pillar demo against live AWS
python demo.py run

# Step 3 — clean up all created resources
python demo.py teardown
```

### Run the orchestrator directly

```python
from orchestrator import CORPSSOrchestrator

orc = CORPSSOrchestrator()

# Real-time query — runs the full CORPSS pipeline
result = orc.handle_query(
    user_query="Analyse the risk of a $2.8M commercial loan in Sydney CBD.",
    account_id="ACC-001",
)
print(result)

# Nightly batch audit — 50% cheaper async processing
batch = orc.submit_batch_audit()
print(batch["jobArn"])
```

> 📄 Full app docs, AWS Console setup checklist, and pillar breakdown → [`corpss_app/README.md`](corpss_app/README.md)

---

## AWS Resources Created by the Demo

| Resource | Service | Pillar |
|----------|---------|--------|
| `corpss-demo-perimeter` guardrail | Amazon Bedrock Guardrails | GENSEC |
| `CORPSS_LOAN_ROUTER` prompt v1 | Bedrock Prompt Management | GENOPS |
| `AgentTransactionStream` SNS topic | Amazon SNS | GENREL |
| `corpss-fraud-check-queue` | Amazon SQS | GENREL |
| `corpss-compliance-check-queue` | Amazon SQS | GENREL |
| `corpss-demo/batch-input.jsonl` | Amazon S3 | GENCOST |
| `BedrockBatchProcessingRole` | AWS IAM | GENCOST |

---

## Region

All resources are deployed in **`ap-southeast-2` (Sydney)** to satisfy AU data sovereignty requirements under APRA prudential standards.
