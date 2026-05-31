# Autonomous AI Agent Loop — Reference Implementation

> **Pattern**: Autonomous multi-step reasoning loop with native Bedrock tool-use (function calling), stateful session memory, GENCOST token compression, and dynamic task planning.

---

## 📋 The Enterprise Scenario

A corporate client sends an urgent, ambiguous request:

> *"Our recent deployment is throwing authentication exceptions. Fix it."*

A linear RAG pipeline fails here — the request has **no error code, no server ID, no context**. The autonomous agent loop handles this by reasoning, planning, acting, observing, and re-planning in a continuous cycle until the root cause is found.

---

## 🔄 The 8-Step Agent Loop

```
[User Input] ──► (1) Chat History Ingestion
                       │  Stateful session context loaded from memory store
                       ▼
                 (2) Reasoning (Initial Reflection)
                       │  "Need to find which app was deployed — query ledger first"
                       ▼
                 (3) Planning (Task Tree Generation)
                       │  T1: Query ledger → T2: Fetch S3 logs → T3: Diagnose → T4: Notify
                       ▼
                 (4) Tool Execution ◄──────────────── [MCP Postgres Server]
                       │  query_deployment_ledger(client_id="QANTAS-AU", limit=1)
                       │  → {"app_name": "auth-gateway-service", "config_s3_uri": "s3://..."}
                       ▼
                 (5) Observation & Self-Reflection
                       │  "Found the app + S3 log URI. T1 done. Now fetch the log."
                       ▼
                 (6) Dynamic Plan Update (Loop Iteration)
                       │  Mark T1 DONE → pivot to T2 → select read_s3_log_file
                       ▼
                 (7) Secondary Tool Execution ◄──────── [Amazon S3 Log Store]
                       │  read_s3_log_file(uri="s3://prod-logs/auth-gateway/err.log")
                       │  → "[CRITICAL] Redis password mismatch in line 42"
                       ▼
                 (8) Token Reduction & Final Answer
                       │  Flush intermediate traces → long-term memory (GENCOST)
                       ▼
                 [Root cause identified. Client notified via PagerDuty.]
```

---

## 🏗️ Architecture

```
aiAgentPaterns/
├── run_demo.py               ← Entry point (--dry-run or live Bedrock)
├── autonomous_agent_loop.py  ← The 8-step AgentLoop orchestrator
├── memory.py                 ← Two-tier stateful session memory
├── config.py                 ← AWS profile, model, loop limits
└── tools/
    ├── __init__.py           ← Tool registry + execute_tool dispatcher
    ├── deployment_ledger.py  ← Tool 1: MCP Postgres → deployment audit DB
    ├── s3_log_reader.py      ← Tool 2: Amazon S3 GetObject → error logs
    └── notifier.py           ← Tool 3: Amazon SNS → PagerDuty/Slack
```

### Component Mapping to AWS Services

| Code Component | Production AWS Service |
|---|---|
| `AgentLoop._call_model()` | Amazon Bedrock `converse` API (with `toolConfig`) |
| `AgentLoop._execute_tool_calls()` | **Amazon Bedrock AgentCore** runtime harness |
| `SessionMemory.short_term` | AgentCore in-session managed state |
| `SessionMemory.flush_to_long_term()` | Amazon S3 / DynamoDB session store |
| `query_deployment_ledger` | **MCP Postgres Server** → Aurora Serverless |
| `read_s3_log_file` | Amazon S3 `GetObject` (pre-signed URL via IAM role) |
| `send_notification` | Amazon SNS → PagerDuty / Slack webhook |
| Tool routing loop | **LangGraph / AWS Strands Agents** orchestration engine |

---

## 🧠 Key Design Decisions

### 1. Why `stopReason == "tool_use"` drives the loop

The Bedrock `converse` API returns `stopReason="tool_use"` whenever the model needs to execute a tool before continuing. The loop resubmits tool results as `toolResult` content blocks and re-invokes the model. This continues until `stopReason="end_turn"` — the model has enough information to answer.

```python
while response["stopReason"] == "tool_use":
    tool_results = execute_all_tool_calls(response)
    messages.append({"role": "user", "content": tool_results})
    response = bedrock.converse(messages=messages, toolConfig=TOOL_CONFIG)
```

### 2. Two-tier memory (GENCOST optimisation)

The main cost driver in long agentic loops is **token accumulation** — every intermediate tool result stays in the prompt context window. The `ContinuousEvalLoop` and `SessionMemory.flush_to_long_term()` implement the GENCOST control:

- **Short-term** (prompt window): last N tool results, current task tree
- **Long-term** (S3/DynamoDB): compressed summaries of completed task chains
- When `_token_estimate > TOKEN_FLUSH_BUDGET`: archive + clear short-term buffer

### 3. Self-reflection via system prompt engineering

The system prompt instructs the model to output a structured observation block after every tool result:

```
OBSERVATION: <what the tool result tells you>
GAP:         <what is still unknown>
NEXT ACTION: <exactly what you will do next and why>
```

This forces the model to reason explicitly before selecting the next tool — preventing premature answers and tool misuse.

### 4. Dynamic task tree (not hardcoded)

The agent builds its task list based on the initial reasoning call — in production, a lightweight Nova Micro sub-call generates a formal JSON task plan. Tasks are marked `PENDING → IN_PROGRESS → DONE` as the loop progresses, giving a complete audit trail.

---

## 🚀 Running the Demo

### Quick Start (no AWS needed)

```bash
cd aiAgentPaterns
python run_demo.py --dry-run
```

Shows the complete 8-step trace with mock tool responses — no AWS credentials required.

### Live Bedrock Mode

```bash
# Ensure your AWS profile is configured
export AWS_PROFILE=rackspace-sydney

cd aiAgentPaterns
python run_demo.py
```

Calls live Amazon Bedrock (`anthropic.claude-3-5-sonnet-20241022-v2:0`) in `ap-southeast-2` and runs the full autonomous reasoning loop with real model inference.

---

## 🔌 Adding New Tools

1. Create `tools/my_tool.py` with the implementation function and `MY_TOOL_SCHEMA` (Bedrock `toolSpec` format)
2. Register in `tools/__init__.py`:
   ```python
   from tools.my_tool import my_tool_func, MY_TOOL_SCHEMA
   TOOL_CONFIG["tools"].append(MY_TOOL_SCHEMA)
   TOOL_REGISTRY["my_tool_func"] = my_tool_func
   ```
3. The agent will automatically discover and use the new tool when the model requests it.

---

## 📺 Further Reading

- [Claude 4 + Claude Code + Strands Agents in Action | AWS Show & Tell](https://www.youtube.com/watch?v=yWkxb2kmUIk) — live engineering breakdown of Anthropic reasoning models interfacing with AWS agent architectures
- [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-core.html) — the production harness that manages microVM sessions, tool dispatch, and secure credential injection
- [Bedrock converse API tool use guide](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use.html) — official reference for `toolConfig`, `toolUse`, and `toolResult` message format
- [CORPSEE Framework](../corpss_app/README.md) — the 7-pillar GenAI Well-Architected framework this pattern is built within
