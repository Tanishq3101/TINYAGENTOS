#!/bin/bash
# scripts/verify_deployment.sh
#
# Day 30 deliverable, adapted from the 30-day plan's template with real
# fixes:
#
#   1. Health check hits /api/v1/health, not /health (api/routes.py's
#      router has prefix="/api/v1" -- same fix already applied in the
#      Dockerfile's HEALTHCHECK).
#   2. Requires a real API key via TINYAGENTOS_API_KEY -- the plan's
#      template hardcoded "sk-test", which only exists because ci.yml
#      seeds it into a fresh CI database on every run. A real deployment
#      has no such row unless you create one yourself.
#   3. Does NOT check a "tasks" table in the database. Tasks are kept
#      in-memory only by core/orchestrator.py (self.tasks dict) -- the
#      DB-backed TaskModel path exists in storage/models.py but
#      save_task_execution() is not currently called from the live API
#      flow (see docs/PII_HANDLING.md's note on this). Checking the
#      tasks table would either find nothing or silently pass on stale
#      data -- neither proves the deployment works. Task creation is
#      instead verified through the real API: create a task, then poll
#      it back.
#   4. Checks the api_keys table instead -- that IS what verify_api_key()
#      actually queries (api/dependencies.py), so it's a meaningful check.
#
# USAGE
#   TINYAGENTOS_API_KEY=<your-real-key> ./scripts/verify_deployment.sh
#
# If you don't have a key yet, create one the same way ci.yml seeds its
# test key (run from the project root, with DATABASE_URL matching your
# deployment):
#   python3 -c "
#   from storage.database import Database
#   from infrastructure.security import SecurityManager
#   db = Database('sqlite:///./tinyagentos.db')
#   db.init_db()
#   db.create_api_key(SecurityManager.hash_api_key('YOUR-NEW-KEY'), label='manual')
#   "

set -e

BASE_URL="${TINYAGENTOS_BASE_URL:-http://localhost:8000}"
API_KEY="${TINYAGENTOS_API_KEY:-}"
DB_PATH="${TINYAGENTOS_DB_PATH:-tinyagentos.db}"

echo "Verifying TinyAgentOS Deployment..."
echo "Base URL: $BASE_URL"
echo ""

if [ -z "$API_KEY" ]; then
    echo "ERROR: TINYAGENTOS_API_KEY is not set."
    echo "This deployment has REQUIRE_AUTH=true by default (config.py) --"
    echo "task creation will 401 without a real, seeded API key. See this"
    echo "script's header comment for how to create one."
    exit 1
fi

# 1. Health check
echo "1. Checking health endpoint..."
HEALTH_RESPONSE=$(curl -fsS "$BASE_URL/api/v1/health") || {
    echo "✗ Health check failed"
    exit 1
}
echo "✓ Health check passed"
echo "  $HEALTH_RESPONSE"
if echo "$HEALTH_RESPONSE" | grep -q '"model_loaded": *false'; then
    echo "  ⚠ model_loaded is false -- inference endpoints will 503 until"
    echo "    a real model is loaded (expected if TINYAGENT_SKIP_LLM_LOAD=1"
    echo "    is set, e.g. in a smoke-test environment; NOT expected in a"
    echo "    real production deployment)."
fi
echo ""

# 2. Create a task
echo "2. Creating a task..."
CREATE_RESPONSE=$(curl -fsS -X POST "$BASE_URL/api/v1/tasks" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"text": "Deployment verification test", "task_type": "summarize", "priority": 1}') || {
    echo "✗ Task creation failed -- check API key validity and REQUIRE_AUTH"
    exit 1
}
echo "✓ Task created"
echo "  $CREATE_RESPONSE"

TASK_ID=$(echo "$CREATE_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['task_id'])")
if [ -z "$TASK_ID" ]; then
    echo "✗ Could not extract task_id from response"
    exit 1
fi
echo ""

# 3. Poll the task back
echo "3. Verifying task is retrievable..."
GET_RESPONSE=$(curl -fsS "$BASE_URL/api/v1/tasks/$TASK_ID" \
    -H "X-API-Key: $API_KEY") || {
    echo "✗ Task retrieval failed"
    exit 1
}
echo "✓ Task retrieval passed"
echo "  $GET_RESPONSE"
echo ""

# 4. Check the API key store (this is the table verify_api_key() actually
#    queries -- NOT a "tasks" table, see header comment above)
echo "4. Checking api_keys table..."
if command -v sqlite3 >/dev/null 2>&1; then
    KEY_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM api_keys;" 2>/dev/null) || {
        echo "⚠ Could not query api_keys table at $DB_PATH -- if running in"
        echo "  Docker, this path is inside the container's named volume,"
        echo "  not directly reachable from the host. Run this check via:"
        echo "  docker compose exec tinyagentos sqlite3 tinyagentos.db \"SELECT COUNT(*) FROM api_keys;\""
    }
    if [ -n "$KEY_COUNT" ]; then
        echo "✓ api_keys table reachable ($KEY_COUNT row(s))"
    fi
else
    echo "⚠ sqlite3 CLI not found on this host -- skipping direct DB check"
    echo "  (task creation/retrieval above already exercised the DB indirectly"
    echo "  via verify_api_key()'s lookup, so this is not a hard failure)"
fi
echo ""

# 5. Check logs
echo "5. Checking logs..."
if [ -f "logs/app.json" ]; then
    echo "✓ Logs present at logs/app.json"
elif command -v docker >/dev/null 2>&1 && docker compose -f docker/docker-compose.yml ps tinyagentos >/dev/null 2>&1; then
    echo "⚠ No logs/app.json on host -- likely running via Docker, where logs"
    echo "  live in the tinyagentos_logs named volume, not a host bind mount."
    echo "  Check with:"
    echo "  docker compose -f docker/docker-compose.yml exec tinyagentos cat /app/logs/app.json"
else
    echo "⚠ No logs found at logs/app.json"
fi
echo ""

echo "✅ Deployment verification complete."
echo ""
echo "Not included in this script (optional, slower):"
echo "  - Orchestration-overhead benchmark: python scripts/run_benchmarks.py"
echo "  - Real inference benchmark (needs the actual model loaded):"
echo "    python scripts/benchmark_inference.py"
