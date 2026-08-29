"""
Is the scanner actually running?

    python status.py

Answers from evidence, not from a window being open: the heartbeat the scanner
writes after every scan, the scheduled task's own record, and the log tail.
"""

import io
import json
import os
import re
import cloud
import creds
import subprocess
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
LOG = os.path.join(HERE, "scanner.log")
CONFIG = os.path.join(HERE, "config.json")
TASK = "MemeScanner"

BAR = "-" * 52


def ago(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return str(seconds) + " seconds ago"
    if seconds < 5400:
        return str(round(seconds / 60)) + " minutes ago"
    if seconds < 172800:
        return str(round(seconds / 3600, 1)) + " hours ago"
    return str(round(seconds / 86400, 1)) + " days ago"


def load(path, default):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def task_info():
    """Ask Windows what it knows about the scheduled task."""
    try:
        out = subprocess.run(["schtasks", "/query", "/tn", TASK, "/fo", "LIST", "/v"],
                             capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    info = {}
    for line in out.stdout.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            info[k.strip().lower()] = v.strip()
    for line in out.stdout.splitlines():
        if line.lower().startswith("repeat: every"):
            info["interval"] = line.split(":", 2)[-1].strip()
            break
    return info


def main():
    print("\n" + BAR)
    print("  MEME SCANNER STATUS   " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(BAR)

    # ---------------------------------------------------------- heartbeat
    state = load(STATE, {})
    last = state.get("last_scan")

    if not last:
        print("  scans        never run yet")
        verdict = "NOT RUNNING - no scan has ever completed"
    else:
        gap = time.time() - last
        print("  last scan    " + str(state.get("last_scan_human", "?")) + "   (" + ago(gap) + ")")
        print("  that scan    " + str(state.get("last_candidates", "?")) + " candidates, "
              + str(state.get("last_passed", "?")) + " passed all gates")
        print("  totals       " + str(state.get("scans_total", 0)) + " scans, "
              + str(state.get("alerts_total", 0)) + " alerts sent")
        if gap < 1500:            # 25 min - one missed 15-min slot is still fine
            verdict = "RUNNING"
        elif gap < 7200:
            verdict = "STALLED - no scan in " + ago(gap)
        else:
            verdict = "STOPPED - last scan was " + ago(gap)

    cooling = state.get("alerted") or {}
    if cooling:
        print("  cooldown     " + str(len(cooling)) + " token(s) muted for 24h "
              + "(won't alert twice)")

    # ---------------------------------------------------------- settings
    cfg = load(CONFIG, {})
    creds.apply(cfg)
    comp = cfg.get("composite", {})
    tok = (cfg.get("telegram") or {}).get("bot_token", "")
    print("  telegram     " + ("connected" if tok and "PASTE" not in tok else "NOT CONFIGURED"))
    if comp:
        print("  thresholds   safety floor " + str(cfg.get("min_safety_score", "-"))
              + ", composite " + str(cfg.get("min_score_to_alert", "-"))
              + "  (" + str(int(comp.get("safety_weight", 0) * 100)) + "% safety / "
              + str(int(comp.get("social_weight", 0) * 100)) + "% social)")

    # ---------------------------------------------------------- scheduler
    text, ok = cloud.last_run(cfg.get("github_repo"))
    print(BAR)
    print("  cloud        GitHub Actions: " + text
          + ("" if ok is not False else "   <- failing, check the Actions tab"))

    info = task_info()
    if info is None:
        print("  scheduled    NOT INSTALLED - it only runs when you run it by hand")
        print("               install:  schtasks /create /tn \"" + TASK + "\" /tr "
              + "\"" + os.path.join(HERE, "run_hidden.vbs") + "\" /sc minute /mo 15 /f")
    else:
        every = info.get("interval", "")
        print("  scheduled    " + info.get("status", "?")
              + ("   every " + every if every else ""))
        print("  last run     " + info.get("last run time", "?")
              + "   result " + info.get("last result", "?"))
        print("  next run     " + info.get("next run time", "?"))

    # ---------------------------------------------------------- log tail
    if os.path.exists(LOG):
        try:
            lines = [l.rstrip() for l in io.open(LOG, encoding="utf-8", errors="replace") if l.strip()]
        except OSError:
            lines = []
        if lines:
            print(BAR)
            print("  last lines of scanner.log:")
            for l in lines[-4:]:
                print("    " + l[:96])

    print(BAR)
    print("  " + verdict)
    print(BAR + "\n")


if __name__ == "__main__":
    main()
