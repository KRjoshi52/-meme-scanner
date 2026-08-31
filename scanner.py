"""
Solana meme-coin screener -> Telegram alerts (Phantom-ready).

Pipeline:  discover -> enrich -> HARD GATES -> score -> alert

Every number in an alert is fetched live and linked back to a public source,
so you can verify each claim yourself before you touch a buy button.

Data sources (all free, no API key required):
  DexScreener  https://api.dexscreener.com            discovery + market data
  RugCheck     https://api.rugcheck.xyz               holders, LP lock, risk flags
  Solana RPC   https://api.mainnet-beta.solana.com    mint/freeze authority (on-chain truth)
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime

import requests

import card
import creds
import social
import track

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")

DEX = "https://api.dexscreener.com"
GECKO = "https://api.geckoterminal.com/api/v2/networks/solana"
RUGCHECK = "https://api.rugcheck.xyz/v1"
RPC = "https://api.mainnet-beta.solana.com"

# RugCheck knownAccounts types that are NOT real holders (pools, lockers, exchanges).
NON_HOLDER_TYPES = {"AMM", "LOCKER", "MARKETPLACE", "EXCHANGE", "PROGRAM", "BURN"}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "meme-scanner/1.0", "Accept": "application/json"})


def log(msg):
    print("[" + datetime.now().strftime("%H:%M:%S") + "] " + str(msg), flush=True)


def get_json(url, method="GET", payload=None, timeout=25, retries=2):
    for attempt in range(retries + 1):
        try:
            if method == "POST":
                r = SESSION.post(url, json=payload, timeout=timeout)
            else:
                r = SESSION.get(url, timeout=timeout)
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            if attempt == retries:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def load_json_file(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------- discovery
def discover_solana_mints():
    """
    Candidates from two independent streams.

    DexScreener's profile and boost feeds are pay-to-appear: a token that never
    buys a boost is invisible there. GeckoTerminal's trending pools are ranked
    by actual trading, so they surface tokens nobody paid to promote. Neither
    alone is a full view of the chain; together they are less biased than either.
    """
    mints, seen = [], set()

    for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1"):
        data = get_json(DEX + path)
        if not isinstance(data, list):
            continue
        for item in data:
            if item.get("chainId") != "solana":
                continue
            mint = item.get("tokenAddress")
            if mint and mint not in seen:
                seen.add(mint)
                mints.append({"mint": mint, "src": "dexscreener"})
        time.sleep(1.1)  # these feeds are rate-limited to 60 req/min

    for path in ("/trending_pools", "/pools?sort=h24_volume_usd_desc"):
        data = get_json(GECKO + path)
        if not isinstance(data, dict):
            continue
        for pool in data.get("data") or []:
            base = ((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}
            mint = str(base.get("id", "")).replace("solana_", "")
            if mint and mint not in seen:
                seen.add(mint)
                mints.append({"mint": mint, "src": "geckoterminal"})
        time.sleep(1.1)

    return mints


# ---------------------------------------------------------------- enrichment
def best_pair(mint):
    """Deepest-liquidity trading pair for this mint."""
    data = get_json(DEX + "/token-pairs/v1/solana/" + mint)
    if not isinstance(data, list) or not data:
        return None
    priced = [p for p in data if (p.get("liquidity") or {}).get("usd")]
    if not priced:
        return None
    return max(priced, key=lambda p: p["liquidity"]["usd"])


def onchain_authorities(mint):
    """Authoritative mint/freeze authority read straight from the chain."""
    res = get_json(RPC, method="POST", payload={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [mint, {"encoding": "jsonParsed"}],
    })
    try:
        info = res["result"]["value"]["data"]["parsed"]["info"]
        return {
            "mint_authority": info.get("mintAuthority"),
            "freeze_authority": info.get("freezeAuthority"),
            "decimals": info.get("decimals"),
            "ok": True,
        }
    except Exception:
        return {"ok": False}


def rugcheck_report(mint):
    return get_json(RUGCHECK + "/tokens/" + mint + "/report")


def concentration(report):
    """Top-10 percent held, excluding AMM pools, lockers and burn addresses."""
    holders = report.get("topHolders") or []
    known = report.get("knownAccounts") or {}
    real = []
    for h in holders:
        owner, addr = h.get("owner"), h.get("address")
        meta = known.get(owner) or known.get(addr) or {}
        if str(meta.get("type", "")).upper() in NON_HOLDER_TYPES:
            continue
        real.append(h)
    top10 = sum(h.get("pct") or 0 for h in real[:10])
    insiders = sum(1 for h in holders if h.get("insider"))
    return round(top10, 2), insiders


def insider_networks(report):
    """
    How much of the supply sits in wallet clusters RugCheck has linked together.

    A "bundled" launch funds many wallets from one source so the float looks
    distributed while one person controls it. Top-10 concentration misses this
    entirely - the wallets are individually small.

    Returns (pct_of_supply, network_count, account_count).
    """
    nets = report.get("insiderNetworks") or []
    if not nets:
        return 0.0, 0, 0
    supply = float((report.get("token") or {}).get("supply") or 0)
    held = 0.0
    accounts = 0
    for n in nets:
        try:
            held += float(n.get("tokenAmount") or 0)
            accounts += int(n.get("size") or 0)
        except (TypeError, ValueError):
            continue
    pct = (held / supply * 100.0) if supply else 0.0
    return round(pct, 2), len(nets), accounts


def creator_history(report):
    """
    How many other tokens this deployer has launched, when RugCheck knows.

    Sparse: the field is populated for some tokens and null for most, so this
    can inform a decision but cannot be relied on to make one. None = unknown,
    never treated as clean.
    """
    ct = report.get("creatorTokens")
    if isinstance(ct, list):
        return len(ct)
    return None


def lp_locked_pct(report):
    """Highest LP-locked percentage across this token's markets."""
    best = 0.0
    for m in report.get("markets") or []:
        pct = ((m.get("lp") or {}).get("lpLockedPct")) or 0
        try:
            best = max(best, float(pct))
        except (TypeError, ValueError):
            continue
    return round(best, 2)


