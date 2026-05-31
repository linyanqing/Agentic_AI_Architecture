#!/usr/bin/env python3
"""
Autonomous Agent Loop — Demo Runner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs the enterprise scenario end-to-end:

  User: "Our recent deployment is throwing authentication exceptions. Fix it."

Demonstrates all 8 steps of the autonomous agent loop:
  Step 1  Chat History Ingestion     ← stateful session memory
  Step 2  Reasoning (Reflection)     ← model's cognitive assessment
  Step 3  Planning                   ← dynamic task tree generation
  Step 4  Tool Execution             ← MCP Postgres (deployment ledger)
  Step 5  Observation & Reflection   ← loop iteration with result evaluation
  Step 6  Dynamic Plan Update        ← secondary tool selection
  Step 7  Secondary Tool Execution   ← Amazon S3 (error log retrieval)
  Step 8  Token Reduction & Output   ← GENCOST flush + final answer

Usage:
  python run_demo.py           # Full autonomous loop (calls live Bedrock)
  python run_demo.py --dry-run # Mock mode: no AWS calls, see structure only

AWS Profile: rackspace-sydney  |  Region: ap-southeast-2
"""
import json
import logging
import sys

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
# Quiet noisy boto3/urllib3 logs
for lib in ("boto3", "botocore", "urllib3", "s3transfer"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("demo")


# ══════════════════════════════════════════════════════════════════════════════
#  DRY RUN — shows the full loop flow without any AWS calls
# ══════════════════════════════════════════════════════════════════════════════

def dry_run():
    """
    Simulate the autonomous agent loop without live AWS calls.

    Useful for:
     • Understanding the data flow before provisioning AWS credentials
     • CI/CD testing of the orchestration logic
     • Demos in offline/air-gapped environments
    """
    print("\n" + "▓" * 64)
    print("  AUTONOMOUS AGENT LOOP — DRY RUN (no AWS calls)")
    print("  Enterprise scenario: authentication exception incident")
    print("▓" * 64)

    from memory import SessionMemory
    from tools.deployment_ledger import query_deployment_ledger
    from tools.s3_log_reader      import read_s3_log_file
    from tools.notifier           import send_notification

    USER_INPUT = "Our recent deployment is throwing authentication exceptions. Fix it."

    def step(n, label, icon=""):
        bar = "─" * 60
        print(f"\n{bar}")
        print(f"  {icon}  Step {n} · {label}")
        print(bar)

    # ── Step 1 ────────────────────────────────────────────────────────────────
    step(1, "Chat History Ingestion", "📥")
    mem = SessionMemory(session_id="sydney-client-901")
    mem.set("active_account", "Qantas-AU-Prod")
    mem.set("client_id",      "QANTAS-AU")
    mem.set("contact_email",  "oncall@qantas.com.au")
    mem.set("incident_id",    "INC-2026-001")

    print(f"\n  User message : {USER_INPUT}")
    print(f"  Session ID   : {mem.session_id}")
    print(f"  Memory vars  : {json.dumps(mem.short_term, indent=4)}")

    # ── Step 2 ────────────────────────────────────────────────────────────────
    step(2, "Reasoning — Initial Reflection", "🧠")
    print("""
  Model's internal rationale (simulated):
    "The user reports authentication exceptions on 'recent deployment'.
     From short-term memory I can see the account is Qantas-AU-Prod.
     I cannot guess the issue — I must first discover which specific
     application was deployed and inspect its runtime execution traces.
     I will query the deployment ledger, then fetch the error logs."
""")

    # ── Step 3 ────────────────────────────────────────────────────────────────
    step(3, "Planning — Task Tree Generation", "📋")
    mem.add_task("T1", "Query deployment ledger for recent changes under Qantas-AU-Prod")
    mem.add_task("T2", "Retrieve error log file from S3 URI identified in T1")
    mem.add_task("T3", "Diagnose root cause from log content")
    mem.add_task("T4", "Notify client engineering team with resolution details")
    print(f"\n  Task tree:\n{mem.task_summary()}")

    # ── Step 4 ────────────────────────────────────────────────────────────────
    step(4, "Tool Execution — MCP Postgres (deployment ledger)", "🔌")
    mem.start_task("T1")
    ledger_result = query_deployment_ledger(client_id="QANTAS-AU", limit=1)
    mem.record_tool_call("query_deployment_ledger", {"client_id": "QANTAS-AU", "limit": 1}, ledger_result)

    deployment = ledger_result["deployments"][0]
    print(f"\n  Tool: query_deployment_ledger(client_id='QANTAS-AU', limit=1)")
    print(f"  Via : MCP Postgres → Aurora Serverless (ap-southeast-2)")
    print(f"\n  Response:")
    print(f"    deployment_id  : {deployment['deployment_id']}")
    print(f"    app_name       : {deployment['app_name']}")
    print(f"    timestamp      : {deployment['timestamp']}")
    print(f"    config_s3_uri  : {deployment['config_s3_uri']}")
    print(f"    status         : {deployment['status']}")

    # ── Step 5 ────────────────────────────────────────────────────────────────
    step(5, "Observation & Self-Reflection", "🔍")
    s3_uri = deployment["config_s3_uri"]
    mem.complete_task("T1", result=ledger_result, notes=f"Found {deployment['app_name']} — S3 URI: {s3_uri}")
    print(f"""
  Model observation:
    OBSERVATION: The recent deployment was '{deployment['app_name']}' (v{deployment['version']}).
                 I now have an explicit S3 URI for the runtime error log: {s3_uri}
    GAP:         I do not yet know the specific error — I must fetch the log file.
    NEXT ACTION: Execute read_s3_log_file(uri='{s3_uri}', grep_filter='CRITICAL|ERROR')
                 to retrieve only the fault lines (GENCOST: reduces token payload).

  Updated task tree:\n{mem.task_summary()}
""")

    # ── Step 6 ────────────────────────────────────────────────────────────────
    step(6, "Dynamic Plan Update — Secondary Tool Selected", "🔄")
    print(f"  Task T1 ✅ complete — pivoting to Task T2")
    print(f"  Selecting: read_s3_log_file (Amazon S3)")
    print(f"  Applying grep_filter='CRITICAL|ERROR' to reduce token payload (GENCOST)")

    # ── Step 7 ────────────────────────────────────────────────────────────────
    step(7, "Secondary Tool Execution — Amazon S3 (error log)", "☁️ ")
    mem.start_task("T2")
    log_result = read_s3_log_file(uri=s3_uri, max_lines=15, grep_filter="CRITICAL|ERROR|Config")
    mem.record_tool_call("read_s3_log_file", {"uri": s3_uri, "grep_filter": "CRITICAL|ERROR"}, log_result)

    print(f"\n  Tool: read_s3_log_file(uri='{s3_uri}', grep_filter='CRITICAL|ERROR|Config')")
    print(f"  Via : Amazon S3 GetObject (ap-southeast-2)")
    print(f"\n  Log content ({log_result['line_count']} lines after grep filter):")
    for line in log_result["content"].splitlines():
        print(f"    {line}")

    # Root cause diagnosis
    mem.complete_task("T2", result=log_result, notes="Redis password mismatch identified at line 42")
    mem.complete_task("T3", notes="Root cause: SSM param rotated but config.yaml still references old secret")

    # ── Send notification (T4) ────────────────────────────────────────────────
    notif = send_notification(
        recipient="oncall@qantas.com.au",
        subject=f"[INC-2026-001] Root cause identified: {deployment['app_name']} Redis credential mismatch",
        body=(
            f"Deployment {deployment['deployment_id']} of {deployment['app_name']} v{deployment['version']} "
            f"is failing because the AWS SSM parameter /prod/auth-gateway/redis/password was rotated "
            f"on 2026-05-30T22:00Z, but the deployment pipeline did not re-inject the updated secret. "
            f"The application is reading the OLD password, causing WRONGPASS errors from Redis. "
            f"Resolution: Re-deploy with the current SSM secret version, or manually update "
            f"/etc/auth-gateway/config.yaml line 42 on the production instances."
        ),
        severity="CRITICAL",
        channel="PAGERDUTY",
    )
    mem.complete_task("T4", notes=f"Notification sent: {notif['message_id']}")

    # ── Step 8 ────────────────────────────────────────────────────────────────
    step(8, "Token Reduction & Final Output (GENCOST)", "🗜️ ")

    # Flush intermediate tool traces to long-term memory
    mem.flush_to_long_term(
        summary=(
            "Incident INC-2026-001 diagnosed. auth-gateway-service v2.4.1 failing due to "
            "Redis WRONGPASS — SSM secret /prod/auth-gateway/redis/password rotated but not "
            "re-injected into deployment. Config line 42. Notification sent to oncall@qantas.com.au."
        )
    )

    final_output = f"""
  ╔══════════════════════════════════════════════════════════╗
  ║  AGENT FINAL RESPONSE                                    ║
  ╚══════════════════════════════════════════════════════════╝

  I have diagnosed the issue with your recent deployment.

  Service     : {deployment['app_name']} (v{deployment['version']})
  Deployed at : {deployment['timestamp']}
  Root cause  : Authentication mismatch between the application
                configuration and your Redis caching cluster.

  Specifically: The AWS SSM parameter
    /prod/auth-gateway/redis/password
  was rotated on 2026-05-30T22:00Z, but the deployment
  pipeline did not re-inject the updated secret value.
  auth-gateway-service is still reading the OLD password
  at config.yaml line 42, causing WRONGPASS errors from
  Redis. All downstream authentication token requests fail.

  Resolution:
    Option A (Fast): Re-deploy auth-gateway-service — the
      pipeline will pick up the current SSM secret version.
    Option B (Immediate): Manually update line 42 in
      /etc/auth-gateway/config.yaml on production instances
      to reference the rotated secret, then restart the service.

  Your on-call team has been paged via PagerDuty.
  Incident reference: INC-2026-001
"""
    print(final_output)

    # Final task tree
    print(f"  Final task tree:\n{mem.task_summary()}")
    print(f"\n  Memory stats:")
    print(f"    Short-term tokens flushed : ✅ (GENCOST — long-term summary written)")
    print(f"    Long-term summaries       : {len(mem.long_term)}")
    print(f"    Tool calls made           : 3 (ledger + S3 + SNS)")

    print("\n" + "▓" * 64)
    print("  DRY RUN COMPLETE — all 8 loop steps demonstrated")
    print("▓" * 64)


# ══════════════════════════════════════════════════════════════════════════════
#  LIVE RUN — calls real Bedrock with live model reasoning
# ══════════════════════════════════════════════════════════════════════════════

def live_run():
    """Run the autonomous agent loop with live Amazon Bedrock inference."""
    from autonomous_agent_loop import AgentLoop
    from config import DEMO_SESSION_ID

    print("\n" + "▓" * 64)
    print("  AUTONOMOUS AGENT LOOP — LIVE (Amazon Bedrock)")
    print("  Enterprise scenario: authentication exception incident")
    print("▓" * 64)

    USER_INPUT = (
        "Our recent deployment is throwing authentication exceptions. Fix it."
    )

    loop = AgentLoop(session_id=DEMO_SESSION_ID)

    try:
        final_answer = loop.run(user_input=USER_INPUT)

        print("\n" + "═" * 64)
        print("  AGENT FINAL RESPONSE")
        print("═" * 64)
        print(f"\n{final_answer}\n")

        print("  Memory state:")
        print(json.dumps(loop.memory.to_dict(), indent=4, default=str))

    except Exception as exc:
        logger.error("Live run failed: %s", exc)
        logger.info("Falling back to dry-run mode …")
        dry_run()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv or "-d" in sys.argv

    if dry:
        dry_run()
    else:
        live_run()
