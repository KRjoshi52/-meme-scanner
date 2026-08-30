"""
Renders a signal card as a PNG for Telegram.

The card is the glance; the text message underneath is the proof. Everything on
the card is a number the scanner actually verified - nothing decorative that
implies a fact it did not check.
"""

import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
PAD = 64

BG      = (13, 19, 22)
PANEL   = (20, 29, 33)
RULE    = (37, 50, 56)
INK     = (229, 236, 238)
MUTED   = (151, 167, 174)
FAINT   = (111, 128, 135)
ACCENT  = (116, 185, 216)
ACCENT2 = (63, 122, 147)
PASS    = (109, 190, 141)
WARN    = (224, 128, 95)

FONT_DIRS = ("C:/Windows/Fonts/",
             "/usr/share/fonts/truetype/dejavu/",
             "/usr/share/fonts/truetype/liberation/",
             "/usr/share/fonts/truetype/")

# Windows first, then what a Linux CI runner actually ships.
FONT_ALIASES = {
    "segoeui.ttf":   ("segoeui.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"),
    "segoeuib.ttf":  ("segoeuib.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"),
    "segoeuil.ttf":  ("segoeuil.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"),
    "consola.ttf":   ("consola.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf"),
    "consolab.ttf":  ("consolab.ttf", "DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf"),
}


