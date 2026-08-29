"""
Where the Telegram credentials come from, in order of precedence:

  1. environment  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID   (GitHub Actions)
  2. secrets.json next to this file                        (this machine)
  3. config.json                                           (legacy)

config.json is safe to commit and push because the token is not in it.
secrets.json is git-ignored and never leaves the machine.
"""

import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "secrets.json")


def load_secrets():
    try:
        return json.load(io.open(SECRETS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_secrets(token, chat_id):
    io.open(SECRETS, "w", encoding="utf-8").write(
        json.dumps({"telegram": {"bot_token": token, "chat_id": str(chat_id)}}, indent=2) + "\n")
    return SECRETS


def apply(cfg):
    """Fill cfg['telegram'] from the best source available. Returns cfg."""
    tg = dict(cfg.get("telegram") or {})

    secret_tg = (load_secrets().get("telegram") or {})
    for key in ("bot_token", "chat_id"):
        if secret_tg.get(key):
            tg[key] = secret_tg[key]

    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    env_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if env_token:
        tg["bot_token"] = env_token.strip()
    if env_chat:
        tg["chat_id"] = env_chat.strip()

    cfg["telegram"] = tg
    return cfg


def configured(cfg):
    tg = cfg.get("telegram") or {}
    tok, chat = str(tg.get("bot_token", "")), str(tg.get("chat_id", ""))
    return bool(tok) and bool(chat) and "PASTE" not in tok and "PASTE" not in chat
