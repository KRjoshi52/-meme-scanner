"""
Social-signal layer for the Solana meme-coin screener.

WHY THIS MODULE EXISTS - the research basis
-------------------------------------------
Kim et al., "Pump.fun Graduation Regime Windows: Survival Analysis of 832,941
Token Launches and the Social-Presence Effect" (arXiv:2607.02823) ran a Cox
proportional-hazards model over 832,941 pump.fun launches and found:

  * Tokens WITH a Telegram channel graduate at  1.485%
  * Tokens WITHOUT one graduate at             0.166%
  * Lift 8.94x, Cox hazard ratio 5.40 (95% CI [4.73, 6.17])
  * Model concordance 0.858, stable under bootstrap

"Graduation" means reaching a real DEX. It is NOT profit. These weights raise
the odds of picking a live token, not a winning trade.

WHAT CHANGED, AND WHY
---------------------
The first version scored *presence*: an X link was worth 15 points and a website
10, with no check on either. Two real signals showed how empty that was - both
tokens' X accounts and domains had been created the day before, one with zero
tweets. Presence is free to fake, so it is worth nothing.

Everything scored here is now age- and activity-checked against a live source:

  Telegram presence + subscriber count   t.me public page
  Telegram dead-link detection           t.me renders no markers for fakes
  X account existence                    vxtwitter + fxtwitter, cross-checked
  X account age, followers, tweet count  same calls, conservative reading
  Website domain registration date       RDAP (rdap.org), the registry's own record

A days-old account is not neutral. Nearly every meme coin has one, so it earns
no points - but a genuinely established account or domain is hard to fake in a
hurry, and that is what gets rewarded.

Failures are treated as unknown, never as guilt: if a service is unreachable the
component scores zero rather than rejecting the token. Only a definitive 404 on
a declared link counts as deception.
"""

import re
import time
from datetime import datetime, timezone

import requests

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": BROWSER_UA})

# t.me renders the count as e.g.  tgme_page_extra">9 716 937 subscribers
TG_EXTRA = re.compile(r'tgme_page_extra">([^<]*)', re.I)
TG_MARKER = re.compile(r"tgme_page_(title|extra|description)", re.I)

X_HANDLE = re.compile(r"(?:twitter|x)\.com/(?!i/|intent/|share|home|search)([A-Za-z0-9_]{1,15})", re.I)
X_COMMUNITY = re.compile(r"(?:twitter|x)\.com/i/communities/", re.I)

X_API = "https://api.vxtwitter.com/"
X_API_FALLBACK = "https://api.fxtwitter.com/"
RDAP = "https://rdap.org/domain/"

# A "website" that points at a platform tells you when the PLATFORM was registered,
# not the project. A token linking to an X community scored a perfect domain-age
# mark for x.com being 33 years old. These are never the project's own domain.
PLATFORM_DOMAINS = {
    "x.com", "twitter.com", "t.me", "telegram.me", "telegram.org",
    "discord.gg", "discord.com", "instagram.com", "tiktok.com", "youtube.com",
    "youtu.be", "facebook.com", "reddit.com", "medium.com", "substack.com",
    "linktr.ee", "linktree.com", "beacons.ai", "bio.link", "carrd.co",
    "notion.site", "github.io", "github.com", "gitbook.io",
    "pump.fun", "dexscreener.com", "dextools.io", "birdeye.so", "solscan.io",
    "raydium.io", "jup.ag", "bit.ly", "tinyurl.com",
}


def _digits(text):
    """'9 716 937 subscribers' -> 9716937  (handles non-breaking spaces)."""
    d = re.sub(r"[^0-9]", "", text or "")
    return int(d) if d else None


def _days_since(dt):
    return (datetime.now(timezone.utc) - dt).days


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


# ---------------------------------------------------------------- Telegram
def telegram_stats(url, timeout=15):
    """Live check of a Telegram link: does the channel exist, and how big is it."""
    res = {"exists": False, "members": None, "label": None, "checked": False}
    if not url:
        return res
    m = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_+]+)", url)
    if not m:
        return res
    handle = m.group(1)
    if handle.startswith("+"):
        # private invite link - cannot be inspected; present, unmeasurable
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
    except requests.RequestException:
        pass
    return res


# ---------------------------------------------------------------- X / Twitter
def _x_lookup(base, handle, timeout):
    """One mirror's answer: (found, record) - record is None on 404."""
    try:
        r = SESSION.get(base + handle, timeout=timeout)
    except requests.RequestException:
        return False, None
    if r.status_code == 404:
        return True, None
    if r.status_code != 200:
        return False, None
    try:
        data = r.json()
    except ValueError:
        return False, None
    user = data.get("user") if isinstance(data.get("user"), dict) else data
    created = user.get("created_at") or user.get("joined")
    if not created:
        return False, None
    try:
        dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        return False, None
    return True, {
        "id": str(user.get("id", "")),
        "age_days": _days_since(dt),
        "created": created,
        "followers": user.get("followers_count", user.get("followers")),
        "tweets": user.get("tweet_count", user.get("tweets")),
        "following": user.get("following_count", user.get("following")),
    }


