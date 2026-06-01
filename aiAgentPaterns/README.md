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

## ⚙️ Core Mechanism — How the LLM Knows Which Tool to Call

The LLM has no built-in knowledge of your tools. There are three distinct layers
that work together to make tool selection happen.

### Layer 1 — Tool Registration: Teaching the LLM what tools exist

Every call to `bedrock.converse()` includes a `toolConfig` block — this is how
the LLM learns what tools are available. Without it, the model can only output text.

```python
bedrock.converse(
    modelId=self._active_model,
    messages=messages,
    system=[{"text": system_prompt}],
    toolConfig=TOOL_CONFIG,          # ← registration happens here, every call
)
```

`TOOL_CONFIG` is built in `tools/__init__.py` from three `toolSpec` schemas.
Each schema has exactly three fields the LLM reads:

```python
"toolSpec": {
    "name":        "query_deployment_ledger",   # what the tool is called
    "description": "Query the deployment audit ledger... Use this to identify
                    WHICH service was recently deployed when investigating
                    production incidents.",      # WHEN to use it ← most important
    "inputSchema": { ... }                       # what arguments to pass
}
```

**The `description` field is the routing logic.** It is plain English written by
you, encoding the domain knowledge of when this tool should be chosen over others.
The model reads all descriptions and uses them like a menu before acting.

### Layer 2 — Tool Selection: How the LLM decides which tool and when

The LLM never runs code — it only outputs text. When it wants a tool, it outputs
a structured `toolUse` block instead of a plain text response, and Bedrock signals
this with `stopReason = "tool_use"`:

```json
{
  "toolUse": {
    "toolUseId": "call-abc123",
    "name":      "query_deployment_ledger",
    "input":     { "client_id": "QANTAS-AU", "limit": 1 }
  }
}
```

The model's internal reasoning:
> *"The user reported an auth exception. I need to know which service was deployed.
> The tool `query_deployment_ledger` says 'Use this to identify WHICH service was
> recently deployed when investigating production incidents.' That matches. I'll
> call it with `client_id = QANTAS-AU` from the session memory."*

The decision is entirely inside the model. **The quality of your `description`
fields directly controls the quality of tool selection.**

### Layer 3 — Dispatch: How your code executes what the LLM chose

Once `stopReason == "tool_use"`, the `while` loop in `run()` does three things:

**A — Extract** what the model requested:
```python
tool_name   = block["toolUse"]["name"]      # e.g. "query_deployment_ledger"
tool_input  = block["toolUse"]["input"]     # e.g. {"client_id": "QANTAS-AU"}
tool_use_id = block["toolUse"]["toolUseId"]
```

**B — Dispatch** to the Python function via `TOOL_REGISTRY` in `tools/__init__.py`:
```python
TOOL_REGISTRY = {
    "query_deployment_ledger": query_deployment_ledger,  # Python function
    "read_s3_log_file":        read_s3_log_file,
    "send_notification":       send_notification,
}
result = TOOL_REGISTRY[tool_name](**tool_input)
```

**C — Return** the result as a `toolResult` block and re-call `converse()`:
```python
{"toolResult": {"toolUseId": tool_use_id, "content": [{"json": result}]}}
```

The model reads the result, reasons again, and either requests another tool
(`stopReason = "tool_use"`) or produces its final answer (`stopReason = "end_turn"`).

### The complete data flow

```
YOUR CODE                           LLM (inside Bedrock)
──────────────────────────────────────────────────────────────
Pass toolConfig (3 schemas)   →    Reads descriptions, builds
                                   mental map of available tools

                              ←    stopReason = "tool_use"
                                   toolUse: {name: "query_deployment_ledger",
                                             input: {client_id: "QANTAS-AU"}}

TOOL_REGISTRY["query_..."]()
→ runs Python function
→ returns {"deployments": [...]}

Pass toolResult back          →    Reads result, reasons:
                                   "Now I have the app name + S3 URI.
                                    I need the log file next."
                              ←    stopReason = "tool_use"
                                   toolUse: {name: "read_s3_log_file", ...}

read_s3_log_file(uri=...)
→ returns log content

Pass toolResult back          →    Reads log, diagnosis complete.
                              ←    stopReason = "end_turn"
                                   Plain text final answer.

Loop exits. Return to user.
```

---

## 🏆 Agent Quality — The Three Dimensions

The same base model can perform dramatically differently as an agent depending
on three independent quality dimensions. This is what distinguishes agents like
Claude Code and Rovo Dev from a generic loop like this reference implementation.

```
┌─────────────────────────────────────────────────────────────┐
│                      AGENT QUALITY                           │
│                                                              │
│  1. REASONING    ← base model capability + system prompt    │
│  2. PLANNING     ← planning strategy + memory architecture  │
│  3. TOOL CALLING ← tool description quality + tool design   │
└─────────────────────────────────────────────────────────────┘
```

