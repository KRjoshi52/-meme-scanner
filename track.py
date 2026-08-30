"""
The track record: what actually happened after each alert.

    python track.py --report        what the signals have been worth
    python track.py --update        fill in due checkpoints
    python track.py --report --all  include near-misses (the control group)

Without this the scanner is an opinion. Every alert is recorded with its entry
price, then re-priced at +1h, +6h and +24h, so the gates and thresholds can be
tuned against what happened rather than against what sounded right.

Near-misses - tokens that cleared every hard gate but scored below the alert
threshold - are recorded too. They are the control: if they do as well as the
alerts, the threshold is not earning its keep.
"""

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK = os.path.join(HERE, "track.json")
DEX = "https://api.dexscreener.com/token-pairs/v1/solana/"

# (label, seconds, grace) - a checkpoint measured long after it came due is not
# that checkpoint. If the scanner was offline, "1h" filled from a 10-hour-old
# price is not a 1-hour result, so it is marked missed and excluded from stats.
HORIZONS = (("1h", 3600, 2700), ("6h", 21600, 10800), ("24h", 86400, 43200))
GIVE_UP_AFTER = 172800          # 48h - past the last checkpoint plus its grace

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "meme-scanner-track/1.0", "Accept": "application/json"})


def _load():
    try:
        d = json.load(io.open(TRACK, encoding="utf-8"))
        return d if isinstance(d, dict) and "entries" in d else {"entries": []}
    except (OSError, ValueError):
        return {"entries": []}


def _save(data):
    tmp = TRACK + ".tmp"
    io.open(tmp, "w", encoding="utf-8").write(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, TRACK)


def _price_now(mint, timeout=20):
    """Current price and liquidity, or (None, None) if it cannot be read."""
    try:
        r = SESSION.get(DEX + mint, timeout=timeout)
        pairs = r.json()
    except (requests.RequestException, ValueError):
        return None, None
    if not isinstance(pairs, list) or not pairs:
        return None, None
    priced = [p for p in pairs if (p.get("liquidity") or {}).get("usd")]
    if not priced:
        return None, None
    best = max(priced, key=lambda p: p["liquidity"]["usd"])
    try:
        return float(best.get("priceUsd")), float(best["liquidity"]["usd"])
    except (TypeError, ValueError):
        return None, best["liquidity"]["usd"]


def record(mint, facts, composite, alerted=True):
    """Log a signal at the moment it fired. Never raises - tracking must not break a scan."""
    try:
        data = _load()
        if any(e["mint"] == mint for e in data["entries"]):
            return False
        try:
            price0 = float(facts.get("price"))
        except (TypeError, ValueError):
            price0 = None
        data["entries"].append({
            "mint": mint,
            "symbol": facts.get("symbol", "?"),
            "at": time.time(),
            "at_human": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "alerted": bool(alerted),
            "composite": composite,
            "safety": facts.get("safety"),
            "social": facts.get("social"),
            "price0": price0,
            "liq0": facts.get("liq"),
            "mcap0": facts.get("mcap"),
            "age_h0": facts.get("age_h"),
            "marks": {},
        })
        _save(data)
        return True
    except Exception:
        return False


def update(verbose=False):
    """Fill in any checkpoint that has come due. Safe to call after every scan."""
    data = _load()
    now = time.time()
    changed = 0
    for e in data["entries"]:
        age = now - e["at"]
        if age > GIVE_UP_AFTER and len(e["marks"]) >= len(HORIZONS):
            continue
        due = [(k, s, g) for k, s, g in HORIZONS if age >= s and k not in e["marks"]]
        if not due:
            continue

        # anything already past its grace window can never be measured honestly
        late = [(k, s, g) for k, s, g in due if age > s + g]
        for key, _, _g in late:
            e["marks"][key] = {"missed": True, "pct": None,
                               "note": "not measured within the window"}
            changed += 1
        due = [t for t in due if t not in late]
        if not due:
            continue

        price, liq = _price_now(e["mint"])
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for key, _, _g in due:
            if price is None:
                # unreadable price after a real alert almost always means the pool is gone
                e["marks"][key] = {"at": stamp, "price": None, "liq": liq, "pct": None,
                                   "note": "no priced pair"}
            else:
                p0 = e.get("price0")
                pct = ((price - p0) / p0 * 100.0) if p0 else None
                e["marks"][key] = {"at": stamp, "price": price, "liq": liq,
                                   "pct": round(pct, 1) if pct is not None else None}
            changed += 1
            if verbose:
                print("  {:<12} {:<4} {}".format(e["symbol"], key,
                      e["marks"][key].get("pct", "n/a")))
        time.sleep(0.4)
    if changed:
        _save(data)
    return changed


