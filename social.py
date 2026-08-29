"""
Social-signal layer for the Solana meme-coin screener.

WHY THIS MODULE EXISTS — the research basis
-------------------------------------------
Kim et al., "Pump.fun Graduation Regime Windows: Survival Analysis of 832,941
Token Launches and the Social-Presence Effect" (arXiv:2607.02823) ran a Cox
proportional-hazards model over 832,941 pump.fun launches and found:

  * Tokens WITH a Telegram channel graduate at  1.485%
  * Tokens WITHOUT one graduate at             0.166%
  * Lift 8.94x, Cox hazard ratio 5.40 (95% CI [4.73, 6.17])
  * Model concordance 0.858, stable under bootstrap

That makes verified Telegram presence the single strongest *social* predictor
measured at scale, which is why it carries the most weight below.

The same paper found initial market cap above the 30 SOL platform default is
the strongest single predictor overall (Cox HR 4.51).

IMPORTANT CAVEAT, stated honestly: "graduation" means reaching a real DEX.
It is NOT the same as profit. A token can graduate and still lose you money.
These weights raise the odds of picking a live token, not a winning trade.

WHAT IS ACTUALLY OBTAINABLE (tested live, not assumed)
------------------------------------------------------
  Telegram presence + subscriber count   FREE   scraped from t.me public page
  Telegram dead-link detection           FREE   fake handles render no markers
  Social presence (X/TG/site/discord)    FREE   DexScreener info.socials
  X/Twitter follower counts, account age PAID   X API v2 is pay-per-use since
                                                Feb 2026 ($0.005/read). Optional,
                                                OFF by default - see config.
  X/Twitter handle existence             NOT POSSIBLE FREE - x.com returns
                                                HTTP 200 for nonexistent handles
  Reddit mentions                        OAUTH  reddit.com/*.json returns 403
                                                without OAuth; new API clients
                                                need manual approval. Optional.
"""

import re
import time

import requests

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": BROWSER_UA})

# t.me renders the count as e.g.  tgme_page_extra">9 716 937 subscribers
TG_EXTRA = re.compile(r'tgme_page_extra">([^<]*)', re.I)
TG_MARKER = re.compile(r"tgme_page_(title|extra|description)", re.I)


def _digits(text):
    """'9 716 937 subscribers' -> 9716937  (handles non-breaking spaces)."""
    d = re.sub(r"[^0-9]", "", text or "")
    return int(d) if d else None


def parse_socials(pair):
    """Extract declared social links from a DexScreener pair object."""
    info = pair.get("info") or {}
    out = {
        "telegram_url": None, "twitter_url": None, "discord_url": None,
        "website_url": None, "declared": [],
    }
    for s in info.get("socials") or []:
        stype = str(s.get("type", "")).lower()
        url = s.get("url")
        if not url:
            continue
        out["declared"].append(stype)
        if stype == "telegram" and not out["telegram_url"]:
            out["telegram_url"] = url
        elif stype in ("twitter", "x") and not out["twitter_url"]:
            out["twitter_url"] = url
        elif stype == "discord" and not out["discord_url"]:
            out["discord_url"] = url
    sites = info.get("websites") or []
    if sites:
        out["website_url"] = sites[0].get("url") if isinstance(sites[0], dict) else sites[0]
    return out


def telegram_stats(url, timeout=15):
    """
    Live check of a Telegram link. Returns:
      exists  - True if t.me renders a real channel/group page
      members - subscriber count if the channel publishes one, else None
      label   - 'subscribers' / 'members' / None

    A declared-but-dead Telegram link is a deception signal, not a neutral one.
    """
    res = {"exists": False, "members": None, "label": None, "checked": False}
    if not url:
        return res
    m = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_+]+)", url)
    if not m:
        return res
    handle = m.group(1)
    if handle.startswith("+"):
        # private invite link - cannot be inspected; treat as present, unmeasurable
        res.update({"exists": True, "checked": True, "label": "private invite"})
        return res
    try:
        r = SESSION.get("https://t.me/" + handle, timeout=timeout)
        if r.status_code != 200:
            res["checked"] = True
            return res
        html = r.text
        res["checked"] = True
        res["exists"] = bool(TG_MARKER.search(html))
        for chunk in TG_EXTRA.findall(html):
            low = chunk.lower()
            if "subscriber" in low or "member" in low:
                res["members"] = _digits(chunk)
                res["label"] = "subscribers" if "subscriber" in low else "members"
                break
    except Exception:
        pass
    return res


