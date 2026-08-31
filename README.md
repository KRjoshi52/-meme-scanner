# Solana Meme-Coin Screener → Telegram

Finds Solana tokens that survive a hard safety screen, then pushes the **contract
address plus verifiable proof** to your Telegram. Built for Phantom wallet.

It does **not** predict price. It filters out the tokens that are structurally
designed to take your money, so you spend your attention on the small remainder.

---

## 1. Connect Telegram (3 minutes)

1. In Telegram, message **@BotFather** and send `/newbot`. Pick a display name, then a
   username ending in `bot`. It replies with a token like `8123456789:AAH...`
2. Open your new bot and press **START**, so it is allowed to message you
3. Run the setup, which verifies the token, finds your chat id and sends a test message:

```bash
python setup_telegram.py 8123456789:AAH...
```

If it says your bot has not received any message, you missed step 2 - or you messaged a
different bot with the same display name. Open your bot through the `t.me/<username>`
link BotFather gave you, not through search. You can also skip the lookup entirely:

```bash
python setup_telegram.py <token> --chat-id 123456789      # your Telegram user id
```

**Where the credentials live.** `setup_telegram.py` writes them to `secrets.json`, which
is git-ignored and never leaves the machine. `config.json` holds only settings and is safe
to commit. In GitHub Actions they come from the `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` repository secrets instead.

```bash
python scanner.py --test-telegram    # prove the wiring end to end
```

---

## 2. Run it

```bash
python scanner.py --dry-run --verbose   # see everything, send nothing
python scanner.py                       # one scan, sends real alerts
python scanner.py --loop                # continuous, every 15 min
python scanner.py --demo <mint>         # send one demo alert for any token
python bot_commands.py                  # answer /status and /scan from Telegram
```

### Around the clock

A scheduled task named **MemeScanner** runs one scan every 15 minutes with no window
on screen, appending to `scanner.log`. It is already installed:

```powershell
python status.py                      # is it running? what has it done?
schtasks /run    /tn MemeScanner      # force a scan right now
schtasks /end    /tn MemeScanner      # stop the scan in progress
schtasks /change /tn MemeScanner /disable   # pause it
schtasks /change /tn MemeScanner /enable    # resume
schtasks /delete /tn MemeScanner /f   # remove it completely
```

`status.py` answers *is it running* from evidence — the heartbeat the scanner writes
into `state.json` after every scan, what Windows records about the task, and the log
tail — not from whether a terminal happens to be open.

**The task only runs while the laptop is on and you are logged in.** Sleep, shutdown
and logout all stop it; it picks the schedule back up on its own at the next 15-minute
slot after you log in. Nothing queues up in the meantime: the scanner reads the market
as it is *right now*, so a token that passed its gates at 3am while the lid was shut is
simply never seen. Continuous coverage means a machine that stays on — a cheap VPS,
or leaving this one awake.

---

## 3. The gates — what each one actually stops

Every gate is a hard reject. One failure and the token is never sent to you.

| Gate | What it prevents |
|---|---|
| **Mint authority revoked** | Dev printing unlimited new supply and dumping it on you. Read live from the Solana chain, not from a third party. |
| **Freeze authority revoked** | Dev freezing your wallet so you physically cannot sell. This is how "honeypots" work. |
| **LP locked/burned ≥ 90%** | The classic rug: dev pulls all liquidity, price goes to zero in one block. |
| **Top-10 hold < 25%** | A handful of wallets dumping and collapsing the chart. LP pools and lockers are excluded from this math, otherwise every locked token looks like a rug. |
| **Holders ≥ 250** | Tokens where the "community" is 20 wallets, all the dev's. |
| **Insiders in top-20 ≤ 2** | Bundled launches where insiders secretly hold the float. |
| **Liquidity $20k–$3M** | Too thin = you cannot exit without destroying the price. Too big = the move already happened. |
| **Vol/Liq 0.4–30x** | Below 0.4 = dead, nobody trading. Above 30x = almost always wash trading to fake activity. |
| **Age 20m–72h** | Skips the first minutes (pure sniper chaos) and anything past 3 days (momentum gone). |
| **1h buy ratio ≥ 42%** | Catches tokens already in active distribution. |
| **24h change ≤ +400%** | Buying the top. A token can pass every safety check above and still be up 790% on the day — clean, verified, and almost entirely behind you. Missing or unparseable data does not reject. Set to `null` to switch this gate off. |
| **RugCheck risk ≤ 40** | Independent second opinion on all of the above. |
| **Has socials** | Zero-effort launches with no account behind them. |
| **Telegram link not dead** | A declared Telegram that does not exist on t.me is not a missing signal — it is an active deception. Checked live against the real page. |
| **Telegram ≥ 150 members** | Channels of 12 wallets pretending to be a community. Private invite links publish no count and are not punished for it. |