def _stats(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    return {
        "n": n,
        "median": round(median, 1),
        "mean": round(sum(s) / n, 1),
        "win_rate": round(100.0 * sum(1 for v in s if v > 0) / n),
        "best": round(s[-1], 1),
        "worst": round(s[0], 1),
    }


def report(include_all=False):
    data = _load()
    entries = data["entries"]
    if not entries:
        return ("No signals recorded yet. The record starts with the next alert.")

    groups = [("ALERTED", [e for e in entries if e.get("alerted")])]
    if include_all:
        groups.append(("NEAR MISS (control)", [e for e in entries if not e.get("alerted")]))

    out = []
    for name, group in groups:
        if not group:
            continue
        out.append(name + "  -  " + str(len(group)) + " recorded")
        for key, _, _g in HORIZONS:
            vals = [e["marks"][key]["pct"] for e in group
                    if key in e["marks"] and e["marks"][key].get("pct") is not None]
            dead = sum(1 for e in group
                       if key in e["marks"] and e["marks"][key].get("pct") is None
                       and not e["marks"][key].get("missed"))
            missed = sum(1 for e in group
                         if key in e["marks"] and e["marks"][key].get("missed"))
            st = _stats(vals)
            if not st and not dead:
                out.append("  {:<4} {}".format(
                    key, "{} missed".format(missed) if missed else "not due yet"))
                continue
            if not st:
                out.append("  {:<4} {} with no priced pair left".format(key, dead))
                continue
            out.append("  {:<4} n={:<3} median {:>7.1f}%   mean {:>7.1f}%   "
                       "up {:>3}%   best {:>7.1f}%   worst {:>7.1f}%{}{}".format(
                           key, st["n"], st["median"], st["mean"], st["win_rate"],
                           st["best"], st["worst"],
                           "   (+{} dead)".format(dead) if dead else "",
                           "   ({} missed)".format(missed) if missed else ""))
        out.append("")

    pending = [e for e in entries if len(e["marks"]) < len(HORIZONS)]
    if pending:
        out.append("still maturing: " + ", ".join(e["symbol"] for e in pending[:8]))
    return "\n".join(out)


def recent(limit=8):
    """One line per recent signal, newest first."""
    data = _load()
    rows = sorted(data["entries"], key=lambda e: e["at"], reverse=True)[:limit]
    if not rows:
        return "Nothing recorded yet."
    out = []
    for e in rows:
        marks = "  ".join(
            "{} {}".format(k, ("missed" if e["marks"][k].get("missed") else "dead")
                           if e["marks"][k].get("pct") is None
                           else "{:+.0f}%".format(e["marks"][k]["pct"]))
            for k, _, _g in HORIZONS if k in e["marks"])
        out.append("{:<12} {:<5} {:>5}  {}".format(
            e["symbol"][:12], "alert" if e.get("alerted") else "near",
            e.get("composite", "?"), marks or "pending"))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Track record for the scanner's signals")
    ap.add_argument("--update", action="store_true", help="fill in due checkpoints")
    ap.add_argument("--report", action="store_true", help="print the record")
    ap.add_argument("--recent", action="store_true", help="list recent signals")
    ap.add_argument("--all", action="store_true", help="include near-misses in the report")
    args = ap.parse_args()

    if args.update or not (args.report or args.recent):
        n = update(verbose=True)
        print("checkpoints filled:", n)
    if args.report:
        print("\n" + report(include_all=args.all))
    if args.recent:
        print("\n" + recent())


if __name__ == "__main__":
    main()