def gather(pair, cfg_social=None):
    """Collect every free social signal for one token."""
    cfg_social = cfg_social or {}
    sig = parse_socials(pair)
    sig["telegram"] = telegram_stats(sig["telegram_url"]) if sig["telegram_url"] else \
        {"exists": False, "members": None, "label": None, "checked": False}
    if sig["telegram_url"]:
        time.sleep(0.4)  # be polite to t.me

    sig["has_telegram"] = bool(sig["telegram_url"]) and sig["telegram"]["exists"]
    sig["telegram_dead"] = bool(sig["telegram_url"]) and sig["telegram"]["checked"] \
        and not sig["telegram"]["exists"]
    sig["has_twitter"] = bool(sig["twitter_url"])
    sig["has_discord"] = bool(sig["discord_url"])
    sig["has_website"] = bool(sig["website_url"])
    sig["members"] = sig["telegram"]["members"]
    return sig


def member_tier_points(members, tiers):
    """Subscriber-count points. Bigger verified community = higher survival odds."""
    if members is None:
        return 0, "count not published"
    for lo, pts in tiers:
        if members >= lo:
            return pts, "{:,} subscribers".format(members)
    return 0, "{:,} subscribers".format(members)


def social_score(sig, weights):
    """
    0-100 social score. Returns (score, breakdown) where breakdown is a list of
    (component, points_awarded, max_points, evidence) so every point is auditable.
    """
    rows = []

    # Telegram presence - arXiv:2607.02823, Cox HR 5.40, 8.94x graduation lift
    if sig["has_telegram"]:
        rows.append(("Telegram channel live", weights["telegram_live"],
                     weights["telegram_live"], "verified on t.me"))
    else:
        rows.append(("Telegram channel live", 0, weights["telegram_live"],
                     "dead link" if sig["telegram_dead"] else "none declared"))

    # Telegram size
    if sig["has_telegram"]:
        pts, ev = member_tier_points(sig["members"], weights["member_tiers"])
    else:
        pts, ev = 0, "no live channel"
    rows.append(("Telegram community size", pts, weights["member_tiers"][0][1], ev))

    # X/Twitter - presence only; existence is NOT verifiable for free
    rows.append(("X/Twitter declared", weights["twitter"] if sig["has_twitter"] else 0,
                 weights["twitter"],
                 "link present (unverified - see README)" if sig["has_twitter"] else "none"))

    rows.append(("Website", weights["website"] if sig["has_website"] else 0,
                 weights["website"], "present" if sig["has_website"] else "none"))

    rows.append(("Discord", weights["discord"] if sig["has_discord"] else 0,
                 weights["discord"], "present" if sig["has_discord"] else "none"))

    total = sum(r[1] for r in rows)
    return round(min(100.0, total), 1), rows


def social_gates(sig, gates):
    """
    Hard social gates. Returns list of (label, passed, evidence).
    Kept deliberately small - only checks that are verifiable and meaningful.
    """
    C = []
    if gates.get("reject_dead_telegram", True):
        C.append(("Telegram link not dead", not sig["telegram_dead"],
                  "declared but does not exist - deception signal"
                  if sig["telegram_dead"] else "ok"))
    if gates.get("require_live_telegram", False):
        C.append(("Live Telegram channel", sig["has_telegram"],
                  "verified" if sig["has_telegram"] else "no live channel"))
    min_members = gates.get("min_telegram_members", 0)
    if min_members and sig["has_telegram"]:
        m = sig["members"]
        # private invite links publish no count - do not punish them
        ok = (m is None) or (m >= min_members)
        C.append(("Telegram >= {} members".format(min_members), ok,
                  "{:,}".format(m) if m is not None else "count not public (allowed)"))
    return C