# ---------------------------------------------------------------- evaluation
def evaluate(pair, auth, report, gates, sig, cfg):
    """Run every hard gate. Returns (checks, facts); checks carry pass/fail + evidence."""
    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    vol24 = float((pair.get("volume") or {}).get("h24") or 0)
    vol1 = float((pair.get("volume") or {}).get("h1") or 0)
    created = pair.get("pairCreatedAt")
    age_h = (time.time() * 1000 - created) / 3600000.0 if created else None
    chg24 = (pair.get("priceChange") or {}).get("h24")
    txn1 = (pair.get("txns") or {}).get("h1") or {}
    buys, sells = txn1.get("buys", 0), txn1.get("sells", 0)
    buy_ratio = buys / (buys + sells) if (buys + sells) else 0
    vl = vol24 / liq if liq else 0

    top10, insiders = concentration(report)
    ins_pct, ins_nets, ins_accounts = insider_networks(report)
    creator_tokens = creator_history(report)
    lp_pct = lp_locked_pct(report)
    holders = report.get("totalHolders") or 0
    rc_score = report.get("score_normalised")
    socials = (pair.get("info") or {}).get("socials") or []
    sites = (pair.get("info") or {}).get("websites") or []

    mint_auth = auth.get("mint_authority")
    freeze_auth = auth.get("freeze_authority")

    C = []  # (label, passed, evidence)

    C.append(("Mint authority revoked", mint_auth is None,
              "revoked - supply cannot be inflated" if mint_auth is None
              else "ACTIVE (" + str(mint_auth) + ") - dev can mint unlimited supply"))
    C.append(("Freeze authority revoked", freeze_auth is None,
              "revoked - your wallet cannot be frozen" if freeze_auth is None
              else "ACTIVE (" + str(freeze_auth) + ") - dev can freeze your tokens"))
    C.append(("Not flagged as rugged", not report.get("rugged"),
              "clean" if not report.get("rugged") else "RugCheck flags this as already rugged"))
    C.append(("LP locked/burned >= {}%".format(gates["min_lp_locked_pct"]),
              lp_pct >= gates["min_lp_locked_pct"],
              "{}% locked".format(lp_pct)))
    C.append(("Liquidity ${:,}-${:,}".format(gates["min_liquidity_usd"], gates["max_liquidity_usd"]),
              gates["min_liquidity_usd"] <= liq <= gates["max_liquidity_usd"],
              "${:,.0f}".format(liq)))
    C.append(("Top-10 hold < {}%".format(gates["max_top10_pct_excl_lp"]),
              top10 < gates["max_top10_pct_excl_lp"],
              "{}% (LP/lockers excluded)".format(top10)))
    C.append(("Holders >= {}".format(gates["min_holders"]),
              holders >= gates["min_holders"],
              "{:,} holders".format(holders)))
    C.append(("Insiders in top-20 <= {}".format(gates["max_insiders_in_top20"]),
              insiders <= gates["max_insiders_in_top20"],
              "{} flagged".format(insiders)))
    C.append(("Age {}m-{}h".format(gates["min_age_minutes"], gates["max_age_hours"]),
              age_h is not None and gates["min_age_minutes"] / 60.0 <= age_h <= gates["max_age_hours"],
              "{:.1f}h old".format(age_h) if age_h is not None else "unknown"))
    C.append(("24h volume >= ${:,}".format(gates["min_vol24_usd"]),
              vol24 >= gates["min_vol24_usd"],
              "${:,.0f}".format(vol24)))
    C.append(("Vol/Liq {}-{}x".format(gates["min_vol_to_liq_ratio"], gates["max_vol_to_liq_ratio"]),
              gates["min_vol_to_liq_ratio"] <= vl <= gates["max_vol_to_liq_ratio"],
              "{:.1f}x".format(vl) + (" - wash-trading range" if vl > gates["max_vol_to_liq_ratio"] else "")))
    C.append(("1h buy ratio >= {:.0%}".format(gates["min_buy_ratio_h1"]),
              buy_ratio >= gates["min_buy_ratio_h1"],
              "{:.0%} ({}B/{}S)".format(buy_ratio, buys, sells)))

    max_chg = gates.get("max_price_change_h24_pct")
    if max_chg:
        try:
            c24 = float(chg24)
        except (TypeError, ValueError):
            c24 = None
        C.append(("24h change <= +{:.0f}%".format(max_chg),
                  c24 is None or c24 <= max_chg,
                  "unknown - not rejected" if c24 is None
                  else "{:+.0f}%".format(c24)
                  + (" - most of the move already happened" if c24 > max_chg else "")))

    max_ins = gates.get("max_insider_network_pct")
    if max_ins:
        C.append(("Insider clusters hold <= {}%".format(max_ins), ins_pct <= max_ins,
                  "{}% across {} cluster(s), {} wallets".format(ins_pct, ins_nets, ins_accounts)
                  if ins_nets else "no linked clusters found"))

    max_ct = gates.get("max_creator_tokens")
    if max_ct and creator_tokens is not None:
        C.append(("Deployer has launched <= {}".format(max_ct), creator_tokens <= max_ct,
                  "{} previous token(s) by this wallet".format(creator_tokens)))

    C.append(("RugCheck risk <= {}".format(gates["max_rugcheck_score"]),
              rc_score is not None and rc_score <= gates["max_rugcheck_score"],
              "score {}".format(rc_score)))
    if gates.get("require_any_social"):
        C.append(("Has socials/website", bool(socials or sites),
                  "{} social(s), {} site(s)".format(len(socials), len(sites))
                  if (socials or sites) else "none found"))

    C.extend(social.social_gates(sig, cfg["social_gates"]))

    facts = {
        "liq": liq, "vol24": vol24, "vol1": vol1, "age_h": age_h, "top10": top10,
        "holders": holders, "lp_pct": lp_pct, "buy_ratio": buy_ratio, "vl": vl,
        "insiders": insiders, "rc_score": rc_score,
        "ins_pct": ins_pct, "ins_nets": ins_nets, "ins_accounts": ins_accounts,
        "creator_tokens": creator_tokens, "creator": report.get("creator"),
        "symbol": (pair.get("baseToken") or {}).get("symbol", "?"),
        "name": (pair.get("baseToken") or {}).get("name", "?"),
        "price": pair.get("priceUsd"),
        "mcap": pair.get("marketCap") or pair.get("fdv"),
        "chg24": chg24,
        "chg1": (pair.get("priceChange") or {}).get("h1"),
        "dex": pair.get("dexId"),
        "url": pair.get("url"),
        "launchpad": (report.get("launchpad") or {}).get("name"),
        "sig": sig,
    }
    return C, facts