Tune anything in `config.json`. Loosening gates means more alerts and more rugs —
that trade is yours to make, and it is a real trade, not a free win.

The momentum gate is the one to think hardest about. Raise `max_price_change_h24_pct`
and you will see more runners, later in their run. Lower it and you will see quieter
tokens earlier, most of which stay quiet. Neither setting is safer — they trade one
kind of loss for the other.

---

## 4. The score — how a survivor becomes a signal

Tokens that clear **every** gate above are scored twice, then combined:

```
safety ≥ 60                        ← hard floor, checked first
composite = 0.70 × safety + 0.30 × social   → alert at ≥ 60
```

**Safety (0–100)** — liquidity depth 20, holder breadth 15, top-10 concentration 20,
turnover health 15, buy pressure 15, RugCheck risk 15.

**Social (0–100)** — live Telegram 30, community size 3–25 by tier, X account age
0–20, X activity 0–15, website domain age 0–10. Every one of those is checked
against a live source. Nothing scores for merely *existing*.

Why social is weighted at all, and why Telegram carries most of it:

> Kim et al., *Pump.fun Graduation Regime Windows: Survival Analysis of 832,941
> Token Launches and the Social-Presence Effect* (arXiv:2607.02823) — tokens with
> a Telegram channel graduate at **1.485%** vs **0.166%** without: an **8.94× lift**,
> Cox hazard ratio **5.40** (95% CI 4.73–6.17), model concordance 0.858.

Read that honestly: **graduation is not profit.** It measures a token reaching a real
DEX rather than dying on the launchpad. The weight raises your odds of picking a live
token, not a winning trade.

Every alert prints the full breakdown — each component, points awarded out of max,
and the evidence behind it — so no point in the score is something you have to trust.

### Why it is balanced this way

**On-chain vs social.** The first version weighted social at 45%, and five live tokens
showed what that did: every decision was made by the social score. A token with 88/100
on-chain safety was blocked for having no Telegram, while the weakest on-chain token of
the five was sent on the strength of a 4,333-member chat room. Social is now a 30%
tiebreaker behind a hard safety floor, so no chat room can carry a weak token through.

**Presence vs evidence.** The first version also scored *presence*: 15 points for an X
link, 10 for a website, neither checked. Both were worthless, and one alert proved it.

> **The `401k` case.** It cleared every gate and was sent on 29 Aug at composite 61.5.
> Checked properly the next morning: the X account was created **the same day** with 3
> posts, the domain was registered **the same day**, and the token was **down 98%** with
> liquidity collapsed from $38k to $2k. It scored 25 social points for two things that
> were, on inspection, evidence of the opposite.

Under the current rules that token scores **social 0, composite 31.7** and never alerts.

| Signal | Old | Now |
|---|---|---|
| X link present | 15 pts, unchecked | 0 — presence is free to fake |
| X account 1 day old | not measured | 0 pts, printed on the alert |
| X account 1+ year old, active | not measured | up to 35 pts |
| X link that 404s | 15 pts | **hard reject** — declared and false |
| Website present | 10 pts, unchecked | 0 |
| Domain registered yesterday | not measured | 0 pts, printed on the alert |
| Domain 1+ year old | not measured | 10 pts |

**Age gates are off by default.** Nearly every legitimate meme coin launches with a
fresh account and a fresh domain, so rejecting on age rejects everything. The ages are
scored and printed on the card instead, with a warning mark, so you see what is actually
behind a token before you buy. Set `min_x_account_age_days` or `min_domain_age_days` in
`config.json` if you would rather gate than judge.

### What the social layer still cannot do

