"""
One-shot Telegram wiring for the scanner.

    python setup_telegram.py 8123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxx
    python setup_telegram.py --token-file token.txt
    python setup_telegram.py <token> --chat-id -1001234567890

It verifies the token, finds your chat id for you, writes both into secrets.json
and sends a test message. The token is never printed back - only a masked form -
and secrets.json is git-ignored, so it never leaves this machine.
"""

import argparse
import io
import json
import os
import re
import sys
from collections import OrderedDict

import requests

import creds

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
API = "https://api.telegram.org/bot"

TOKEN_SHAPE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


def mask(token):
    """8123456789:AAH... -> 8123...gT4c  (enough to recognise, useless to steal)."""
    if len(token) < 12:
        return "****"
    return token[:4] + "..." + token[-4:]


def fail(msg, *hints):
    print("\n  FAILED  " + msg)
    for h in hints:
        print("          " + h)
    sys.exit(1)


def call(token, method, payload=None, timeout=20):
    try:
        r = requests.post(API + token + "/" + method, json=payload or {}, timeout=timeout)
    except requests.RequestException as e:
        fail("could not reach api.telegram.org (" + e.__class__.__name__ + ")",
             "Check your internet connection, then run this again.")
    try:
        data = r.json()
    except ValueError:
        fail("Telegram returned something that is not JSON (HTTP " + str(r.status_code) + ")")
    return data


def chats_from_updates(updates):
    """Every distinct chat that has spoken to this bot recently."""
    found = OrderedDict()
    for u in updates:
        for key in ("message", "edited_message", "channel_post", "my_chat_member", "callback_query"):
            item = u.get(key) or {}
            chat = item.get("chat") or (item.get("message") or {}).get("chat")
            if not chat or not chat.get("id"):
                continue
            cid = str(chat["id"])
            if cid not in found:
                name = chat.get("title") or " ".join(
                    x for x in (chat.get("first_name"), chat.get("last_name")) if x
                ) or chat.get("username") or "(unnamed)"
                found[cid] = {"id": cid, "name": name, "type": chat.get("type", "?")}
    return list(found.values())


def main():
    ap = argparse.ArgumentParser(description="Wire the scanner up to your Telegram bot")
    ap.add_argument("token", nargs="?", help="bot token from @BotFather")
    ap.add_argument("--token-file", help="read the token from this file instead")
    ap.add_argument("--chat-id", help="skip auto-detection and use this chat id")
    args = ap.parse_args()

    # ------------------------------------------------------------ token
    token = args.token
    if args.token_file:
        try:
            token = io.open(args.token_file, encoding="utf-8").read()
        except OSError as e:
            fail("could not read " + args.token_file + " (" + e.strerror + ")")
    if not token:
        fail("no token given",
             "Run:  python setup_telegram.py <the token @BotFather gave you>")

    token = token.strip().strip('"').strip("'")
    if token.lower().startswith("bot"):
        token = token[3:]
    if not TOKEN_SHAPE.match(token):
        fail("that does not look like a bot token",
             "It should look like  8123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
             "Open @BotFather, send /mybots, pick your bot, then 'API Token'.")

    print("\nSetting up Telegram for the meme scanner")
    print("-" * 46)

    # ------------------------------------------------------------ verify
    me = call(token, "getMe")
    if not me.get("ok"):
        fail("Telegram rejected the token: " + str(me.get("description")),
             "Get a fresh one from @BotFather with /mybots, or /revoke to reissue.")
    bot = me["result"]
    print("  bot        @" + str(bot.get("username")) + "  (" + str(bot.get("first_name")) + ")")
    print("  token      " + mask(token) + "  verified")

    # ------------------------------------------------------------ chat id
    if args.chat_id:
        chat_id = str(args.chat_id).strip()
        print("  chat       " + chat_id + "  (given on the command line)")
    else:
        upd = call(token, "getUpdates", {"timeout": 0, "limit": 100})
        if not upd.get("ok"):
            fail("getUpdates failed: " + str(upd.get("description")))
        chats = chats_from_updates(upd.get("result") or [])

        if not chats:
            fail("your bot has not received any message yet",
                 "Open Telegram, find @" + str(bot.get("username")) + ", and send it any message",
                 "(a plain 'hi' is enough). Then run this command again.",
                 "For a group: add the bot to the group and send a message there.")
        if len(chats) > 1:
            print("\n  More than one chat has messaged this bot:")
            for c in chats:
                print("    " + c["id"].ljust(16) + c["name"] + "  (" + c["type"] + ")")
            fail("cannot choose for you",
                 "Re-run with the one you want, e.g.:",
                 "  python setup_telegram.py <token> --chat-id " + chats[0]["id"])

        c = chats[0]
        chat_id = c["id"]
        print("  chat       " + chat_id + "  " + c["name"] + " (" + c["type"] + ")")

    # ------------------------------------------------------------ credentials
    creds.save_secrets(token, chat_id)
    print("  config     secrets.json written (git-ignored, stays on this machine)")

    # ------------------------------------------------------------ test send
    sent = call(token, "sendMessage", {
        "chat_id": chat_id,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "text": ("<b>Scanner connected.</b>\nAlerts will arrive here with the contract "
                 "address and the full proof behind every check.\n\n<i>Screener output, "
                 "not financial advice.</i>"),
    })
    if not sent.get("ok"):
        fail("test message rejected: " + str(sent.get("description")),
             "'chat not found' - wrong chat id, or you have not messaged the bot.",
             "'bot was blocked by the user' - unblock the bot in Telegram.")

    print("  test       message sent")
    print("-" * 46)
    print("Done. Check Telegram - you should have a message from @"
          + str(bot.get("username")) + ".\n")
    print("Next:")
    print("  python scanner.py --dry-run --verbose   see a scan, send nothing")
    print("  python scanner.py                       one real scan, sends alerts")
    print("  python scanner.py --loop                keep scanning every 15 min\n")


if __name__ == "__main__":
    main()
