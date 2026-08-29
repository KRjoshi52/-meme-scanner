"""
What the cloud runner is doing, read from GitHub's public API.

The laptop and GitHub Actions each keep their own scan history, so a status
reply from one knows nothing about the other unless it asks. This asks.
No token needed - the repository is public.
"""

import json
from datetime import datetime, timezone

import requests


def _ago(dt):
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 90:
        return str(secs) + "s ago"
    if secs < 5400:
        return str(round(secs / 60)) + " min ago"
    if secs < 172800:
        return str(round(secs / 3600, 1)) + " hours ago"
    return str(round(secs / 86400, 1)) + " days ago"


def last_run(repo, timeout=15):
    """(text, ok) for the most recent workflow run, or (reason, None) if unknown."""
    if not repo:
        return "not configured", None
    try:
        r = requests.get("https://api.github.com/repos/" + repo + "/actions/runs?per_page=1",
                         timeout=timeout, headers={"Accept": "application/vnd.github+json"})
        data = r.json()
    except (requests.RequestException, ValueError):
        return "could not reach GitHub", None

    if "workflow_runs" not in data:
        return str(data.get("message", "unavailable")), None
    runs = data["workflow_runs"]
    if not runs:
        return "no runs yet", None

    run = runs[0]
    when = run.get("run_started_at") or run.get("created_at")
    try:
        dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
        stamp = _ago(dt)
    except (AttributeError, ValueError):
        stamp = str(when)

    if run.get("status") != "completed":
        return "running now (started " + stamp + ")", True
    conclusion = run.get("conclusion") or "?"
    return conclusion + ", " + stamp, conclusion == "success"