### Dimension 1 — Reasoning Quality

Comes from two sources: the **base model** and the **system prompt**.

A stronger model, given the same ambiguous input, reasons before acting:
> *"I must not guess. I need to discover context first — query what was recently
> deployed before looking at any logs."*

A weaker model might immediately call a log tool with an invented service name,
fail, and loop badly. Claude Code and Rovo Dev use Claude Sonnet/Opus class
models — frontier-level reasoning — as their base.

The **system prompt** adds domain-specific reasoning rules on top. Claude Code's
system prompt encodes decades of software engineering best practices: explore
before editing, verify after every change, never assume file structure.

### Dimension 2 — Planning Quality

Architectural choices matter more than model choice here.

| Agent | Planning approach | Characteristic |
|---|---|---|
| This demo | Plan-and-Execute | One upfront LLM plan, then execute linearly |
| Claude Code | ReAct per step | Reason → Act → Observe → Re-reason each iteration |
| Rovo Dev | Workflow-aware | Maps request to Atlassian workflow before any code |


Claude Code does not just plan at the start — it re-evaluates after every tool
result, which means it recovers gracefully from unexpected findings mid-task.
Rovo Dev knows to check Jira tickets and PR review comments before touching code,
because its planning is built around Atlassian's collaboration model.

### Dimension 3 — Tool Calling Quality

The most underappreciated dimension. **The `description` in `toolSpec` is the
routing logic** — its quality directly determines whether the model picks the
right tool at the right moment.

```
❌  Poor description (causes wrong tool selection):
    "Read a file from S3"

✅  Production-grade description (Claude Code / Rovo Dev level):
    "Read the contents of a log file from Amazon S3.
     Use the S3 URI obtained from query_deployment_ledger — never guess a path.
     Apply grep_filter to return only ERROR/CRITICAL lines (reduces token payload).
     Use ONLY after you have a confirmed URI from a prior tool result."
```

The rich description gives the model: **when** to call it, **what guard rails**
apply, **why** to use the optional filter, and **what precondition** must be true.
Claude Code's tool descriptions for `Read`, `Edit`, `Bash`, and `Write` encode
years of software engineering best practices in plain English — that is a
significant part of why it performs reliably across diverse coding tasks.

### How this demo compares to Claude Code and Rovo Dev

```
┌──────────────────┬──────────────────┬───────────────────┬──────────────────┐
│                  │  This Demo Loop  │   Claude Code     │   Rovo Dev       │
├──────────────────┼──────────────────┼───────────────────┼──────────────────┤
│ Base model       │ Nova Pro / Lite  │ Claude Sonnet /   │ Claude           │
│                  │                  │ Opus 4.x          │ (Sonnet class)   │
├──────────────────┼──────────────────┼───────────────────┼──────────────────┤
│ System prompt    │ Generic infra    │ Deep software      │ Atlassian        │
│ domain depth     │ support pattern  │ engineering rules  │ workflow rules   │
├──────────────────┼──────────────────┼───────────────────┼──────────────────┤
│ Planning         │ LLM upfront plan │ ReAct per step,   │ Workflow-aware,  │
│ strategy         │ (Plan+Execute)   │ verify after each │ Jira-first       │
├──────────────────┼──────────────────┼───────────────────┼──────────────────┤
│ Tool set         │ 3 mock tools     │ 15+ real tools:   │ Jira, Confluence,│
│                  │ (demo scenario)  │ Read, Edit, Bash, │ Bitbucket, code  │
│                  │                  │ Write, WebSearch… │ search, PR review│
├──────────────────┼──────────────────┼───────────────────┼──────────────────┤
│ Tool description │ Good for demo    │ Production-grade,  │ Atlassian API    │
│ quality          │ illustration     │ years of tuning   │ optimised        │
├──────────────────┼──────────────────┼───────────────────┼──────────────────┤
│ Memory           │ Two-tier manual  │ Context window +  │ Atlassian cloud  │
│                  │ (demo concept)   │ file system state │ session state    │
└──────────────────┴──────────────────┴───────────────────┴──────────────────┘
```

### The key insight

This demo loop and Claude Code implement **the same architectural pattern** —
`stopReason == "tool_use"` drives the loop, tool descriptions route decisions,
tool results feed back into reasoning. The Bedrock `converse` API and Anthropic
API use the same underlying mechanism.

What makes Claude Code exceptional is not a different architecture — it is
**software engineering knowledge encoded into its system prompt and tool
descriptions**, combined with a frontier reasoning model and a rich tool ecosystem
built for coding workflows.

This demo is a **transparent reference implementation of the pattern**.
Claude Code is a **highly optimised production specialisation of the same pattern**.
The skeleton is identical — the quality of what is inside each layer is what differs.

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
