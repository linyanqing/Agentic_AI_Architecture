"""
Agent Loop Configuration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AWS profile and model settings for the autonomous agent loop demo.
Mirrors the CORPSEE framework conventions (ap-southeast-2 / rackspace-sydney).
"""
import os

# ── AWS Runtime ───────────────────────────────────────────────────────────────
AWS_PROFILE = os.environ.get("AWS_PROFILE", "rackspace-sydney")
AWS_REGION  = os.environ.get("AWS_REGION",  "ap-southeast-2")

# ── Model selection ───────────────────────────────────────────────────────────
# For production: use Claude Sonnet 4.6 (claude-sonnet-4-6) or later.
# For this demo environment (ap-southeast-2 / rackspace-sydney):
MODEL_ID = os.environ.get(
    "AGENT_MODEL_ID",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",  # available in ap-southeast-2
)

# ── Agent loop limits ─────────────────────────────────────────────────────────
MAX_ITERATIONS      = 10      # Hard cap — prevents runaway loops
TOKEN_FLUSH_BUDGET  = 4_000   # Tokens: above this, summarize & flush long-term
SELF_SCORE_GATE     = 0.75    # Minimum planning confidence before executing

# ── Demo scenario context ─────────────────────────────────────────────────────
DEMO_SESSION_ID = "sydney-client-901"
DEMO_ACCOUNT    = "Qantas-AU-Prod"
DEMO_CLIENT_ID  = "QANTAS-AU"
