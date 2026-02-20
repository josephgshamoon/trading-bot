#!/usr/bin/env python3
"""
Weekly Maintenance Script
=========================
Runs health checks, tests, and basic fixes. Commits and pushes if anything changed.

Usage:
    python3 maintenance.py          # Run full maintenance
    python3 maintenance.py --dry    # Check only, don't commit/push

Designed to run via cron:
    0 3 * * 0 /usr/bin/python3 /home/clawdbot/polymarket-bot/maintenance.py >> /home/clawdbot/polymarket-bot/logs/maintenance.log 2>&1
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
LOGFILE = ROOT / "logs" / "maintenance.log"
REPORT = []
FIXES = []


def log(msg):
    ts = datetime.now(tz=__import__('datetime').timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    REPORT.append(line)


def run(cmd, timeout=60):
    """Run a shell command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(ROOT),
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

def check_imports():
    """Verify all source modules import without errors."""
    log("Checking imports...")
    modules = [
        "src.polymarket_client",
        "src.backtest",
        "src.data_collector",
        "src.paper_trader",
        "src.trader",
    ]
    ok = True
    for mod in modules:
        code, out, err = run(f"{sys.executable} -c \"import {mod}\"")
        if code != 0:
            # trader.py raises ImportError for py-clob-client — that's expected
            if mod == "src.trader" and "py-clob-client" in err:
                log(f"  {mod}: OK (py-clob-client not installed — expected)")
            else:
                log(f"  FAIL: {mod}: {err.strip()}")
                ok = False
        else:
            log(f"  {mod}: OK")
    return ok


def check_commands():
    """Run each CLI command and verify it doesn't crash."""
    log("Checking CLI commands...")
    commands = {
        "help":    f"{sys.executable} run.py --help",
        "markets": f"{sys.executable} run.py markets --limit 3",
        "search":  f"{sys.executable} run.py search test",
        "market":  f"{sys.executable} run.py market 1198423",
        "collect": f"{sys.executable} run.py collect",
        "backtest": f"{sys.executable} run.py backtest",
    }
    ok = True
    for name, cmd in commands.items():
        code, out, err = run(cmd, timeout=30)
        if code != 0:
            log(f"  FAIL: {name} (exit {code}): {err.strip()[:200]}")
            ok = False
        else:
            log(f"  {name}: OK")
    return ok


def check_tests():
    """Run pytest and report results."""
    log("Running tests...")
    code, out, err = run(f"{sys.executable} -m pytest tests/ -v --tb=short", timeout=60)
    combined = out + err
    # Extract summary line
    for line in combined.splitlines():
        if "passed" in line or "failed" in line or "error" in line:
            log(f"  {line.strip()}")
    return code == 0


def check_api():
    """Verify the Polymarket API is reachable and returns data."""
    log("Checking Polymarket API...")
    try:
        from src.polymarket_client import PolymarketClient
        client = PolymarketClient()
        client.clear_cache()
        markets = client.get_markets(limit=3)
        if not markets:
            log("  FAIL: API returned no markets")
            return False
        log(f"  API OK: {len(markets)} markets returned")
        return True
    except Exception as e:
        log(f"  FAIL: {e}")
        return False


def check_config():
    """Verify config files parse without errors."""
    log("Checking config files...")
    import yaml
    ok = True
    for cfg in ["config/config.yaml", "config/strategy.yaml"]:
        try:
            with open(ROOT / cfg) as f:
                yaml.safe_load(f)
            log(f"  {cfg}: OK")
        except Exception as e:
            log(f"  FAIL: {cfg}: {e}")
            ok = False
    return ok


# ---------------------------------------------------------------------------
# Auto-fixes
# ---------------------------------------------------------------------------

def fix_logs_dir():
    """Ensure logs/ directory exists."""
    logs_dir = ROOT / "logs"
    if not logs_dir.exists():
        logs_dir.mkdir(parents=True)
        log("  FIX: Created logs/ directory")
        FIXES.append("Created missing logs/ directory")


def fix_data_dir():
    """Ensure data/ directory exists."""
    data_dir = ROOT / "data"
    if not data_dir.exists():
        data_dir.mkdir(parents=True)
        log("  FIX: Created data/ directory")
        FIXES.append("Created missing data/ directory")


def fix_syntax_errors():
    """Check all .py files for syntax errors."""
    log("Checking syntax...")
    ok = True
    for pyfile in ROOT.rglob("*.py"):
        if ".git" in str(pyfile) or "__pycache__" in str(pyfile):
            continue
        code, out, err = run(f"{sys.executable} -m py_compile {pyfile}")
        if code != 0:
            log(f"  SYNTAX ERROR: {pyfile.relative_to(ROOT)}: {err.strip()[:200]}")
            ok = False
    if ok:
        log("  All .py files: OK")
    return ok


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def git_has_changes():
    """Check if there are uncommitted changes."""
    code, out, err = run("git status --porcelain")
    return bool(out.strip())


def git_commit_and_push(message):
    """Stage, commit, and push changes."""
    log("Committing and pushing...")
    code, out, err = run("git add -A")
    if code != 0:
        log(f"  git add failed: {err}")
        return False

    commit_msg = f"{message}\n\nCo-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
    code, out, err = run(f'git commit -m "{commit_msg}"')
    if code != 0:
        log(f"  git commit failed: {err}")
        return False
    log(f"  Committed: {message}")

    code, out, err = run("git push")
    if code != 0:
        log(f"  git push failed: {err}")
        return False
    log("  Pushed to origin")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry" in sys.argv

    log("=" * 60)
    log("WEEKLY MAINTENANCE START")
    log("=" * 60)

    # Ensure directories exist
    fix_logs_dir()
    fix_data_dir()

    # Run all checks
    results = {}
    results["config"] = check_config()
    results["syntax"] = fix_syntax_errors()
    results["imports"] = check_imports()
    results["api"] = check_api()
    results["commands"] = check_commands()
    results["tests"] = check_tests()

    # Summary
    log("")
    log("=" * 60)
    log("SUMMARY")
    log("=" * 60)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        log(f"  {name}: {status}")

    if FIXES:
        log(f"\nFixes applied: {len(FIXES)}")
        for fix in FIXES:
            log(f"  - {fix}")

    all_passed = all(results.values())
    if all_passed:
        log("\nAll checks passed.")
    else:
        failed = [k for k, v in results.items() if not v]
        log(f"\nFailed checks: {', '.join(failed)}")

    # Commit and push if there are changes
    if not dry_run and git_has_changes():
        date = datetime.now(tz=__import__('datetime').timezone.utc).strftime("%Y-%m-%d")
        if FIXES:
            msg = f"Maintenance ({date}): {'; '.join(FIXES)}"
        else:
            msg = f"Maintenance ({date}): routine check"
        git_commit_and_push(msg)
    elif dry_run:
        log("\nDry run — skipping commit/push.")
    else:
        log("\nNo changes to commit.")

    log("")
    log("WEEKLY MAINTENANCE COMPLETE")
    log("=" * 60)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
