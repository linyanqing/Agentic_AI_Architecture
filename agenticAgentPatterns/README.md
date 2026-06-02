# Agentic Agent Patterns — LangGraph Reference Implementations

> **Pattern**: Hybrid State-Steering — deterministic Python routing rails combined with LLM tactical step selection via state steering variables.

---

## 📋 What is Hybrid State-Steering?

Most multi-agent frameworks collapse into one of two extremes:

| Extreme | Mechanism | Problem |
|---|---|---|
| **Fully Hardcoded** | Code decides every next step | Brittle — adding a new node means rewriting routing logic |
| **Fully LLM-Routed** | LLM decides where to go next via text output | Unpredictable — hallucinated node names cause infinite loops or silent failures |

**Hybrid State-Steering** takes the best of both:

```
┌─────────────────────────────────────────────────────────────────┐
│                   HYBRID STATE-STEERING                          │
│                                                                  │
│  STRUCTURAL LAYER (Python, deterministic)                        │
│  ├── graph.add_conditional_edges("supervisor", route_fn, {...}) │
│  ├── route_fn() reads state["next_agent"] token                 │
│  └── unmapped tokens → safe END fallback (GENREL)               │
│                                                                  │
│  TACTICAL LAYER (LLM, context-aware)                             │
│  ├── LLM sets state["next_agent"] = "devops" | "human_gate"    │
│  ├── LLM reads full compliance context before deciding           │
│  └── LLM never names a node directly — only emits a token       │
└─────────────────────────────────────────────────────────────────┘
```

The **structural layer** defines what is topologically possible.  
The **tactical layer** decides what is contextually appropriate.  
Neither layer can cause an undefined graph state.

---

## 📦 Services

```
agenticAgentPatterns/
└── services/
    └── compliance_audit_agent/   ← Full LangGraph application (Hybrid State-Steering)
        ├── agent.py              ← Graph nodes, routing, state, mock responses
        ├── app.py                ← CLI entry point, 6-phase lifecycle demo
        ├── config.py             ← AWS profile, model IDs, routing constants
        └── requirements.txt      ← Python dependencies
```

---

## 🔍 Service: Compliance Audit Agent

### The Enterprise Scenario

A Platform Engineering team submits a Terraform infrastructure-as-code file for
APRA CPS 234 compliance review before it is deployed to production:

> *"Audit our S3 infrastructure Terraform for APRA CPS 234 compliance."*

The submitted code has a critical violation: S3 encryption at rest is missing
(`aws_s3_bucket_server_side_encryption_configuration` not configured), which
violates APRA CPS 234 §36, SOC2 CC6.1, and CIS AWS Foundations 2.1.1.

### The 6-Phase Lifecycle

```
Phase 1   Input non-compliant Terraform
              │
              ▼
Phase 2   Graph execution begins
          ┌──────────────┐
          │  supervisor  │ ← audits TF code, sets next_agent="devops"
          └──────┬───────┘
                 │ route_from_supervisor() → terraform_agent
                 ▼
          ┌──────────────────┐
          │  terraform_agent │ ← patches HCL, adds SSE + KMS, sets next_agent="supervisor"
          └──────┬───────────┘
                 │ graph.add_edge → supervisor (re-audit loop)
                 ▼
          ┌──────────────┐
          │  supervisor  │ ← re-audits patched code, finds COMPLIANT
          └──────┬───────┘   sets next_agent="human_gate"
                 │ route_from_supervisor() → human_gatekeeper
                 ▼
          ┌──────────────────────┐
          │  human_gatekeeper    │ ← interrupt() — graph PAUSES here
          └──────────────────────┘
              │
              ▼
Phase 3   Graph paused — operator review window open
Phase 4   Operator reviews compliance report, decides APPROVE
Phase 5   graph.invoke(Command(resume={"approved": True}))
              │
              ▼
Phase 6   Graph resumes → records decision → END
```

### Architecture: Three LangGraph Nodes

#### `supervisor_agent_node`

Calls Claude Sonnet (frontier model) for complex multi-regulation reasoning.

On iteration 0: sends the full Terraform text.  
On iterations 1+: sends only `audit_metadata` dense references (GENCOST — avoids
re-encoding thousands of HCL tokens on every audit cycle).

Outputs a JSON routing token (`next_agent`) that the Python router maps to a
concrete graph destination:

```python
ROUTE_DEVOPS      = "devops"       # → terraform_agent  (violations found)
ROUTE_HUMAN_GATE  = "human_gate"   # → human_gatekeeper (compliant)
ROUTE_END         = "end"          # → END              (loop guard exit)
```

GENREL loop guard: if `iteration_count >= MAX_AUDIT_ITERATIONS`, the supervisor
node forces `ROUTE_HUMAN_GATE` regardless of LLM output — preventing infinite
remediation loops.

#### `terraform_agent_node`