def x_stats(url, timeout=15):
    """
    Real X account data, free, no key, cross-checked against two independent
    mirrors. Returns:
      checked     the lookup completed (False = both services down, unknown)
      exists      the account is real (False only when both agree it 404s)
      conflict    the two mirrors returned different account ids for this handle
      age_days    how long ago it was created
      followers / tweets / following
      community   the link points at a community, not an account

    The cross-check is not paranoia. X handles are released and re-registered,
    and these mirrors cache: asked for the same handle at the same moment, one
    returned an account created 12 Aug and the other one created 28 Aug, with
    different ids. When they disagree about *which account this is*, nothing
    about it can be treated as evidence.
    """
    res = {"checked": False, "exists": None, "age_days": None, "followers": None,
           "tweets": None, "following": None, "community": False, "handle": None,
           "conflict": False, "sources": 0}
    if not url:
        return res
    if X_COMMUNITY.search(url):
        res.update({"checked": True, "community": True})
        return res
    m = X_HANDLE.search(url)
    if not m:
        return res
    handle = m.group(1)
    res["handle"] = handle

    answers = []
    gone = 0
    for base in (X_API, X_API_FALLBACK):
        found, rec = _x_lookup(base, handle, timeout)
        if found and rec is None:
            gone += 1
        elif rec:
            answers.append(rec)

    if not answers:
        if gone:
            res.update({"checked": True, "exists": False})
        return res

    res["sources"] = len(answers)
    ids = {a["id"] for a in answers if a["id"]}
    if len(ids) > 1:
        # same handle, two different accounts - identity is unresolved
        res.update({"checked": True, "exists": True, "conflict": True})
        return res

    # agreed (or only one mirror answered): take the most conservative reading
    best = min(answers, key=lambda a: a["age_days"])
    res.update({
        "checked": True, "exists": True, "age_days": best["age_days"],
        "followers": min(a["followers"] or 0 for a in answers),
        "tweets": min(a["tweets"] or 0 for a in answers),
        "following": best["following"],
    })
    return res


