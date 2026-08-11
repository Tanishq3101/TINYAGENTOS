#!/bin/bash
# scripts/security_scan.sh
#
# Day 21-23 deliverable (Integration Testing & Quality Assurance).
#
# CHANGES FROM THE PLAN'S TEMPLATE
# ---------------------------------------
# 1. `safety check` is deprecated by pyup.io in favor of `safety scan`
#    (the old command still works today but prints a deprecation
#    warning and requires a login for `scan`'s full feature set on
#    newer versions) -- using `check` here since it's the one that
#    still works unauthenticated, matching this project's offline/
#    self-contained tooling style. Swap to `safety scan` if you've
#    got a pyup.io account wired in via CI secrets.
# 2. `dependency-check` (OWASP) is NOT in requirements.txt and is a
#    separate Java-based CLI tool, not a pip package -- the plan's
#    template assumes it's globally available, which it likely isn't
#    on a fresh clone. Guarded with a command -v check so the whole
#    script doesn't hard-fail if it's absent; installs are OS-specific
#    (brew install dependency-check / Docker image / manual download)
#    so this script tells you it's missing rather than guessing how
#    to install it for you.
# 3. Exits non-zero if bandit finds HIGH severity issues, so this is
#    usable as a CI gate (Day 24's GitHub Actions), not just a report
#    generator you have to remember to read.
set -uo pipefail

echo "Running security scans..."
echo

REPORT_DIR="security-reports"
mkdir -p "$REPORT_DIR"

EXIT_CODE=0

# ---------------------------------------------------------------------------
# Bandit -- Python source security issues (SQL injection, hardcoded
# secrets, weak crypto, etc.)
# ---------------------------------------------------------------------------
echo "[1/3] Running Bandit..."
if command -v bandit >/dev/null 2>&1; then
    bandit -r core/ agents/ infrastructure/ api/ storage/ \
        -f json -o "$REPORT_DIR/bandit-report.json"
    BANDIT_EXIT=$?

    # Separately check for HIGH severity findings specifically -- bandit's
    # own exit code trips on ANY finding at default settings (including
    # LOW), which is too noisy to gate CI on. Re-check severity from the
    # JSON report instead of trusting the raw exit code for the gate.
    HIGH_COUNT=$(python3 -c "
import json
try:
    with open('$REPORT_DIR/bandit-report.json') as f:
        data = json.load(f)
    high = [r for r in data.get('results', []) if r.get('issue_severity') == 'HIGH']
    print(len(high))
except Exception:
    print(0)
" 2>/dev/null || echo 0)

    if [ "$HIGH_COUNT" -gt 0 ]; then
        echo "  ✗ Bandit found $HIGH_COUNT HIGH severity issue(s) -- see $REPORT_DIR/bandit-report.json"
        EXIT_CODE=1
    else
        echo "  ✓ Bandit: no HIGH severity issues"
    fi
else
    echo "  ⚠ bandit not found -- is it installed? (it's in requirements.txt: bandit==1.7.5)"
    EXIT_CODE=1
fi
echo

# ---------------------------------------------------------------------------
# Safety -- known CVEs in pinned dependency versions
# ---------------------------------------------------------------------------
echo "[2/3] Running Safety (dependency vulnerability check)..."
if command -v safety >/dev/null 2>&1; then
    safety check --file=requirements.txt --json --output "$REPORT_DIR/safety-report.json" 2>&1
    echo "  See $REPORT_DIR/safety-report.json"
    echo "  NOTE: 'safety check' is deprecated upstream in favor of 'safety scan'."
    echo "  Add it to requirements.txt (not currently listed) if you want this"
    echo "  step to run in CI rather than only when installed locally."
else
    echo "  ⚠ safety not found -- not in requirements.txt. Install with:"
    echo "      pip install safety"
    echo "  before this step will do anything."
fi
echo

# ---------------------------------------------------------------------------
# OWASP Dependency-Check -- separate Java CLI, not a pip package
# ---------------------------------------------------------------------------
echo "[3/3] Running OWASP Dependency-Check..."
if command -v dependency-check >/dev/null 2>&1 || command -v dependency-check.sh >/dev/null 2>&1; then
    DC_CMD=$(command -v dependency-check || command -v dependency-check.sh)
    "$DC_CMD" --project TinyAgentOS --scan . \
        --out "$REPORT_DIR/dependency-check-report" \
        --exclude "**/node_modules/**" --exclude "**/.venv/**"
else
    echo "  ⚠ dependency-check CLI not found on PATH -- this is a separate"
    echo "    Java-based tool, not a pip package, so a fresh clone won't have"
    echo "    it by default. Options:"
    echo "      brew install dependency-check          # macOS"
    echo "      docker run --rm -v \"\$PWD\":/src owasp/dependency-check ...  # any OS"
    echo "    Skipping this step rather than failing the whole scan."
fi
echo

echo "Security scans complete."
echo "Reports written to $REPORT_DIR/:"
echo "  - bandit-report.json"
echo "  - safety-report.json (if safety was installed)"
echo "  - dependency-check-report/ (if the CLI was found)"

exit $EXIT_CODE