Calls Claude Haiku (lightweight model — structured code generation only).  
GENCOST: receives only the violation list, not the full TF text.  
Produces an HCL patch block and appends it to `state["terraform_code"]`.  
Always sets `next_agent = ROUTE_SUPERVISOR` (deterministic — no LLM routing here).

#### `human_gatekeeper_node`

Assembles a structured review payload and calls `interrupt(review_payload)`.  
The graph **pauses** at this point — the Python process is suspended but state
is checkpointed in `MemorySaver`.

The CLI resumes the graph with `Command(resume={"approved": True})`.  
The node records the operator decision and routes to `END`.

### Routing: Pure Python, Never LLM-Inferred

```python
def route_from_supervisor(state: AuditState) -> Literal[...]:
    token = state.get("next_agent", "")
    mapping = {
        ROUTE_DEVOPS:     "terraform_agent",
        ROUTE_HUMAN_GATE: "human_gatekeeper",
        ROUTE_END:        END,
    }
    destination = mapping.get(token)
    if destination is None:
        logger.warning("Unmapped token '%s' — GENREL safe fallback to END", token)
        return END          # ← GENREL: undefined tokens terminate safely, never loop
    return destination
```

The LLM emits a token string. Python maps the token to a node name.  
The LLM never names a node directly — it can't hallucinate a node into existence.

---

## 🏗️ State Architecture

```python
class AuditState(TypedDict):
    messages:          Annotated[list[dict], add]   # append-only — full audit trail
    terraform_code:    str                           # grows as patches are applied
    compliance_report: str                           # latest supervisor finding
    is_approved:       bool                          # set by human_gatekeeper
    next_agent:        str                           # LLM steering token
    iteration_count:   int                           # GENREL loop guard counter
    audit_metadata:    dict                          # dense refs for GENCOST
```

`Annotated[list[dict], add]` is a LangGraph reducer — every node that returns
`{"messages": [...]}` **appends** to the existing list rather than replacing it.
This gives a complete tamper-evident audit trail across all graph iterations
without any manual merge logic.

`audit_metadata` stores compressed references (model IDs, issue counts, patched
resource names, severity ratings) rather than raw LLM prose. On re-audit cycles,
the supervisor receives these dense refs rather than re-encoding full TF text —
this is the GENCOST token compression strategy.

---

## 🔐 CORPSEE Constraints Applied

| Pillar | Application |
|---|---|
| **GENCOST** | Supervisor uses `audit_metadata` dense refs on re-audit iterations; Terraform agent receives only issue list, not full HCL |
| **GENSEC** | Bedrock client bound via closure in `build_graph()` — never serialised into state or passed through LLM context windows |
| **GENREL** | Pure Python routing dict; unmapped token → safe `END` fallback; `MAX_AUDIT_ITERATIONS` loop guard prevents infinite remediation cycles |
| **GENPERF** | Two-model architecture: Claude Sonnet for audit (complex reasoning), Claude Haiku for patching (structured generation) |
| **GENEVAL** | `audit_metadata` records issue counts, severity, patched resources — structured evidence for downstream evaluation |
| **GENOPS** | `MemorySaver` checkpointer preserves thread state across `invoke()` calls; `thread_id` enables replay and audit |

---

## ⚡ Human-in-the-Loop: `interrupt()` / `Command(resume=...)`

LangGraph's `interrupt()` / `Command(resume=...)` pair implements a true
asynchronous human gate — the graph is suspended at a specific node with its
full state checkpointed, and can be resumed from any process (CLI, API endpoint,
Lambda function, Slack bot) with an arbitrary structured decision payload.

```python
# Inside human_gatekeeper_node — graph suspends here:
human_decision = interrupt(review_payload)   # ← process pauses, state saved

# In app.py — operator reviews, then resumes:
final_state = graph.invoke(
    Command(resume={"approved": True}),
    config=thread_config,                    # ← same thread_id restores checkpoint
)
```

In production, the interrupt payload would be stored in a database and surfaced
through an approval UI. The `Command(resume=...)` call would originate from a
webhook when the operator clicks Approve/Reject. The LangGraph state serialisation
and checkpoint restore happen transparently via the `MemorySaver` (or a
production `PostgresSaver` / `RedisSaver`).

---

## 🏆 Hybrid State-Steering vs Alternative Patterns

| Pattern | Routing mechanism | Failure mode |
|---|---|---|
| **Hardcoded FSM** | Pure Python switch | Rigid — every new node requires code change |
| **LLM Router** | LLM picks node name from text | Hallucinated node names → undefined state / loops |
| **Tool-Call Router** | LLM calls a `route_to_X()` tool | Better, but tool descriptions become routing bottleneck |
| **Hybrid State-Steering** ← | LLM emits token; Python maps token to node | Bounded — unknown tokens safe-fallback; routing logic is auditable |