def safety_score(f, gates):
    """0-100 quality score, only for tokens that already cleared every hard gate."""
    s = 0.0
    s += min(20.0, (f["liq"] / 150000.0) * 20)                                      # depth
    s += min(15.0, (f["holders"] / 3000.0) * 15)                                    # breadth
    s += max(0.0, 20 * (1 - f["top10"] / max(gates["max_top10_pct_excl_lp"], 1)))   # concentration
    vl = f["vl"]                                                                    # turnover health
    if 1.5 <= vl <= 10:
        s += 15
    elif 0.8 <= vl < 1.5 or 10 < vl <= 18:
        s += 9
    else:
        s += 3
    s += min(15.0, max(0.0, (f["buy_ratio"] - 0.42) / 0.23 * 15))                   # buy pressure
    s += max(0.0, 15 * (1 - (f["rc_score"] if f["rc_score"] is not None else 50) / 100.0))
    return round(min(100.0, s), 1)


# ---------------------------------------------------------------- alerting
def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_plain(msg):
    """Telegram-HTML -> terminal text, so a dry run reads exactly like the real alert."""
    t = re.sub(r'<a href="([^"]*)">(.*?)</a>', r"\2  ->  \1", msg, flags=re.S)
    t = re.sub(r"</?(b|i|code|pre|u|s)>", "", t)
    return html.unescape(t)


