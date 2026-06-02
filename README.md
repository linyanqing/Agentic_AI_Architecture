# Agentic AI Architecture — CORPSEE GenAI Framework

> **Enterprise GenAI on AWS**, architected across 7 well-designed pillars for the Sydney (`ap-southeast-2`) region.

---

## What is CORPSEE?

**CORPSEE** (pronounced *"Corpus-E"*) is a GenAI-specific architectural framework that extends the AWS Well-Architected principles to production-grade AI workloads. Each pillar addresses a distinct class of risk, cost, or trust gap that emerges when running large language models at enterprise scale.

| # | Pillar | Code | One-line summary |
|---|--------|------|-----------------|
| **C** | Cost Optimisation | `GENCOST` | **Prompt Caching** (80% token saving) + Bedrock Batch Inference (**50% cheaper**) + W-S-C-I context engineering to control token bloat |
| **O** | Operational Excellence | `GENOPS` | Open-Closed prompts via Bedrock **PROD-ACTIVE** aliases + **OpenTelemetry (OTEL)** observability for granular agent reasoning visibility |
| **R** | Reliability | `GENREL` | **Circuit breaker failover** (PT → serverless) + SNS/SQS Fan-Out blast-radius isolation + Strands Agents multi-agent design |
| **P** | Performance Efficiency | `GENPERF` | **AgentCore Harness** execution + Managed Agent Memory + **WebSocket converse_stream** for sub-200 ms token delivery |
| **S** | Security | `GENSEC` | Ephemeral **microVM session isolation** (AgentCore Runtime) + dual-sided Bedrock Guardrails locked to Sydney data-centre boundary |
| **E** | Evaluation & Trust | `GENEVAL` | **5-step continuous eval loop** — Ground Truth → Offline Eval → Safe Deploy → Online Monitor → Continuous Improvement via AgentCore Traces |
| **E** | Sustainability | `GENSUST` | **Right-size every request** — Nova Micro on low-power Trainium/Inferentia for SIMPLE; Claude 3.5 Sonnet only when COMPLEX reasoning demanded |

> 📄 Full framework blueprint → [`architecture/AI_Architecture_Lens.txt`](architecture/AI_Architecture_Lens.txt)  
> 📑 AWS Summit source briefing → [`architecture/AIM201_Fromdemotodeployment-...final.pdf`](architecture/AIM201_Fromdemotodeployment-solvingagenticAIstoughestchallengesfinal.pdf)

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
Agentic_AI_Architecture/
├── architecture/
│   ├── Bedrock_AgentCore_ReferenceArchitecture.md          ← Full reference architecture + component guide
│   ├── AI_Architecture_Lens.txt                            ← Master CORPSEE 7-pillar framework blueprint
│   ├── AIM201_Fromdemotodeployment-...final.pdf            ← AWS Summit AIM201 source briefing
│   ├── LanggraphGraphDiagram.md                            ← LangGraph state-machine diagram
│   └── AWS_Step_StateDiagram.md                            ← AWS Step Functions state diagram
│
└── corpss_app/                     ← Production Python application
    ├── README.md                   ← App-level docs & AWS Console checklist
    ├── config.py                   ← Centralised ARNs & model IDs (7 pillars)
    ├── orchestrator.py             ← CORPSEEOrchestrator — all 7 pillars
    ├── demo.py                     ← Live AWS demo runner (setup/run/teardown)
    ├── requirements.txt
    ├── pillars/
    │   ├── gencost.py              ← C · Prompt Caching + Bedrock Batch
    │   ├── genops.py               ← O · Bedrock Prompt Management + OTEL
    │   ├── genrel.py               ← R · Circuit Breaker + SNS/SQS Fan-Out
    │   ├── genperf.py              ← P · AgentCore Harness + WebSocket Stream
    │   ├── gensec.py               ← S · microVM Isolation + Guardrails
    │   ├── geneval.py              ← E · 5-Step Eval Loop + Trace Scoring  ← NEW
    │   └── gensust.py              ← E · Right-sized Model Routing
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
[S · GENSEC]     Dual-sided guardrail scan + ephemeral microVM session boundary
   │  PASS
   ▼
[E · GENSUST]    Nova Micro classifies intent → SIMPLE or COMPLEX
   │
   ├─ SIMPLE ──► Nova Micro answers directly (low-power Trainium track) ──► Response
   │
   └─ COMPLEX
        │
        ▼
   [O · GENOPS]    Fetch PROD-ACTIVE prompt alias → hydrate {{variables}} + OTEL trace
        │
        ▼
   [R · GENREL]    Circuit breaker: Provisioned Throughput → auto-fallback to serverless
        │
        ▼
   [P · GENPERF]   AgentCore Harness invoke_agent + WebSocket converse_stream
        │           Managed Memory re-injects user context automatically
        ▼
   [R · GENREL]    SNS fan-out → fraud-check-queue + compliance-check-queue
        │           (parallel Lambda workers, independent failure domains)
        ▼
   [E · GENEVAL]   AgentCore Trace scoring: RAG faithfulness · rationale · tool calls
        ▼
       Response

─────────────────────────────────────────────────────
Background / evaluation paths:

[C · GENCOST]    Bedrock Batch job on S3 manifest → 50% cheaper
                 Prompt Caching on static context → 80% token saving

[E · GENEVAL]    Bedrock Model Evaluation job → Faithfulness / Helpfulness / Coherence
                 S3 ground-truth dataset → score-gated PROD promotion
```

---

## Quick Start

### Prerequisites
- Python 3.11+
- AWS credentials configured for `ap-southeast-2`
- Bedrock model access: `amazon.nova-micro-v1:0` + `anthropic.claude-3-5-sonnet-20241022-v2:0`

```bash
git clone https://github.com/linyanqing/Agentic_AI_Architecture.git
cd Agentic_AI_Architecture/corpss_app
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
| `eval/ground-truth.jsonl` | Amazon S3 | GENEVAL |
| `BedrockEvalRole` | AWS IAM | GENEVAL |

---

## Region

All resources are deployed in **`ap-southeast-2` (Sydney)** to satisfy AU data sovereignty requirements under APRA prudential standards.
