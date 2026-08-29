"""
Answers commands you send to the bot from Telegram.

    python bot_commands.py

Long-polls Telegram and replies to:

    /status   is it running, when was the last scan, what has it sent
    /scan     run a scan right now and report what it found
    /last     resend the last signal card
    /pause    stop the 15-minute scheduled task
    /resume   start it again
    /help     this list

Only messages from the configured chat_id are answered - anyone else who finds
the bot gets nothing back.
"""

import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime

import requests

import creds

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
STATE = os.path.join(HERE, "state.json")
CARD = os.path.join(HERE, "last_card.png")
TASK = "MemeScanner"
API = "https://api.telegram.org/bot"

SESSION = requests.Session()


def log(m):
    print("[" + datetime.now().strftime("%H:%M:%S") + "] " + str(m), flush=True)


def load(path, default):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def ago(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return str(seconds) + "s ago"
    if seconds < 5400:
        return str(round(seconds / 60)) + " min ago"
    if seconds < 172800:
        return str(round(seconds / 3600, 1)) + " hours ago"
    return str(round(seconds / 86400, 1)) + " days ago"


def api(token, method, **kw):
    try:
        r = SESSION.post(API + token + "/" + method, timeout=kw.pop("timeout", 40), **kw)
        return r.json()
    except Exception as e:
        log("api " + method + " failed: " + str(e))
        return {"ok": False}


def say(token, chat, text):
    return api(token, "sendMessage", json={"chat_id": chat, "text": text,
                                           "parse_mode": "HTML",
                                           "disable_web_page_preview": True})


def task_line():
    try:
        out = subprocess.run(["schtasks", "/query", "/tn", TASK, "/fo", "LIST", "/v"],
                             capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return "scheduler: unavailable"
    if out.returncode != 0:
        return "scheduler: <b>not installed</b>"
    info = {}
    for line in out.stdout.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            info.setdefault(k.strip().lower(), v.strip())
    return ("scheduler: <b>" + info.get("status", "?") + "</b>, next run "
            + info.get("next run time", "?"))


def status_text():
    st = load(STATE, {})
    cfg = load(CONFIG, {})
    last = st.get("last_scan")
    L = ["<b>Scanner status</b>"]
    if not last:
        L.append("No scan has completed yet.")
    else:
        gap = time.time() - last
        state_word = "RUNNING" if gap < 1500 else ("STALLED" if gap < 7200 else "STOPPED")
        L.append("state: <b>" + state_word + "</b>")
        L.append("last scan: " + str(st.get("last_scan_human", "?")) + "  (" + ago(gap) + ")")
        L.append("that scan: " + str(st.get("last_candidates", "?")) + " candidates, "
                 + str(st.get("last_passed", "?")) + " passed all gates")
        L.append("totals: " + str(st.get("scans_total", 0)) + " scans, "
                 + str(st.get("alerts_total", 0)) + " alerts")
    muted = st.get("alerted") or {}
    if muted:
        L.append("cooldown: " + str(len(muted)) + " token(s) muted 24h")
    comp = cfg.get("composite", {})
    if comp:
        L.append("thresholds: safety floor " + str(cfg.get("min_safety_score", "-"))
                 + ", composite " + str(cfg.get("min_score_to_alert", "-"))
                 + "  (" + str(int(comp.get("safety_weight", 0) * 100)) + "/"
                 + str(int(comp.get("social_weight", 0) * 100)) + ")")
    L.append(task_line())
    return "\n".join(L)


def run_scan(token, chat):
    say(token, chat, "Scanning now - about 30 seconds.")
    try:
        out = subprocess.run([sys.executable, os.path.join(HERE, "scanner.py")],
                             capture_output=True, text=True, timeout=600, cwd=HERE)
    except subprocess.SubprocessError as e:
        return say(token, chat, "Scan failed to start: " + str(e))
    tail = [l for l in (out.stdout or "").splitlines() if l.strip()][-3:]
    say(token, chat, "<b>Scan finished</b>\n<pre>" + "\n".join(tail) + "</pre>")


def task_control(enable):
    flag = "/enable" if enable else "/disable"
    try:
        out = subprocess.run(["schtasks", "/change", "/tn", TASK, flag],
                             capture_output=True, text=True, timeout=25)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


HELP = ("<b>Commands</b>\n"
        "/status - is it running, what has it done\n"
        "/scan - run a scan right now\n"
        "/last - resend the last signal card\n"
        "/pause - stop the 15-minute schedule\n"
        "/resume - start it again\n"
        "/help - this list")


def handle(token, chat, text):
    cmd = text.strip().lower().split("@")[0].lstrip("/")
    if cmd in ("status", "s"):
        say(token, chat, status_text())
    elif cmd == "scan":
        run_scan(token, chat)
    elif cmd == "last":
        if os.path.exists(CARD):
            with open(CARD, "rb") as fh:
                api(token, "sendPhoto", data={"chat_id": chat, "caption": "Last signal card"},
                    files={"photo": ("card.png", fh, "image/png")}, timeout=60)
        else:
            say(token, chat, "No card yet - nothing has alerted since this was installed.")
    elif cmd == "pause":
        say(token, chat, "Schedule paused." if task_control(False)
            else "Could not pause - is the task installed?")
    elif cmd == "resume":
        say(token, chat, "Schedule resumed." if task_control(True)
            else "Could not resume - is the task installed?")
    elif cmd in ("help", "start"):
        say(token, chat, HELP)
    else:
        say(token, chat, "Unknown command.\n\n" + HELP)


def main():
    cfg = load(CONFIG, None)
    if not cfg:
        sys.exit("config.json missing")
    creds.apply(cfg)
    token = cfg["telegram"]["bot_token"]
    owner = str(cfg["telegram"]["chat_id"])
    if "PASTE" in token:
        sys.exit("Telegram not configured yet - run setup_telegram.py first")

    me = api(token, "getMe", json={})
    if not me.get("ok"):
        sys.exit("Telegram rejected the token")
    log("Listening as @" + str(me["result"].get("username")) + " for chat " + owner)

    offset = None
    while True:
        payload = {"timeout": 50, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        res = api(token, "getUpdates", json=payload, timeout=70)
        if not res.get("ok"):
            time.sleep(5)
            continue
        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = msg.get("text") or ""
            if not text:
                continue
            if chat != owner:
                log("ignored message from chat " + chat)
                continue
            log("command: " + text[:40])
            try:
                handle(token, chat, text)
            except Exception as e:
                log("handler error: " + str(e))
                say(token, chat, "That command hit an error: " + str(e))


if __name__ == "__main__":
    main()