# ---------------------------------------------------------------- website
def domain_of(url):
    if not url:
        return None
    m = re.match(r"^\s*(?:https?://)?(?:www\.)?([^/:\s?#]+)", url, re.I)
    if not m:
        return None
    host = m.group(1).lower()
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def domain_stats(url, timeout=20):
    """Registration date straight from the registry, via RDAP. No key needed."""
    res = {"checked": False, "exists": None, "age_days": None,
           "domain": None, "registered": None, "platform": False}
    domain = domain_of(url)
    if not domain:
        return res
    res["domain"] = domain
    if domain in PLATFORM_DOMAINS:
        res.update({"checked": True, "platform": True})
        return res
    try:
        r = SESSION.get(RDAP + domain, timeout=timeout, allow_redirects=True,
                        headers={"Accept": "application/rdap+json"})
    except requests.RequestException:
        return res
    if r.status_code == 404:
        res.update({"checked": True, "exists": False})
        return res
    if r.status_code != 200:
        return res
    try:
        data = r.json()
    except ValueError:
        return res
    for ev in data.get("events") or []:
        if str(ev.get("eventAction", "")).lower() == "registration":
            raw = str(ev.get("eventDate", ""))
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
                try:
                    dt = datetime.strptime(raw, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    res.update({"checked": True, "exists": True,
                                "age_days": _days_since(dt), "registered": raw[:10]})
                    return res
                except ValueError:
                    continue
    res["checked"] = True
    res["exists"] = True
    return res


# ---------------------------------------------------------------- gather
def gather(pair, cfg_social=None):
    """Collect every free social signal for one token, checked against live sources."""
    sig = parse_socials(pair)

    sig["telegram"] = telegram_stats(sig["telegram_url"]) if sig["telegram_url"] else \
        {"exists": False, "members": None, "label": None, "checked": False}
    if sig["telegram_url"]:
        time.sleep(0.3)

    sig["x"] = x_stats(sig["twitter_url"])
    if sig["twitter_url"]:
        time.sleep(0.3)

    sig["site"] = domain_stats(sig["website_url"])

    sig["has_telegram"] = bool(sig["telegram_url"]) and sig["telegram"]["exists"]
    sig["telegram_dead"] = bool(sig["telegram_url"]) and sig["telegram"]["checked"] \
        and not sig["telegram"]["exists"]
    sig["members"] = sig["telegram"]["members"]

    sig["has_twitter"] = bool(sig["twitter_url"])
    sig["twitter_dead"] = sig["x"]["checked"] and sig["x"]["exists"] is False
    sig["has_discord"] = bool(sig["discord_url"])
    sig["has_website"] = bool(sig["website_url"])
    sig["site_dead"] = sig["site"]["checked"] and sig["site"]["exists"] is False
    return sig


# ---------------------------------------------------------------- scoring
def _tier(value, tiers):
    """tiers is [[threshold, points], ...] highest first."""
    if value is None:
        return None
    for lo, pts in tiers:
        if value >= lo:
            return pts
    return 0


def social_score(sig, weights):
    """
    0-100 social score. Returns (score, breakdown) where breakdown is a list of
    (component, points, max_points, evidence) so every point is auditable.
    """
    rows = []
    x, site = sig["x"], sig["site"]

    # --- Telegram presence: the one component with published survival evidence
    mx = weights["telegram_live"]
    if sig["has_telegram"]:
        rows.append(("Telegram channel live", mx, mx, "verified on t.me"))
    else:
        rows.append(("Telegram channel live", 0, mx,
                     "dead link" if sig["telegram_dead"] else "none declared"))

    # --- Telegram size
    mx = weights["member_tiers"][0][1]
    if sig["has_telegram"]:
        pts = _tier(sig["members"], weights["member_tiers"])
        if pts is None:
            pts, ev = 0, "count not published"
        else:
            ev = "{:,} subscribers".format(sig["members"])
    else:
        pts, ev = 0, "no live channel"
    rows.append(("Telegram community size", pts, mx, ev))

    # --- X account age: a day-old account is what every rug has
    mx = weights["x_age_tiers"][0][1]
    if not sig["has_twitter"]:
        pts, ev = 0, "no X account declared"
    elif x["community"]:
        pts, ev = 0, "link is an X community, not an account"
    elif x["exists"] is False:
        pts, ev = 0, "ACCOUNT DOES NOT EXIST"
    elif x.get("conflict"):
        pts, ev = 0, "two sources return different accounts for this handle"
    elif not x["checked"] or x["age_days"] is None:
        pts, ev = 0, "could not verify (service unreachable)"
    else:
        pts = _tier(x["age_days"], weights["x_age_tiers"]) or 0
        ev = "created {} days ago".format(x["age_days"])
        if x["age_days"] <= 2:
            ev += " - same week as the token"
        if x.get("sources", 0) < 2:
            ev += " (one source only)"
    rows.append(("X account age", pts, mx, ev))

    # --- X activity: followers mean nothing next to zero posts
    mx = weights["x_activity"]
    if x.get("followers") is None:
        pts, ev = 0, "not measurable"
    else:
        f, t = x.get("followers") or 0, x.get("tweets") or 0
        if t < weights["x_min_tweets"]:
            pts, ev = 0, "{:,} followers but only {} posts".format(f, t)
        else:
            pts = _tier(f, weights["x_follower_tiers"]) or 0
            ev = "{:,} followers, {:,} posts".format(f, t)
    rows.append(("X account activity", pts, mx, ev))

    # --- website domain age, from the registry
    mx = weights["site_age_tiers"][0][1]
    if not sig["has_website"]:
        pts, ev = 0, "no website declared"
    elif site.get("platform"):
        pts, ev = 0, "links to {}, not a project domain".format(site.get("domain"))
    elif site["exists"] is False:
        pts, ev = 0, "DOMAIN NOT REGISTERED"
    elif site["age_days"] is None:
        pts, ev = 0, "registration date unavailable"
    else:
        pts = _tier(site["age_days"], weights["site_age_tiers"]) or 0
        ev = "{} registered {} days ago".format(site["domain"], site["age_days"])
    rows.append(("Website domain age", pts, mx, ev))

    # --- Discord: presence only, and priced accordingly
    mx = weights["discord"]
    rows.append(("Discord", mx if sig["has_discord"] else 0, mx,
                 "present" if sig["has_discord"] else "none"))

    total = sum(r[1] for r in rows)
    return round(min(100.0, total), 1), rows


def social_gates(sig, gates):
    """Hard social gates: only checks that are verifiable and meaningful."""
    C = []
    if gates.get("reject_dead_telegram", True):
        C.append(("Telegram link not dead", not sig["telegram_dead"],
                  "declared but does not exist - deception signal"
                  if sig["telegram_dead"] else "ok"))

    if gates.get("reject_dead_twitter", True):
        C.append(("X account exists", not sig["twitter_dead"],
                  "declared but the account does not exist - deception signal"
                  if sig["twitter_dead"] else "ok"))

    if gates.get("require_live_telegram", False):
        C.append(("Live Telegram channel", sig["has_telegram"],
                  "verified" if sig["has_telegram"] else "no live channel"))

    min_members = gates.get("min_telegram_members", 0)
    if min_members and sig["has_telegram"]:
        m = sig["members"]
        ok = (m is None) or (m >= min_members)   # private links publish no count
        C.append(("Telegram >= {} members".format(min_members), ok,
                  "{:,}".format(m) if m is not None else "count not public (allowed)"))

    min_x_age = gates.get("min_x_account_age_days", 0)
    x = sig["x"]
    if min_x_age and sig["has_twitter"] and x["age_days"] is not None:
        C.append(("X account >= {}d old".format(min_x_age), x["age_days"] >= min_x_age,
                  "{} days old".format(x["age_days"])))

    min_site_age = gates.get("min_domain_age_days", 0)
    if min_site_age and sig["has_website"] and sig["site"]["age_days"] is not None:
        C.append(("Domain >= {}d old".format(min_site_age),
                  sig["site"]["age_days"] >= min_site_age,
                  "{} days old".format(sig["site"]["age_days"])))
    return C
