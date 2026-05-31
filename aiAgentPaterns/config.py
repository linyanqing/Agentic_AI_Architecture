"""
Agent Loop Configuration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AWS profile and model settings for the autonomous agent loop demo.
Mirrors the CORPSEE framework conventions (ap-southeast-2 / rackspace-sydney).

Model selection strategy:
  Primary   → amazon.nova-pro-v1:0   (Amazon-native, separate daily quota)
  Fallback  → amazon.nova-lite-v1:0  (cheaper, higher throughput quota)
  Override  → set AGENT_MODEL_ID env var or pass --model CLI flag

  Claude models (anthropic.*) share the account's daily token quota,
  which can be exhausted during heavy demo usage.  The Amazon Nova
  family has its own independent quota pool and is recommended for
  demos in this account.
"""
import os

# ── AWS Runtime ───────────────────────────────────────────────────────────────
AWS_PROFILE = os.environ.get("AWS_PROFILE", "rackspace-sydney")
AWS_REGION  = os.environ.get("AWS_REGION",  "ap-southeast-2")

# ── Model selection ───────────────────────────────────────────────────────────
# Primary: Amazon Nova Pro — capable reasoning, separate quota from Claude.
# Override via AGENT_MODEL_ID env var or --model CLI flag in run_demo.py.
MODEL_PRIMARY  = "amazon.nova-pro-v1:0"
MODEL_FALLBACK = "amazon.nova-lite-v1:0"    # lighter model, larger quota headroom

# The active model — env var wins if set
MODEL_ID = os.environ.get("AGENT_MODEL_ID", MODEL_PRIMARY)

# ── Agent loop limits ─────────────────────────────────────────────────────────
MAX_ITERATIONS      = 10      # Hard cap — prevents runaway loops
TOKEN_FLUSH_BUDGET  = 4_000   # Tokens: above this, summarize & flush long-term
SELF_SCORE_GATE     = 0.75    # Minimum planning confidence before executing

# ── Retry / throttle handling ─────────────────────────────────────────────────
MAX_RETRIES         = 4       # Per-call Bedrock retry attempts
RETRY_BASE_DELAY_S  = 2       # Exponential backoff base (doubles each attempt)

# ── Demo scenario context ─────────────────────────────────────────────────────
DEMO_SESSION_ID = "sydney-client-901"
DEMO_ACCOUNT    = "Qantas-AU-Prod"
DEMO_CLIENT_ID  = "QANTAS-AU"