def build_message(mint, checks, f, sc):
    jup = "https://jup.ag/swap/SOL-" + mint
    phantom = ("https://phantom.app/ul/browse/" + urllib.parse.quote(jup, safe="")
               + "?ref=" + urllib.parse.quote("https://jup.ag", safe=""))
    mcap = "${:,.0f}".format(f["mcap"]) if f.get("mcap") else "n/a"
    age = "{:.1f}h".format(f["age_h"]) if f.get("age_h") is not None else "?"

    L = []
    L.append("<b>SIGNAL  " + esc(f["symbol"]) + "</b>  -  composite <b>" + str(sc) + "/100</b>")
    L.append("safety <b>" + str(f.get("safety", "?")) + "/100</b> ({:.0%})".format(f.get("w_safety", 0))
             + "  +  social <b>" + str(f.get("social", "?")) + "/100</b> ({:.0%})".format(f.get("w_social", 0)))
    L.append("<i>" + esc(f["name"]) + "</i>"
             + (" - via " + esc(f["launchpad"]) if f.get("launchpad") else ""))
    L.append("")
    L.append("<b>Contract address (tap to copy):</b>")
    L.append("<code>" + mint + "</code>")
    L.append("")
    L.append("Price <b>$" + str(f["price"]) + "</b> | MCap <b>" + mcap
             + "</b> | Liq <b>${:,.0f}</b>".format(f["liq"]))
    L.append("1h <b>" + str(f["chg1"]) + "%</b> | 24h <b>" + str(f["chg24"])
             + "%</b> | Vol24 <b>${:,.0f}</b>".format(f["vol24"]))
    L.append("Age <b>" + age + "</b> | Holders <b>{:,}</b>".format(f["holders"])
             + " | DEX " + esc(f["dex"]))
    L.append("")
    if f.get("ins_nets"):
        L.append("Insider clusters: <b>{}%</b> of supply across {} cluster(s), {} wallets".format(
            f["ins_pct"], f["ins_nets"], f["ins_accounts"]))
    if f.get("creator_tokens") is not None:
        L.append("Deployer has launched <b>{}</b> other token(s)".format(f["creator_tokens"]))
    L.append("<b>PROOF - every check, verified live:</b>")
    for label, ok, ev in checks:
        L.append(("✅ " if ok else "❌ ") + esc(label) + " - <i>" + esc(ev) + "</i>")
    L.append("")
    L.append("<b>SOCIAL - basis: arXiv:2607.02823, Cox HR 5.40</b>")
    for name, pts, mx, ev in f.get("social_rows", []):
        L.append(("+" if pts else "-") + " " + esc(name) + ": <b>" + str(pts) + "/"
                 + str(mx) + "</b> - <i>" + esc(ev) + "</i>")
    sg = f.get("sig") or {}
    if sg.get("telegram_url"):
        L.append('- <a href="' + esc(sg["telegram_url"]) + '">Telegram</a>')
    if sg.get("twitter_url"):
        L.append('- <a href="' + esc(sg["twitter_url"]) + '">X/Twitter</a> (link unverified)')
    if sg.get("website_url"):
        L.append('- <a href="' + esc(sg["website_url"]) + '">Website</a>')

    L.append("")
    L.append("<b>Verify it yourself:</b>")
    L.append('- <a href="https://rugcheck.xyz/tokens/' + mint + '">RugCheck report</a>')
    L.append('- <a href="https://solscan.io/token/' + mint + '">Solscan (on-chain)</a>')
    L.append('- <a href="' + str(f["url"]) + '">DexScreener chart</a>')
    L.append('- <a href="https://solscan.io/token/' + mint + '#holders">Holder distribution</a>')
    L.append("")
    L.append('<b>Buy:</b> <a href="' + jup + '">Jupiter</a> | <a href="'
             + phantom + '">Open in Phantom</a>')
    L.append("")
    L.append("<i>Screener output, not financial advice. These gates reduce rug risk - "
             "they do not remove it. Meme coins routinely go to zero. Verify the links "
             "above and never risk more than you can afford to lose.</i>")
    return "\n".join(L)