The key insight: **the token vocabulary is the contract between the LLM and the
routing layer**. The LLM's job is to pick the right token from a small closed
set; Python's job is to map that token to a graph edge. Neither layer needs to
know the internal structure of the other.

---

## 🚀 Running the Demo

### Prerequisites

```bash
pip install -r agenticAgentPatterns/services/compliance_audit_agent/requirements.txt
```

### Quick Start (no AWS needed — fully offline)

```bash
cd agenticAgentPatterns/services/compliance_audit_agent
python app.py --mock
```

Runs the complete 6-phase lifecycle with deterministic canned responses —
no credentials or network access required. Expected output includes:

```
═══════════════════════════════════════════════════════════════════
  🔍  COMPLIANCE AUDIT AGENT — Hybrid State-Steering Demo
═══════════════════════════════════════════════════════════════════
  Mode    : 🔧 MOCK (offline)
...
  ✅  Graph interrupted at node(s): ('human_gatekeeper',)
...
  Decision: APPROVED ✅
  Reason  : All critical violations remediated by DevOps agent.
...
  │  is_approved    : True                                │
  │  iteration_count: 2                                   │
  │  final severity : NONE                                │
  │  issues found   : 0                                   │
  │  patched        : aws_s3_bucket_server_side_encryption_configuration.audit_logs │
```

### Live Bedrock Mode

```bash
export AWS_PROFILE=rackspace-sydney   # or your configured profile

cd agenticAgentPatterns/services/compliance_audit_agent
python app.py
```

Calls Amazon Bedrock in `ap-southeast-2` using:
- Supervisor: `us.anthropic.claude-3-5-sonnet-20241022-v2:0` (cross-region inference)
- Terraform: `us.anthropic.claude-3-haiku-20240307-v1:0`

### Environment Overrides

```bash
# Custom model selection
SUPERVISOR_MODEL_ID=us.anthropic.claude-3-5-sonnet-20241022-v2:0 \
TERRAFORM_MODEL_ID=us.anthropic.claude-3-haiku-20240307-v1:0 \
python app.py

# Increase max remediation iterations (default: 3)
MAX_AUDIT_ITERATIONS=5 python app.py

# Use .env file
cp .env.example .env   # edit as needed
python app.py
```

---

## 🔌 Extending the Graph

### Adding a New Audit Node

1. **Add the routing token to `config.py`:**
   ```python
   ROUTE_SECURITY_SCAN = "security_scan"
   ```

2. **Implement the node in `agent.py`:**
   ```python
   def security_scan_node(state: AuditState, bedrock_client) -> dict:
       # ... call model or tools ...
       return {"messages": [...], "next_agent": ROUTE_SUPERVISOR, "audit_metadata": {...}}
   ```

3. **Register the node and edge in `build_graph()`:**
   ```python
   graph.add_node("security_scan", lambda s: security_scan_node(s, bedrock_client))
   graph.add_edge("security_scan", "supervisor")  # always re-audits after scan
   ```

4. **Add the token to the routing dict in `route_from_supervisor()`:**
   ```python
   mapping = {
       ROUTE_DEVOPS:        "terraform_agent",
       ROUTE_SECURITY_SCAN: "security_scan",     # ← add here
       ROUTE_HUMAN_GATE:    "human_gatekeeper",
       ROUTE_END:           END,
   }
   ```

No other files need to change. The supervisor LLM will automatically consider
the new token when its system prompt documents the new routing option.

### Replacing `MemorySaver` with a Persistent Checkpointer

For production deployments where graph state must survive process restarts:

```python
# PostgreSQL-backed checkpointer (requires psycopg2)
from langgraph.checkpoint.postgres import PostgresSaver
checkpointer = PostgresSaver.from_conn_string("postgresql://...")

# Redis-backed checkpointer
from langgraph.checkpoint.redis import RedisSaver
checkpointer = RedisSaver.from_conn_string("redis://...")

graph = workflow.compile(checkpointer=checkpointer)
```

The human-in-the-loop interrupt/resume flow is identical — only the storage
backend changes.

---

## 📺 Further Reading

- [LangGraph Human-in-the-Loop](https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/) — official docs on `interrupt()` / `Command(resume=...)` / checkpointers
- [LangGraph StateGraph API](https://langchain-ai.github.io/langgraph/reference/graphs/) — `add_node`, `add_conditional_edges`, `compile`, reducers
- [Amazon Bedrock Converse API](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html) — request/response format for multi-turn inference
- [CORPSEE Framework](../../corpss_app/README.md) — the 7-pillar GenAI Well-Architected framework this pattern is built within
- [Autonomous Agent Loop](../../aiAgentPaterns/README.md) — the simpler Bedrock-native loop pattern (no LangGraph) that this service extends
- [APRA CPS 234](https://www.apra.gov.au/sites/default/files/cps_234_july_2019_for_public_release.pdf) — Australian Prudential Standard: Information Security