- **Follower quality.** A 50,000-follower account can be bought. Counts are scored in
  tiers and paired with post counts, which is harder to fake convincingly, but neither
  proves a real audience.
- **X communities.** A link to `x.com/i/communities/...` names no account, so there is
  nothing to verify. It scores zero and says so.
- **Reddit.** Needs OAuth; the public JSON endpoints return 403. Not wired in.
- **Discovery bias.** DexScreener's profile and boost feeds are where projects pay to
  appear. Tokens that never buy a boost are invisible to this scanner.
- **Content.** Nothing here reads what an account posts or who follows it.
- **Platform links are not websites.** A token whose "website" is an X community or a
  linktree scores zero for domain age - the registration date of `x.com` says nothing
  about the token. One live run awarded a perfect 10/10 for exactly that before it was
  caught.

### Bundled launches and deployer history

Two checks that cost nothing, because the data is already in the RugCheck report
the scanner fetches anyway - and two honest measurements of what they are worth:

**Insider clusters.** RugCheck links wallets that were funded together. A bundled
launch spreads the float across many small wallets so a top-10 check sees nothing,
while one person still controls it. The scanner now sums what those clusters hold
and rejects above 10% of supply.

> Measured over 22 live tokens: **23% had linked clusters**, and one (`Tuff`, 22.8%
> across 5 wallets) would have been rejected. Useful, but narrow - and it would
> **not** have caught `401k`, whose clusters held 0.48%.

**Deployer history.** `creatorTokens` says how many other tokens the deploying
wallet has launched. A wallet on its fortieth launch is a different proposition
from one on its first.

> Measured over the same 22 tokens: populated for **1 of 22**. At 5% coverage it
> cannot carry a decision, so `max_creator_tokens` is **0 (off)** by default. When
> the data is there it is printed on the card; when it is missing the deployer is
> never assumed clean. There is no free alternative - RugCheck exposes no creator
> endpoint (404) and pump.fun's API blocks unauthenticated reads (403).

### The track record

`track.py` records every signal with its entry price and re-prices it at +1h, +6h and
+24h. Near-misses - tokens that cleared every gate but scored below the threshold - are
recorded too, as a control group: if they do as well as the alerts, the threshold is
not earning its keep.

```bash
python track.py --report --all    # what the signals have been worth
python track.py --recent          # one line per recent signal
```

Or send `/record` to the bot. The record so far is one alert, `401k`, **down 98.9% at
24h** - which is the point of keeping it. A screener with no measured record is an
opinion.

**The record lives in git, not in a cache.** The cloud run commits `track.json` back
to this repo whenever it changes, so there is one copy, it survives cache eviction,
and you can read the whole history on GitHub. A record the tool could quietly lose
is not a record.

---

## 5. Data sources (all free, no API key)

- **DexScreener** `api.dexscreener.com` — pay-to-appear discovery feeds + market data
- **GeckoTerminal** `api.geckoterminal.com` — trending pools ranked by real trading,
  so tokens nobody paid to promote are visible too
- **RugCheck** `api.rugcheck.xyz` — holder distribution, LP lock status, risk flags
- **Solana RPC** `api.mainnet-beta.solana.com` — mint/freeze authority, straight from chain
- **Telegram** `t.me/<handle>` — channel existence + subscriber count, scraped from the public page
- **X/Twitter** `api.vxtwitter.com` **and** `api.fxtwitter.com` — account existence,
  creation date, followers, post count. Both are queried and their answers compared:
  asked for one handle at the same moment they returned *different account ids*, one
  created 12 Aug and one 28 Aug. When they disagree on which account holds a handle,
  the account scores zero rather than trusting either.
- **RDAP** `rdap.org` — the registry's own record of when a domain was registered

Every alert links back to RugCheck, Solscan and DexScreener so you can re-check
each number yourself before buying.

---

## 6. Read this before you trade

- Passing every gate means **"not obviously a scam."** It does not mean "goes up."
  The first live test of this scanner surfaced a token that passed all 14 checks
  and was **down 22% on the day**. That is normal and expected.
- Most meme coins go to zero. Position size accordingly — money you can lose entirely.
- The scanner does not sell for you. Decide your exit **before** you enter.
- Never buy a contract address someone DMs you. Paste every address into
  rugcheck.xyz yourself. That includes addresses from this tool.
