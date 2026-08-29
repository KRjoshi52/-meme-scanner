# Solana Meme-Coin Screener → Telegram

Finds Solana tokens that survive a hard safety screen, then pushes the **contract
address plus verifiable proof** to your Telegram. Built for Phantom wallet.

It does **not** predict price. It filters out the tokens that are structurally
designed to take your money, so you spend your attention on the small remainder.

---

## 1. Create your Telegram bot (3 minutes)

1. Open Telegram, search **@BotFather**, send `/newbot`
2. Pick a name and a username ending in `bot`
3. BotFather replies with a token like `8123456789:AAH...` — copy it
4. Send **any message** to your new bot (this opens the chat so it can reply)
5. Get your chat id — paste your token into this URL in a browser:

   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```

   Find `"chat":{"id":123456789` — that number is your `chat_id`.

Put both into `config.json`:

```json
"telegram": {
  "bot_token": "8123456789:AAH...",
  "chat_id": "123456789"
}
```

Test it:

```bash
python scanner.py --test-telegram
```

---

## 2. Run it

```bash
python scanner.py --dry-run --verbose   # see everything, send nothing
python scanner.py                       # one scan, sends real alerts
python scanner.py --loop                # continuous, every 15 min
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

**Social (0–100)** — live Telegram 40, community size 4–30 by tier, X/Twitter link 15,
website 10, Discord 5.

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

The first version weighted social at 45%, and five live tokens showed what that
actually did: every single decision was made by the social score. A token with 88/100
on-chain safety was blocked for having no Telegram, while the weakest on-chain token
of the five was sent on the strength of a 4,333-member chat room.

Both of those are the wrong answer. The fix is two-part — social demoted to a
tiebreaker at 30%, and a hard safety floor applied *before* the blend so no chat room,
however large, can carry a weak token through. The same five tokens under the
current rules:

| Token | Safety | Social | Composite | Verdict |
|---|---|---|---|---|
| apesemfone | 88.1 | 25 | 69.2 | **alert** |
| 401k | 78.1 | 25 | 62.2 | **alert** |
| GIGAAPE | 53.4 | 90 | 64.4 | blocked by safety floor |
| cat | 59.1 | 25 | 48.9 | blocked by safety floor |
| POKEMON | 59.1 | 15 | 45.9 | blocked by safety floor |

On-chain quality decides. Social only separates tokens that already earned their way
past the floor. Expect roughly one or two alerts per hour of scanning in a normal
market, and none at all in a quiet one.

---

## 5. Data sources (all free, no API key)

- **DexScreener** `api.dexscreener.com` — discovery feeds + live market data
- **RugCheck** `api.rugcheck.xyz` — holder distribution, LP lock status, risk flags
- **Solana RPC** `api.mainnet-beta.solana.com` — mint/freeze authority, straight from chain
- **Telegram** `t.me/<handle>` — channel existence + subscriber count, scraped from the public page

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