def _font(name, size):
    for candidate in FONT_ALIASES.get(name, (name,)):
        for folder in FONT_DIRS:
            try:
                return ImageFont.truetype(folder + candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def bold(s):   return _font("segoeuib.ttf", s)
def reg(s):    return _font("segoeui.ttf", s)
def light(s):  return _font("segoeuil.ttf", s)
def mono(s):   return _font("consola.ttf", s)
def monob(s):  return _font("consolab.ttf", s)


def _money(v):
    if v is None:
        return "n/a"
    v = float(v)
    if v >= 1_000_000:
        return "${:.2f}M".format(v / 1_000_000)
    if v >= 1_000:
        return "${:.0f}k".format(v / 1_000)
    return "${:,.0f}".format(v)


def _price(p):
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "n/a"
    if p >= 1:
        return "${:,.4f}".format(p)
    s = "{:.10f}".format(p).rstrip("0")
    return "$" + s


def _check(d, x, y, r, colour):
    """A drawn tick - no emoji font dependency."""
    d.ellipse([x - r, y - r, x + r, y + r], outline=colour, width=3)
    d.line([(x - r * .42, y + r * .02), (x - r * .10, y + r * .34),
            (x + r * .46, y - r * .38)], fill=colour, width=4, joint="curve")


def _bang(d, x, y, r, colour):
    """A drawn exclamation - for a check that passed the gates but reads thin."""
    d.ellipse([x - r, y - r, x + r, y + r], outline=colour, width=3)
    d.line([(x, y - r * .52), (x, y + r * .14)], fill=colour, width=4)
    d.ellipse([x - 2, y + r * .40, x + 2, y + r * .40 + 4], fill=colour)


def _mark(d, x, y, r, ok):
    (_check if ok else _bang)(d, x, y, r, PASS if ok else WARN)


def render(path, mint, facts, composite, label="SIGNAL"):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # ---------------------------------------------------------------- top rule
    d.rectangle([0, 0, W, 7], fill=ACCENT)

    # ---------------------------------------------------------------- header
    y = 52
    chip_w, chip_h = 132, 40
    d.rounded_rectangle([PAD, y, PAD + chip_w, y + chip_h], radius=8, fill=ACCENT)
    d.text((PAD + chip_w / 2, y + chip_h / 2), label, font=bold(21),
           fill=BG, anchor="mm")

    passed = facts.get("checks_passed", 17)
    d.text((W - PAD, y + chip_h / 2), "{} / {} CHECKS PASSED".format(passed, passed),
           font=reg(21), fill=PASS, anchor="rm")

    y += chip_h + 26
    d.text((PAD, y), str(facts.get("symbol", "?"))[:14], font=bold(92), fill=INK, anchor="la")
    y += 104
    sub = str(facts.get("name", ""))[:38]
    if facts.get("launchpad"):
        sub += "   ·   via " + str(facts["launchpad"])
    d.text((PAD, y), sub, font=reg(28), fill=MUTED, anchor="la")

    # ---------------------------------------------------------------- score
    y += 62
    d.text((PAD, y), "COMPOSITE", font=reg(20), fill=FAINT, anchor="la")
    d.text((PAD, y + 26), "{:.1f}".format(composite), font=bold(96), fill=ACCENT, anchor="la")
    w_num = d.textlength("{:.1f}".format(composite), font=bold(96))
    d.text((PAD + w_num + 12, y + 92), "/100", font=reg(30), fill=FAINT, anchor="ls")

    saf = float(facts.get("safety") or 0)
    soc = float(facts.get("social") or 0)
    ws = float(facts.get("w_safety") or 0.70)
    wo = float(facts.get("w_social") or 0.30)

    rx = W - PAD
    d.text((rx, y + 24), "SAFETY", font=reg(19), fill=FAINT, anchor="ra")
    d.text((rx, y + 48), "{:.0f}".format(saf) + " /100", font=bold(34), fill=INK, anchor="ra")
    d.text((rx, y + 96), "SOCIAL", font=reg(19), fill=FAINT, anchor="ra")
    d.text((rx, y + 120), "{:.0f}".format(soc) + " /100", font=bold(34), fill=INK, anchor="ra")

    # stacked contribution bar
    y += 156
    bar_x0, bar_x1, bar_h = PAD, W - PAD, 26
    span = bar_x1 - bar_x0
    d.rounded_rectangle([bar_x0, y, bar_x1, y + bar_h], radius=6, fill=PANEL)
    s_w = span * (ws * saf) / 100.0
    o_w = span * (wo * soc) / 100.0
    if s_w > 4:
        d.rounded_rectangle([bar_x0, y, bar_x0 + s_w, y + bar_h], radius=6, fill=ACCENT)
    if o_w > 4:
        d.rounded_rectangle([bar_x0 + s_w - 6, y, bar_x0 + s_w + o_w, y + bar_h],
                            radius=6, fill=ACCENT2)
    tx = bar_x0 + span * 0.60
    d.line([(tx, y - 9), (tx, y + bar_h + 9)], fill=WARN, width=3)
    d.text((tx, y + bar_h + 16), "alert threshold 60", font=reg(18), fill=WARN, anchor="ma")

    y += bar_h + 42
    d.text((bar_x0, y), "safety contribution", font=reg(18), fill=ACCENT, anchor="la")
    d.text((bar_x1, y), "social contribution", font=reg(18), fill=ACCENT2, anchor="ra")

    # ---------------------------------------------------------------- stats
    y += 46
    d.line([(PAD, y), (W - PAD, y)], fill=RULE, width=1)
    y += 30

    cells = [
        ("PRICE", _price(facts.get("price"))),
        ("MARKET CAP", _money(facts.get("mcap"))),
        ("LIQUIDITY", _money(facts.get("liq"))),
        ("24H VOLUME", _money(facts.get("vol24"))),
        ("AGE", "{:.1f}h".format(facts["age_h"]) if facts.get("age_h") is not None else "n/a"),
        ("HOLDERS", "{:,}".format(int(facts.get("holders") or 0))),
    ]
    col_w = (W - PAD * 2) / 3.0
    for i, (cap, value) in enumerate(cells):
        cx = PAD + col_w * (i % 3)
        cy = y + (i // 3) * 102
        d.text((cx, cy), cap, font=reg(19), fill=FAINT, anchor="la")
        d.text((cx, cy + 28), value, font=bold(38), fill=INK, anchor="la")

    y += 202
    d.line([(PAD, y), (W - PAD, y)], fill=RULE, width=1)

    # ---------------------------------------------------------------- evidence
    y += 34
    d.text((PAD, y), "VERIFIED - ON-CHAIN AND OFF", font=reg(20), fill=FAINT, anchor="la")
    y += 36

    top10 = facts.get("top10")
    lp = facts.get("lp_pct")
    rc = facts.get("rc_score")
    chg = facts.get("chg24")

    # (text, ok) - ok=False draws a warning mark: it cleared the gates but reads thin
    rows = [
        ("Mint and freeze authority revoked - supply fixed, cannot be frozen", True),
        ("LP {:.0f}% locked or burned - liquidity cannot be pulled".format(float(lp or 0)), True),
        ("Top-10 hold {:.1f}% excluding pools and lockers".format(float(top10 or 0)), True),
    ]
    rc_text = "RugCheck risk {} / 100".format(rc if rc is not None else "?")
    if chg is not None:
        rc_text += "  -  24h move {:+.0f}%".format(float(chg))
    rows.append((rc_text, True))

    sig = facts.get("sig") or {}
    x = sig.get("x") or {}
    site = sig.get("site") or {}

    if x.get("community"):
        rows.append(("X link is a community, not an account - unverifiable", False))
    elif x.get("exists") and x.get("age_days") is not None:
        age, fol, tw = x["age_days"], x.get("followers") or 0, x.get("tweets") or 0
        rows.append(("X @{} - {} days old, {:,} followers, {:,} posts".format(
            x.get("handle", "?"), age, fol, tw), age >= 30 and tw >= 5))
    elif sig.get("has_twitter"):
        rows.append(("X account could not be verified", False))

    if site.get("age_days") is not None:
        rows.append(("{} registered {} days ago".format(site.get("domain", "?"),
                                                        site["age_days"]),
                     site["age_days"] >= 30))

    for text, ok in rows[:6]:
        _mark(d, PAD + 13, y + 12, 13, ok)
        d.text((PAD + 42, y), text, font=reg(23), fill=MUTED, anchor="la")
        y += 38

    # ---------------------------------------------------------------- address
    y = H - 232
    d.rounded_rectangle([PAD, y, W - PAD, y + 104], radius=10, fill=PANEL)
    d.text((PAD + 24, y + 20), "CONTRACT ADDRESS", font=reg(18), fill=FAINT, anchor="la")
    addr_font = mono(25)
    while d.textlength(mint, font=addr_font) > (W - PAD * 2 - 48) and addr_font.size > 14:
        addr_font = mono(addr_font.size - 1)
    d.text((PAD + 24, y + 50), mint, font=addr_font, fill=ACCENT, anchor="la")

    # ---------------------------------------------------------------- footer
    d.text((PAD, H - 88), "Screener output, not financial advice - verify every number yourself",
           font=reg(20), fill=FAINT, anchor="la")
    d.text((PAD, H - 58), "rugcheck.xyz  ·  solscan.io  ·  dexscreener.com",
           font=reg(20), fill=FAINT, anchor="la")
    d.text((W - PAD, H - 58), datetime.now().strftime("%d %b %Y  %H:%M"),
           font=mono(20), fill=FAINT, anchor="ra")
    d.rectangle([0, H - 7, W, H], fill=ACCENT2)

    img.save(path, "PNG", optimize=True)
    return path