def links(mint, pair_url):
    """Every outbound link an alert carries, built from the mint."""
    jup = "https://jup.ag/swap/SOL-" + mint
    return {
        "jupiter": jup,
        "phantom": ("https://phantom.app/ul/browse/" + urllib.parse.quote(jup, safe="")
                    + "?ref=" + urllib.parse.quote("https://jup.ag", safe="")),
        "dexscreener": pair_url or ("https://dexscreener.com/solana/" + mint),
        "rugcheck": "https://rugcheck.xyz/tokens/" + mint,
        "solscan": "https://solscan.io/token/" + mint,
    }


def build_caption(mint, f, sc, tag="SIGNAL"):
    """Telegram caps captions at 1024 chars - this is the glance, the proof follows."""
    L = links(mint, f.get("url"))
    mcap = "${:,.0f}".format(f["mcap"]) if f.get("mcap") else "n/a"
    age = "{:.1f}h".format(f["age_h"]) if f.get("age_h") is not None else "?"
    C = []
    C.append("<b>" + tag + "  " + esc(f["symbol"]) + "</b>  -  composite <b>"
             + str(sc) + "/100</b>")
    C.append("safety <b>" + str(f.get("safety", "?")) + "</b>  ·  social <b>"
             + str(f.get("social", "?")) + "</b>  ·  " + str(f.get("checks_passed", "?"))
             + " checks passed")
    C.append("")
    C.append("<code>" + mint + "</code>")
    C.append("")
    C.append("MCap <b>" + mcap + "</b> | Liq <b>${:,.0f}</b>".format(f["liq"])
             + " | Age <b>" + age + "</b>")
    C.append("")
    C.append('<b>BUY:</b> <a href="' + L["phantom"] + '">Open in Phantom</a>'
             + ' | <a href="' + L["jupiter"] + '">Jupiter</a>')
    C.append('<b>CHART:</b> <a href="' + L["dexscreener"] + '">DexScreener</a>'
             + ' | <a href="' + L["rugcheck"] + '">RugCheck</a>'
             + ' | <a href="' + L["solscan"] + '">Solscan</a>')
    C.append("")
    C.append("<i>Full proof below.</i>")
    return "\n".join(C)


def send_photo(cfg, path, caption):
    tok = cfg["telegram"]["bot_token"]
    chat = cfg["telegram"]["chat_id"]
    if "PASTE" in str(tok) or "PASTE" in str(chat):
        log("!! Telegram not configured")
        return False
    try:
        with open(path, "rb") as fh:
            r = SESSION.post("https://api.telegram.org/bot" + tok + "/sendPhoto",
                             data={"chat_id": chat, "caption": caption,
                                   "parse_mode": "HTML"},
                             files={"photo": ("signal.png", fh, "image/png")},
                             timeout=60)
        body = r.json()
    except Exception as e:
        log("!! Photo send failed: " + str(e))
        return False
    if body.get("ok"):
        return True
    log("!! Photo send rejected: " + str(body))
    return False


def send_telegram(cfg, text):
    tok = cfg["telegram"]["bot_token"]
    chat = cfg["telegram"]["chat_id"]
    if "PASTE" in str(tok) or "PASTE" in str(chat):
        log("!! Telegram not configured - set bot_token and chat_id in config.json")
        return False
    r = get_json("https://api.telegram.org/bot" + tok + "/sendMessage",
                 method="POST",
                 payload={"chat_id": chat, "text": text,
                          "parse_mode": "HTML", "disable_web_page_preview": True})
    if r and r.get("ok"):
        return True
    log("!! Telegram send failed: " + str(r))
    return False


def deliver(cfg, mint, checks, facts, sc, msg, tag="SIGNAL"):
    """Card first as the glance, then the full itemised proof underneath."""
    if cfg.get("send_card", True):
        try:
            facts.setdefault("checks_passed", sum(1 for _, ok, _ in checks if ok))
            path = os.path.join(HERE, "last_card.png")
            card.render(path, mint, facts, sc, label=tag)
            send_photo(cfg, path, build_caption(mint, facts, sc, tag))
        except Exception as e:
            log("!! Card render/send failed, sending text only: " + str(e))
    return send_telegram(cfg, msg)


# ---------------------------------------------------------------- main scan
def scan_once(cfg, dry_run=False, verbose=False):
    gates = cfg["gates"]
    state = load_json_file(STATE_PATH, {"alerted": {}})
    now = time.time()
    cutoff = cfg["scan"]["realert_after_hours"] * 3600
    state["alerted"] = {m: t for m, t in state["alerted"].items() if now - t < cutoff}

    log("Discovering fresh Solana tokens...")
    candidates = discover_solana_mints()
    log("  " + str(len(candidates)) + " candidates from DexScreener feeds")

    passed = []
    sent = 0
    for i, c in enumerate(candidates, 1):
        mint = c["mint"]
        if mint in state["alerted"]:
            continue

        pair = best_pair(mint)
        if not pair:
            continue

        # cheap pre-filter before spending the expensive calls
        liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        if liq < gates["min_liquidity_usd"] or liq > gates["max_liquidity_usd"]:
            if verbose:
                log("  [{}] {}.. skip: liquidity ${:,.0f}".format(i, mint[:8], liq))
            continue

        auth = onchain_authorities(mint)
        report = rugcheck_report(mint)
        if not auth.get("ok") or not report:
            if verbose:
                log("  [{}] {}.. skip: data unavailable".format(i, mint[:8]))
            continue

        sig = social.gather(pair)
        checks, facts = evaluate(pair, auth, report, gates, sig, cfg)
        failed = [lb for lb, ok, _ in checks if not ok]
        if failed:
            extra = " (+{} more)".format(len(failed) - 1) if len(failed) > 1 else ""
            log("  [{}] {:<12} REJECT - {}{}".format(i, facts["symbol"][:12], failed[0], extra))
            time.sleep(0.6)
            continue

        saf = safety_score(facts, gates)
        soc, soc_rows = social.social_score(sig, cfg["social_weights"])
        w = cfg["composite"]
        sc = round(w["safety_weight"] * saf + w["social_weight"] * soc, 1)
        facts["safety"], facts["social"], facts["social_rows"] = saf, soc, soc_rows
        facts["w_safety"], facts["w_social"] = w["safety_weight"], w["social_weight"]

        floor = cfg.get("min_safety_score", 0)
        if saf < floor:
            log("  [{}] {:<12} blocked by safety floor: {} < {} (social {}, composite {})".format(
                i, facts["symbol"][:12], saf, floor, soc, sc))
            time.sleep(0.6)
            continue

        if sc < cfg["min_score_to_alert"]:
            log("  [{}] {:<12} clean but composite {} < {} (safety {} / social {})".format(
                i, facts["symbol"][:12], sc, cfg["min_score_to_alert"], saf, soc))
            # the control group: cleared every gate, scored short. If these do as
            # well as the alerts, the threshold is not earning its keep.
            track.record(mint, facts, sc, alerted=False)
            time.sleep(0.6)
            continue

        log("  [{}] {:<12} *** PASSED - composite {} (safety {} / social {}) ***".format(
            i, facts["symbol"][:12], sc, saf, soc))
        passed.append((mint, checks, facts, sc))
        time.sleep(0.6)

    passed.sort(key=lambda x: x[3], reverse=True)
    for mint, checks, facts, sc in passed[: cfg["scan"]["max_alerts_per_scan"]]:
        msg = build_message(mint, checks, facts, sc)
        if dry_run:
            print("\n" + "=" * 62 + "\n[DRY RUN - would send to Telegram]\n" + "=" * 62)
            print(to_plain(msg))
        elif deliver(cfg, mint, checks, facts, sc, msg):
            state["alerted"][mint] = now
            track.record(mint, facts, sc, alerted=True)
            sent += 1
            log("  -> Telegram alert sent for " + str(facts["symbol"]))

    try:
        filled = track.update()
        if filled:
            log("Track record: {} checkpoint(s) filled.".format(filled))
    except Exception as e:
        log("Track update skipped: " + str(e))

    state["last_scan"] = now
    state["last_scan_human"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["scans_total"] = state.get("scans_total", 0) + 1
    state["alerts_total"] = state.get("alerts_total", 0) + sent
    state["last_candidates"] = len(candidates)
    state["last_passed"] = len(passed)
    if sent:
        state["last_alert"] = now
    save_json_file(STATE_PATH, state)
    log("Scan complete: {} passed all gates, {} alert(s) sent.\n".format(len(passed), sent))
    return len(passed)


def main():
    ap = argparse.ArgumentParser(description="Solana meme-coin screener with Telegram alerts")
    ap.add_argument("--once", action="store_true", help="single scan then exit (default)")
    ap.add_argument("--loop", action="store_true", help="scan forever on the config interval")
    ap.add_argument("--dry-run", action="store_true", help="print alerts instead of sending")
    ap.add_argument("--verbose", action="store_true", help="show skipped candidates")
    ap.add_argument("--test-telegram", action="store_true", help="send a test message and exit")
    ap.add_argument("--demo", metavar="MINT",
                    help="send one demo alert for this mint, whatever it scores")
    args = ap.parse_args()

    cfg = load_json_file(CONFIG_PATH, None)
    if cfg is None:
        log("config.json missing or invalid")
        sys.exit(1)
    creds.apply(cfg)

    if args.test_telegram:
        ok = send_telegram(cfg, "<b>Scanner connected.</b>\nTelegram alerts are working. "
                                "Signals will arrive here with the contract address and full proof.")
        print("OK - check your Telegram." if ok else "FAILED - see the error above.")
        sys.exit(0 if ok else 1)

    if args.demo:
        mint = args.demo.strip()
        log("Building a demo alert for " + mint)
        pair = best_pair(mint)
        if not pair:
            log("No priced pair on DexScreener for that mint.")
            sys.exit(1)
        auth = onchain_authorities(mint)
        report = rugcheck_report(mint)
        if not auth.get("ok") or not report:
            log("Could not read chain or RugCheck data for that mint.")
            sys.exit(1)
        sig = social.gather(pair)
        checks, facts = evaluate(pair, auth, report, cfg["gates"], sig, cfg)
        saf = safety_score(facts, cfg["gates"])
        soc, rows = social.social_score(sig, cfg["social_weights"])
        w = cfg["composite"]
        sc = round(w["safety_weight"] * saf + w["social_weight"] * soc, 1)
        facts["safety"], facts["social"], facts["social_rows"] = saf, soc, rows
        facts["w_safety"], facts["w_social"] = w["safety_weight"], w["social_weight"]
        facts["checks_passed"] = sum(1 for _, ok, _ in checks if ok)
        failed = [lb for lb, ok, _ in checks if not ok]
        log("  " + str(facts["symbol"]) + ": composite " + str(sc)
            + " (safety " + str(saf) + " / social " + str(soc) + ")"
            + ("  failing: " + ", ".join(failed) if failed else "  all checks passed"))
        msg = build_message(mint, checks, facts, sc)
        ok = deliver(cfg, mint, checks, facts, sc, msg, tag="DEMO")
        print("Demo sent - check Telegram." if ok else "Demo failed - see above.")
        sys.exit(0 if ok else 1)

    if args.loop:
        iv = cfg["scan"]["interval_seconds"]
        log("Loop mode - scanning every " + str(iv) + "s. Ctrl+C to stop.")
        while True:
            try:
                scan_once(cfg, args.dry_run, args.verbose)
            except KeyboardInterrupt:
                log("Stopped.")
                break
            except Exception as e:
                log("Scan error: " + str(e))
            time.sleep(iv)
    else:
        scan_once(cfg, args.dry_run, args.verbose)


if __name__ == "__main__":
    main()
