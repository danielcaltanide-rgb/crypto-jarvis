#!/usr/bin/env python3
"""
================================================================================
 JARVIS-SOL v1.0 — Multi-Source Solana Meme Coin PAPER TRADING Bot
================================================================================

 A single-file, fully self-contained rebuild of JARVIS V4, retargeted at
 Solana meme coins and wired to FOUR independent data sources so that one
 dead feed can never leave the scanner at "0 candidates" for a whole day.

 SOURCES
   pump.fun      unofficial frontend API   earliest launches, bonding curve %
   DexScreener   OFFICIAL public API       reliable liquidity / volume / price
   Moonshot      via DexScreener pairs     Moonshot launchpad coverage
   GMGN.ai       unofficial, fragile       holder counts, dev holdings

 ────────────────────────────────────────────────────────────────────────────
 ⚠  THIS BOT TRADES NOTHING REAL.
    open_position() / close_position() only mutate numbers in a local
    state.json file. There is no wallet, no private key, no RPC submission,
    no transaction of any kind anywhere in this file.

 ⚠  THREE OF THE FOUR SOURCES ARE UNOFFICIAL.
    Only DexScreener publishes a documented API. pump.fun and GMGN endpoints
    are reverse-engineered from their web apps and WILL break eventually.
    Every source is therefore optional and failures are non-fatal.

 ⚠  MEME COINS ARE EXTREME-VARIANCE INSTRUMENTS.
    The large majority of new launches go to zero, many within minutes. A
    high score here means "passed a fundamentals filter", NOT "will go up".

 ⚠  PAPER RESULTS WILL NOT MATCH LIVE RESULTS.
    Slippage, MEV/sandwich bots, priority fees, and failed transactions are
    real costs on Solana that this simulation does not model at all. Live
    performance on this style of strategy is typically materially worse than
    paper. Do not read a good paper equity curve as a validated edge.

 ⚠  ON THE $100/DAY TARGET.
    $100/day on a $1,000 account is a 10% DAILY return. It is displayed in
    /status as a reference line only and is deliberately NOT enforced. The
    bot will never loosen a filter or force an entry to reach it. Chasing a
    fixed daily dollar figure by relaxing entry criteria is the single most
    common way accounts running strategies like this get wiped out.
 ────────────────────────────────────────────────────────────────────────────

 SETUP
   pip install requests
   export TELEGRAM_BOT_TOKEN="123456:ABC-your-token-from-BotFather"
   export TELEGRAM_CHAT_ID="123456789"
   python jarvis_sol.py

 TELEGRAM COMMANDS
   /status      cash, equity, total & daily P/L, W/L, positions, settings
   /today       today's P/L, entries used, W/L, loss streak
   /positions   entry, qty, stop/TP levels and score per open position
   /sources     live health of each data feed (last result / last error)

================================================================================
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import date

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")


# ==============================================================================
# SECTION 1 — CONFIGURATION
# Everything tunable lives here. You should not need to edit anything below
# Section 1 for normal use.
# ==============================================================================

# ── Telegram credentials ──────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

# ── Paper account ─────────────────────────────────────────────────────────────
STARTING_CASH = 1000.00
DAILY_TARGET = 100.00        # Reference line shown in /status ONLY.
                             # 10%/day on $1000. Never enforced — see header.

# ── Risk management ───────────────────────────────────────────────────────────
RISK_PER_TRADE_PCT = 1.5     # % of equity risked per position. Position size is
                             # computed so a full stop-out loses exactly this.
STOP_LOSS_PCT = 25.0         # Wide on purpose. Your original 6% stop would be
                             # hit by ordinary meme coin noise within minutes.
TP1_PCT = 40.0               # First take-profit
TP2_PCT = 100.0              # Second take-profit (moon bag)
TP1_SELL_FRACTION = 0.5      # Sell this fraction at TP1, ride the rest to TP2

MAX_OPEN_POSITIONS = 2
MAX_ENTRIES_PER_DAY = 10
MAX_LOSS_STREAK = 2          # Pause NEW entries after N consecutive losses today

MIN_SCORE = 80               # 0-100. Minimum score required to enter.
                             # Raise = pickier. Lower = more trades, worse ones.

# ── Loop timing ───────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = 30
POSITION_CHECK_INTERVAL_SECONDS = 15
MAIN_LOOP_SLEEP_SECONDS = 2

# ── Hard filters (applied BEFORE scoring — fail any and the coin scores 0) ────
MIN_COIN_AGE_SECONDS = 60             # Skip the first minute: instant-rug window
MAX_COIN_AGE_SECONDS = 60 * 60 * 6    # "New launches" focus: ignore >6h old
MIN_MARKET_CAP_USD = 5_000
MIN_LIQUIDITY_USD = 3_000
MIN_UNIQUE_HOLDERS = 20
MAX_DEV_HOLDING_PCT = 15.0            # Reject if creator still holds this much
MIN_VOLUME_5M_USD = 1_000

# ── Data sources ──────────────────────────────────────────────────────────────
# Queried every cycle, merged and deduplicated by mint address. Remove a name
# to disable that feed. A source failing is logged and skipped, never fatal.
ENABLED_SOURCES = ["pumpfun", "dexscreener", "moonshot", "gmgn"]
FETCH_LIMIT_PER_SOURCE = 50

PUMPFUN_BASE_URL = "https://frontend-api.pump.fun"
DEXSCREENER_BASE_URL = "https://api.dexscreener.com"
GMGN_BASE_URL = "https://gmgn.ai"

HTTP_TIMEOUT = 10

# ── Persistence ───────────────────────────────────────────────────────────────
STATE_FILE = "state.json"
LOG_FILE = "jarvis_sol.log"
LOG_LEVEL = logging.INFO     # Set to logging.DEBUG to see every rejection reason


# ==============================================================================
# SECTION 2 — LOGGING
# ==============================================================================

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger("jarvis_sol")


# ==============================================================================
# SECTION 3 — NORMALIZED COIN SCHEMA
#
# Every source returns dicts in this exact shape. Fields a given source cannot
# provide are left at 0/empty, and the aggregator fills those gaps from other
# sources that can. This is what makes four heterogeneous feeds interchangeable.
# ==============================================================================

EMPTY_COIN_TEMPLATE = {
    "mint": "",                 # token mint address — the unique key
    "symbol": "???",
    "name": "Unknown",
    "created_ts": 0.0,          # unix seconds
    "market_cap_usd": 0.0,
    "liquidity_usd": 0.0,
    "volume_5m_usd": 0.0,
    "volume_24h_usd": 0.0,
    "unique_holders": 0,
    "dev_holding_pct": 0.0,
    "price_usd": 0.0,
    "bonding_curve_pct": 0.0,   # 0-100, launchpad-style tokens only
}


class SourceError(Exception):
    """Raised by a source when it cannot fetch or parse data.

    Caught per-source by the aggregator so one broken feed never aborts a scan.
    """

    def __init__(self, source: str, message: str):
        self.source = source
        super().__init__(f"[{source}] {message}")


def new_coin(**overrides) -> dict:
    coin = dict(EMPTY_COIN_TEMPLATE)
    coin["sources"] = set()
    coin.update(overrides)
    return coin


def age_seconds(coin: dict) -> float:
    if not coin.get("created_ts"):
        return 0.0
    return max(0.0, time.time() - coin["created_ts"])


def normalize_ts(ts) -> float:
    """Coerce a timestamp that may be in seconds or milliseconds into seconds."""
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return 0.0
    return ts / 1000.0 if ts > 1e12 else ts


def _f(value, default: float = 0.0) -> float:
    """Tolerant float coercion — API fields are frequently None or strings."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ==============================================================================
# SECTION 4 — SOURCE HEALTH TRACKING
#
# Records per-source outcomes so /sources and the logs tell you WHICH layer
# broke. Your original bot's core problem was that "0 candidates" gave you no
# way to distinguish "feed is down" from "filters too strict".
# ==============================================================================

class SourceHealth:
    def __init__(self):
        self.stats: dict[str, dict] = {}

    def record_success(self, source: str, count: int):
        s = self.stats.setdefault(source, {})
        s["last_ok_ts"] = time.time()
        s["last_count"] = count
        s["consecutive_failures"] = 0
        s["last_error"] = None

    def record_failure(self, source: str, error: str):
        s = self.stats.setdefault(source, {})
        s["last_fail_ts"] = time.time()
        s["last_error"] = error
        s["consecutive_failures"] = s.get("consecutive_failures", 0) + 1

    def summary(self) -> str:
        if not self.stats:
            return "No scan cycles completed yet."
        lines = []
        for name in ENABLED_SOURCES:
            s = self.stats.get(name)
            if not s:
                lines.append(f"• {name}: not yet queried")
                continue
            fails = s.get("consecutive_failures", 0)
            if fails == 0:
                age = time.time() - s.get("last_ok_ts", time.time())
                lines.append(f"• {name}: OK — {s.get('last_count', 0)} coins ({age:.0f}s ago)")
            else:
                err = (s.get("last_error") or "")[:90]
                lines.append(f"• {name}: FAILING x{fails} — {err}")
        return "\n".join(lines)


HEALTH = SourceHealth()


# ==============================================================================
# SECTION 5 — DATA SOURCE: pump.fun  (UNOFFICIAL)
#
# The launchpad itself, so it sees coins earliest and is the only reliable
# source of bonding-curve progress. Undocumented and unversioned: if this
# starts failing, open pump.fun in a browser with devtools -> Network, find
# the XHR that loads the coin list, and update the URL/fields below.
# ==============================================================================

_pf_session = requests.Session()
_pf_session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; JarvisSolBot/1.0)",
    "Accept": "application/json",
})


def pumpfun_fetch_new_launches(limit: int = 50) -> list[dict]:
    params = {
        "offset": 0,
        "limit": limit,
        "sort": "created_timestamp",
        "order": "DESC",
        "includeNsfw": "false",
    }
    try:
        resp = _pf_session.get(f"{PUMPFUN_BASE_URL}/coins", params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        raise SourceError("pumpfun", f"request failed: {e}") from e
    except ValueError as e:
        raise SourceError("pumpfun", f"non-JSON response (blocked or HTML error page?): {e}") from e

    if not isinstance(raw, list):
        raise SourceError("pumpfun", "unexpected response shape — API likely changed")

    coins = []
    for item in raw:
        try:
            coins.append(_pumpfun_normalize(item))
        except (KeyError, TypeError, ValueError) as e:
            log.debug("pumpfun: skipping malformed entry: %s", e)
    return coins


def pumpfun_fetch_detail(mint: str) -> dict:
    try:
        resp = _pf_session.get(f"{PUMPFUN_BASE_URL}/coins/{mint}", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        raise SourceError("pumpfun", f"detail request failed for {mint}: {e}") from e
    except ValueError as e:
        raise SourceError("pumpfun", f"non-JSON detail response for {mint}: {e}") from e
    return _pumpfun_normalize(raw)


def _pumpfun_normalize(item: dict) -> dict:
    return new_coin(
        mint=item.get("mint") or item.get("address") or "",
        symbol=item.get("symbol", "???"),
        name=item.get("name", "Unknown"),
        created_ts=normalize_ts(item.get("created_timestamp", 0)),
        market_cap_usd=_f(item.get("usd_market_cap", item.get("market_cap"))),
        liquidity_usd=_f(item.get("virtual_sol_reserves_usd", item.get("liquidity_usd"))),
        volume_5m_usd=_f(item.get("volume_5m_usd")),
        unique_holders=_i(item.get("holder_count", item.get("unique_holders"))),
        dev_holding_pct=_f(item.get("dev_holding_pct", item.get("creator_holding_pct"))),
        price_usd=_f(item.get("usd_price", item.get("price_usd"))),
        bonding_curve_pct=_f(item.get("bonding_curve_progress", item.get("progress"))),
        sources={"pumpfun"},
    )


# ==============================================================================
# SECTION 6 — DATA SOURCE: DexScreener  (OFFICIAL)
#
# The only documented API here — https://docs.dexscreener.com/api/reference
# Most stable feed and the best source of liquidity/volume/price truth, but it
# indexes liquidity POOLS, so it only sees a token once a pool exists. It will
# miss the first moments of a pump.fun launch. Treat it as the reliability
# backbone and enrichment layer rather than the earliest-discovery feed.
# ==============================================================================

_dex_session = requests.Session()
_dex_session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; JarvisSolBot/1.0)",
    "Accept": "application/json",
})


def dexscreener_fetch_new_launches(limit: int = 50) -> list[dict]:
    try:
        resp = _dex_session.get(
            f"{DEXSCREENER_BASE_URL}/token-profiles/latest/v1", timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        raise SourceError("dexscreener", f"request failed: {e}") from e
    except ValueError as e:
        raise SourceError("dexscreener", f"non-JSON response: {e}") from e

    if not isinstance(raw, list):
        raise SourceError("dexscreener", "unexpected response shape")

    sol_tokens = [t for t in raw if t.get("chainId") == "solana"][:limit]

    coins = []
    for t in sol_tokens:
        addr = t.get("tokenAddress")
        if not addr:
            continue
        try:
            coins.append(dexscreener_fetch_detail(addr))
        except SourceError as e:
            log.debug("dexscreener: skipping %s: %s", addr, e)
    return coins


def dexscreener_fetch_detail(mint: str) -> dict:
    try:
        resp = _dex_session.get(
            f"{DEXSCREENER_BASE_URL}/latest/dex/tokens/{mint}", timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        raise SourceError("dexscreener", f"detail request failed for {mint}: {e}") from e
    except ValueError as e:
        raise SourceError("dexscreener", f"non-JSON detail response for {mint}: {e}") from e

    sol_pairs = [p for p in (raw.get("pairs") or []) if p.get("chainId") == "solana"]
    if not sol_pairs:
        raise SourceError("dexscreener", f"no Solana pairs found for {mint}")

    # Deepest pool is the most representative quote
    best = max(sol_pairs, key=lambda p: _f((p.get("liquidity") or {}).get("usd")))
    return _dexscreener_normalize(best)


def _dexscreener_normalize(pair: dict) -> dict:
    base_token = pair.get("baseToken", {})
    liq = pair.get("liquidity") or {}
    vol = pair.get("volume") or {}
    return new_coin(
        mint=base_token.get("address", ""),
        symbol=base_token.get("symbol", "???"),
        name=base_token.get("name", "Unknown"),
        created_ts=normalize_ts(pair.get("pairCreatedAt", 0)),
        market_cap_usd=_f(pair.get("marketCap", pair.get("fdv"))),
        liquidity_usd=_f(liq.get("usd")),
        volume_5m_usd=_f(vol.get("m5")),
        volume_24h_usd=_f(vol.get("h24")),
        unique_holders=0,        # not exposed by DexScreener — gap-filled elsewhere
        dev_holding_pct=0.0,     # not exposed by DexScreener — gap-filled elsewhere
        price_usd=_f(pair.get("priceUsd")),
        bonding_curve_pct=0.0,   # not applicable once trading on a DEX pool
        sources={"dexscreener"},
    )


# ==============================================================================
# SECTION 7 — DATA SOURCE: Moonshot
#
# Moonshot (DexScreener's own launchpad) has no separate public API, so the
# practical route is DexScreener pair data filtered to dexId == "moonshot".
#
# NOTE: you asked for "Monday" — I read that as Moonshot. If you meant a
# different platform, this section is the one to swap: keep the two function
# names and the normalized dict shape and nothing else needs to change.
# ==============================================================================

def moonshot_fetch_new_launches(limit: int = 50) -> list[dict]:
    try:
        resp = _dex_session.get(
            f"{DEXSCREENER_BASE_URL}/latest/dex/search",
            params={"q": "moonshot"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        raise SourceError("moonshot", f"request failed: {e}") from e
    except ValueError as e:
        raise SourceError("moonshot", f"non-JSON response: {e}") from e

    pairs = [
        p for p in (raw.get("pairs") or [])
        if p.get("chainId") == "solana" and p.get("dexId") == "moonshot"
    ]
    pairs.sort(key=lambda p: _f(p.get("pairCreatedAt")), reverse=True)

    coins = []
    for p in pairs[:limit]:
        try:
            coin = _dexscreener_normalize(p)
            coin["sources"] = {"moonshot"}
            coins.append(coin)
        except (KeyError, TypeError, ValueError) as e:
            log.debug("moonshot: skipping malformed entry: %s", e)
    return coins


def moonshot_fetch_detail(mint: str) -> dict:
    try:
        resp = _dex_session.get(
            f"{DEXSCREENER_BASE_URL}/latest/dex/tokens/{mint}", timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        raise SourceError("moonshot", f"detail request failed for {mint}: {e}") from e
    except ValueError as e:
        raise SourceError("moonshot", f"non-JSON detail response for {mint}: {e}") from e

    pairs = [p for p in (raw.get("pairs") or []) if p.get("dexId") == "moonshot"]
    if not pairs:
        raise SourceError("moonshot", f"no Moonshot pair found for {mint}")
    coin = _dexscreener_normalize(pairs[0])
    coin["sources"] = {"moonshot"}
    return coin


# ==============================================================================
# SECTION 8 — DATA SOURCE: GMGN.ai  (UNOFFICIAL — MOST FRAGILE)
#
# GMGN publishes no public API. The endpoints below are reverse-engineered from
# their web app and they actively rate-limit and block scripted traffic. Expect
# this one to need fixing before the others do.
#
# TO REPAIR:
#   1. Open gmgn.ai/trend/sol in a browser with devtools -> Network tab.
#   2. Find the XHR that populates the new-pairs / trending list.
#   3. Update the URL path, params, and _gmgn_normalize() field names to match.
#
# Because of this fragility the aggregator treats GMGN failure as routine: the
# scan simply proceeds on whatever the other three returned.
# ==============================================================================

_gmgn_session = requests.Session()
_gmgn_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://gmgn.ai/trend/sol",
})


def gmgn_fetch_new_launches(limit: int = 50) -> list[dict]:
    url = f"{GMGN_BASE_URL}/defi/quotation/v1/rank/sol/pairs/new"
    params = {"limit": limit, "orderby": "open_timestamp", "direction": "desc"}
    try:
        resp = _gmgn_session.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        raise SourceError("gmgn", f"request failed (endpoint likely changed/blocked): {e}") from e
    except ValueError as e:
        raise SourceError("gmgn", f"non-JSON response (blocked or HTML error page): {e}") from e

    items = (raw.get("data") or {}).get("pairs") or raw.get("data") or []
    if not isinstance(items, list):
        raise SourceError("gmgn", "unexpected response shape — GMGN changed their API")

    coins = []
    for item in items:
        try:
            coins.append(_gmgn_normalize(item))
        except (KeyError, TypeError, ValueError) as e:
            log.debug("gmgn: skipping malformed entry: %s", e)
    return coins


def gmgn_fetch_detail(mint: str) -> dict:
    url = f"{GMGN_BASE_URL}/defi/quotation/v1/tokens/sol/{mint}"
    try:
        resp = _gmgn_session.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
    except requests.RequestException as e:
        raise SourceError("gmgn", f"detail request failed for {mint}: {e}") from e
    except ValueError as e:
        raise SourceError("gmgn", f"non-JSON detail response for {mint}: {e}") from e
    return _gmgn_normalize(raw.get("data") or raw)


def _gmgn_normalize(item: dict) -> dict:
    return new_coin(
        mint=item.get("address") or item.get("token_address") or "",
        symbol=item.get("symbol", "???"),
        name=item.get("name", "Unknown"),
        created_ts=normalize_ts(item.get("open_timestamp", item.get("created_timestamp", 0))),
        market_cap_usd=_f(item.get("market_cap", item.get("usd_market_cap"))),
        liquidity_usd=_f(item.get("liquidity")),
        volume_5m_usd=_f(item.get("volume_5m")),
        volume_24h_usd=_f(item.get("volume_24h")),
        unique_holders=_i(item.get("holder_count")),
        dev_holding_pct=_f(item.get("dev_holding_percentage", item.get("creator_holding_pct"))),
        price_usd=_f(item.get("price", item.get("price_usd"))),
        bonding_curve_pct=_f(item.get("bonding_curve_progress")),
        sources={"gmgn"},
    )


# ==============================================================================
# SECTION 9 — AGGREGATOR
#
# Queries every enabled source, merges results by mint address, and fills gaps
# in one feed's data using another's. pump.fun and GMGN typically supply holder
# count and dev holdings; DexScreener supplies trustworthy liquidity and price.
# Combined, you get a fuller picture of each coin than any single feed gives.
# ==============================================================================

SOURCE_REGISTRY = {
    "pumpfun":     (pumpfun_fetch_new_launches,     pumpfun_fetch_detail),
    "dexscreener": (dexscreener_fetch_new_launches, dexscreener_fetch_detail),
    "moonshot":    (moonshot_fetch_new_launches,    moonshot_fetch_detail),
    "gmgn":        (gmgn_fetch_new_launches,        gmgn_fetch_detail),
}

# Gap-fill order for detail lookups: most reliable price source first.
DETAIL_PRIORITY = ["dexscreener", "pumpfun", "moonshot", "gmgn"]


class AllSourcesFailedError(Exception):
    """Raised only when EVERY enabled source failed in the same cycle."""


def get_new_launches(limit_per_source: int = FETCH_LIMIT_PER_SOURCE) -> list[dict]:
    merged: dict[str, dict] = {}
    any_success = False
    errors: list[str] = []

    for name in ENABLED_SOURCES:
        entry = SOURCE_REGISTRY.get(name)
        if entry is None:
            log.warning("Unknown source '%s' in ENABLED_SOURCES — skipping", name)
            continue
        fetch_list, _ = entry
        try:
            coins = fetch_list(limit=limit_per_source)
            any_success = True
            HEALTH.record_success(name, len(coins))
            log.info("%s: %d coins fetched", name, len(coins))
        except SourceError as e:
            HEALTH.record_failure(name, str(e))
            log.warning("Source failed, continuing without it: %s", e)
            errors.append(str(e))
            continue
        except Exception as e:  # noqa: BLE001 — one bad feed must never kill a scan
            HEALTH.record_failure(name, f"unexpected: {e}")
            log.exception("Unexpected error in source '%s'", name)
            errors.append(f"[{name}] unexpected: {e}")
            continue

        for coin in coins:
            if coin.get("mint"):
                _merge_into(merged, coin)

    if not any_success:
        raise AllSourcesFailedError("All enabled sources failed: " + "; ".join(errors))

    return list(merged.values())


def get_coin_detail(mint: str, preferred_source: str | None = None) -> dict:
    """Fetch fresh data for one mint, trying sources in priority order.

    Used to mark open positions to market. Falls through every source before
    giving up, so a single dead feed cannot strand a position unmonitored.
    """
    order = [s for s in DETAIL_PRIORITY if s in ENABLED_SOURCES]
    if preferred_source and preferred_source in order:
        order = [preferred_source] + [s for s in order if s != preferred_source]

    last_error = None
    for name in order:
        entry = SOURCE_REGISTRY.get(name)
        if entry is None:
            continue
        _, fetch_detail = entry
        try:
            return fetch_detail(mint)
        except SourceError as e:
            last_error = e
            continue
        except Exception as e:  # noqa: BLE001
            last_error = e
            log.exception("Unexpected error fetching detail from '%s'", name)
            continue

    raise AllSourcesFailedError(f"No source could fetch detail for {mint}: {last_error}")


def _merge_into(merged: dict, coin: dict):
    """Merge a coin into the accumulator, filling gaps rather than overwriting.

    A zero from a source that simply does not track a field must never clobber
    a real value from a source that does — hence the `not existing.get(...)`
    guard on every field.
    """
    mint = coin["mint"]
    if mint not in merged:
        merged[mint] = coin
        return

    existing = merged[mint]
    existing["sources"] |= coin["sources"]

    for f in (
        "market_cap_usd", "liquidity_usd", "volume_5m_usd", "volume_24h_usd",
        "unique_holders", "dev_holding_pct", "price_usd", "bonding_curve_pct",
    ):
        if not existing.get(f) and coin.get(f):
            existing[f] = coin[f]

    # Earliest known creation time wins — it's the most conservative for age filters
    if coin.get("created_ts") and (
        not existing.get("created_ts") or coin["created_ts"] < existing["created_ts"]
    ):
        existing["created_ts"] = coin["created_ts"]

    if existing.get("symbol") in (None, "???", "") and coin.get("symbol"):
        existing["symbol"] = coin["symbol"]
    if existing.get("name") in (None, "Unknown", "") and coin.get("name"):
        existing["name"] = coin["name"]


# ==============================================================================
# SECTION 10 — SCORING ENGINE
#
# Hard filters first (cheap, auto-reject), then a weighted 0-100 score.
#
# This is a heuristic, not a predictive model. It is designed to screen out
# obviously bad setups — thin liquidity, concentrated dev holdings, no volume.
# It cannot tell you a coin will go up, and no threshold value makes it able to.
# ==============================================================================

@dataclass
class ScoreResult:
    total: float
    passed: bool
    breakdown: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)


def score_coin(coin: dict) -> ScoreResult:
    breakdown: dict[str, float] = {}
    reasons: list[str] = []

    # ── Hard filters ──────────────────────────────────────────────────────────
    age = age_seconds(coin)
    if age < MIN_COIN_AGE_SECONDS:
        return ScoreResult(0, False, {}, [f"too new ({age:.0f}s old)"])
    if age > MAX_COIN_AGE_SECONDS:
        return ScoreResult(0, False, {}, [f"too old ({age / 3600:.1f}h old)"])
    if coin["market_cap_usd"] < MIN_MARKET_CAP_USD:
        return ScoreResult(0, False, {}, ["market cap below minimum"])
    if coin["liquidity_usd"] < MIN_LIQUIDITY_USD:
        return ScoreResult(0, False, {}, ["liquidity below minimum"])
    # Holder count is only available from pump.fun/GMGN. If NO source reported
    # it we cannot verify it — rather than auto-passing, the coin just scores 0
    # in that bucket, which on its own usually keeps it under MIN_SCORE.
    if coin["unique_holders"] and coin["unique_holders"] < MIN_UNIQUE_HOLDERS:
        return ScoreResult(0, False, {}, ["too few holders"])
    if coin["dev_holding_pct"] > MAX_DEV_HOLDING_PCT:
        return ScoreResult(0, False, {}, ["dev holds too much supply — rug risk"])
    if coin["volume_5m_usd"] < MIN_VOLUME_5M_USD:
        return ScoreResult(0, False, {}, ["5m volume below minimum"])

    # ── Weighted scoring, 100 points total ───────────────────────────────────

    # Liquidity depth (25) — deeper pool is harder to rug and easier to exit
    breakdown["liquidity"] = _scale(coin["liquidity_usd"], MIN_LIQUIDITY_USD, 50_000, 25)

    # Holder count (20) — distribution and organic-interest proxy
    breakdown["holders"] = _scale(coin["unique_holders"], MIN_UNIQUE_HOLDERS, 500, 20)

    # Dev holding (20) — inverted: less creator supply is better
    breakdown["dev_holding"] = _scale_inverted(coin["dev_holding_pct"], 0, MAX_DEV_HOLDING_PCT, 20)

    # Volume momentum (20) — 5m volume relative to market cap
    vol_ratio = coin["volume_5m_usd"] / max(coin["market_cap_usd"], 1)
    breakdown["volume_momentum"] = _scale(vol_ratio, 0.01, 0.5, 20)

    # Bonding curve progress (5) — mild bonus for a validated launch
    breakdown["bonding_curve"] = _scale(coin["bonding_curve_pct"], 0, 80, 5)

    # Source corroboration (10) — appearing on multiple independent feeds is
    # weak evidence the token is real and indexed rather than a ghost entry
    breakdown["source_corroboration"] = _scale(len(coin.get("sources") or set()), 1, 3, 10)

    total = round(sum(breakdown.values()), 1)
    passed = total >= MIN_SCORE
    if not passed:
        reasons.append(f"scored {total}/100, below MIN_SCORE ({MIN_SCORE})")

    return ScoreResult(total, passed, breakdown, reasons)


def _scale(value: float, lo: float, hi: float, cap: float) -> float:
    """Linearly map value from [lo, hi] onto [0, cap], clamped at both ends."""
    if hi <= lo:
        return 0.0
    frac = max(0.0, min(1.0, (value - lo) / (hi - lo)))
    return round(frac * cap, 2)


def _scale_inverted(value: float, lo: float, hi: float, cap: float) -> float:
    """As _scale, but a lower raw value earns a higher score."""
    return round(cap - _scale(value, lo, hi, cap), 2)


# ==============================================================================
# SECTION 11 — PAPER PORTFOLIO
#
# Tracks simulated cash, positions, and P&L, persisted to JSON so a restart
# does not wipe the account. Nothing in this section touches a wallet, a key,
# or an RPC endpoint. Every "trade" is arithmetic on local numbers.
# ==============================================================================

@dataclass
class Position:
    mint: str
    symbol: str
    entry_price: float
    qty: float
    cost_basis: float
    entry_ts: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    tp1_hit: bool = False
    score: float = 0.0


@dataclass
class DailyStats:
    day: str = field(default_factory=lambda: str(date.today()))
    entries_today: int = 0
    pnl_today: float = 0.0
    loss_streak: int = 0
    wins_today: int = 0
    losses_today: int = 0


class Portfolio:
    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.cash = STARTING_CASH
        self.positions: dict[str, Position] = {}
        self.total_pnl = 0.0
        self.total_wins = 0
        self.total_losses = 0
        self.daily = DailyStats()
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────
    def _load(self):
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            self.cash = data.get("cash", STARTING_CASH)
            self.total_pnl = data.get("total_pnl", 0.0)
            self.total_wins = data.get("total_wins", 0)
            self.total_losses = data.get("total_losses", 0)
            self.positions = {m: Position(**p) for m, p in data.get("positions", {}).items()}
            daily_data = data.get("daily", {})
            self.daily = DailyStats(**daily_data) if daily_data else DailyStats()
            self._roll_day_if_needed()
            log.info("Loaded state: cash $%.2f, %d open positions", self.cash, len(self.positions))
        except (json.JSONDecodeError, TypeError, KeyError) as e:
            log.warning("Could not load %s (%s) — starting fresh", self.state_file, e)

    def _save(self):
        data = {
            "cash": self.cash,
            "total_pnl": self.total_pnl,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "positions": {m: asdict(p) for m, p in self.positions.items()},
            "daily": asdict(self.daily),
        }
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.state_file)   # atomic — never leaves a half-written state

    def _roll_day_if_needed(self):
        today = str(date.today())
        if self.daily.day != today:
            log.info("New day — resetting daily counters (entries, loss streak, daily P&L)")
            self.daily = DailyStats(day=today)
            self._save()

    # ── Equity and entry gating ──────────────────────────────────────────────
    def equity(self, current_prices: dict[str, float] | None = None) -> float:
        value = self.cash
        current_prices = current_prices or {}
        for mint, pos in self.positions.items():
            value += pos.qty * current_prices.get(mint, pos.entry_price)
        return round(value, 2)

    def can_enter_new_position(self) -> tuple[bool, str]:
        self._roll_day_if_needed()
        if len(self.positions) >= MAX_OPEN_POSITIONS:
            return False, f"max open positions reached ({MAX_OPEN_POSITIONS})"
        if self.daily.entries_today >= MAX_ENTRIES_PER_DAY:
            return False, f"max daily entries reached ({MAX_ENTRIES_PER_DAY})"
        if self.daily.loss_streak >= MAX_LOSS_STREAK:
            return False, f"loss streak limit hit ({MAX_LOSS_STREAK}) — paused until tomorrow"
        return True, ""

    # ── Simulated execution ──────────────────────────────────────────────────
    def open_position(self, coin: dict, score: float) -> Position | None:
        ok, reason = self.can_enter_new_position()
        if not ok:
            log.info("Skipping entry for %s: %s", coin["symbol"], reason)
            return None

        price = coin["price_usd"]
        if price <= 0:
            log.warning("Skipping %s: invalid price (%s)", coin["symbol"], price)
            return None

        # Risk-based sizing: size so a full stop-out loses exactly
        # RISK_PER_TRADE_PCT of equity, regardless of the coin's price.
        risk_amount = self.equity() * (RISK_PER_TRADE_PCT / 100.0)
        stop_price = price * (1 - STOP_LOSS_PCT / 100.0)
        loss_per_token = price - stop_price
        qty = risk_amount / loss_per_token if loss_per_token > 0 else 0
        cost = qty * price

        if cost > self.cash:                 # never spend more cash than we hold
            qty = self.cash / price
            cost = qty * price

        if qty <= 0 or cost <= 0:
            log.warning("Skipping %s: computed zero-size position", coin["symbol"])
            return None

        pos = Position(
            mint=coin["mint"],
            symbol=coin["symbol"],
            entry_price=price,
            qty=qty,
            cost_basis=cost,
            entry_ts=time.time(),
            stop_price=stop_price,
            tp1_price=price * (1 + TP1_PCT / 100.0),
            tp2_price=price * (1 + TP2_PCT / 100.0),
            score=score,
        )
        self.cash -= cost
        self.positions[coin["mint"]] = pos
        self.daily.entries_today += 1
        self._save()
        log.info("OPEN %s qty=%.4f @ $%.8f cost=$%.2f score=%.1f",
                 pos.symbol, qty, price, cost, score)
        return pos

    def close_position(self, mint: str, exit_price: float,
                       fraction: float = 1.0, reason: str = "") -> float:
        """Close all or part of a position. Returns realized P&L for the part sold."""
        pos = self.positions.get(mint)
        if not pos:
            return 0.0

        fraction = max(0.0, min(1.0, fraction))
        qty_sold = pos.qty * fraction
        proceeds = qty_sold * exit_price
        cost_of_sold = pos.cost_basis * fraction
        pnl = proceeds - cost_of_sold

        self.cash += proceeds
        self.total_pnl += pnl
        self.daily.pnl_today += pnl

        if pnl >= 0:
            self.total_wins += 1
            self.daily.wins_today += 1
            self.daily.loss_streak = 0
        else:
            self.total_losses += 1
            self.daily.losses_today += 1
            self.daily.loss_streak += 1

        symbol = pos.symbol
        if fraction >= 1.0:
            del self.positions[mint]
        else:
            pos.qty -= qty_sold
            pos.cost_basis -= cost_of_sold

        self._save()
        log.info("CLOSE %.0f%% %s @ $%.8f (%s) P&L $%+.2f",
                 fraction * 100, symbol, exit_price, reason, pnl)
        return pnl

    def mark_tp1_hit(self, mint: str):
        if mint in self.positions:
            self.positions[mint].tp1_hit = True
            self._save()

    def win_rate(self) -> float:
        total = self.total_wins + self.total_losses
        return round(100 * self.total_wins / total, 1) if total else 0.0


# ==============================================================================
# SECTION 12 — TELEGRAM INTERFACE
# ==============================================================================

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def tg_send(text: str, chat_id: str | None = None):
    chat_id = chat_id or TELEGRAM_CHAT_ID
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=HTTP_TIMEOUT,
        )
        if not resp.ok:
            log.error("Telegram send failed: %s", resp.text)
    except requests.RequestException as e:
        log.error("Telegram send error: %s", e)


def fmt_status(pf: Portfolio, status: str = "RESEARCHING") -> str:
    return (
        "🤖 *JARVIS SOL — PAPER*\n\n"
        f"Cash: ${pf.cash:.2f}\n"
        f"Equity: ${pf.equity():.2f}\n"
        f"Total P/L: ${pf.total_pnl:+.2f}\n"
        f"Today's P/L: ${pf.daily.pnl_today:+.2f}\n\n"
        f"Wins: {pf.total_wins}\n"
        f"Losses: {pf.total_losses}\n"
        f"Win rate: {pf.win_rate()}%\n\n"
        f"Open positions: {len(pf.positions)}/{MAX_OPEN_POSITIONS}\n"
        f"Entries: {pf.daily.entries_today}/{MAX_ENTRIES_PER_DAY}\n"
        f"Loss streak: {pf.daily.loss_streak}/{MAX_LOSS_STREAK}\n\n"
        f"Risk/trade: {RISK_PER_TRADE_PCT}%\n"
        f"Stop: {STOP_LOSS_PCT}%\n"
        f"TP1: +{TP1_PCT}%\n"
        f"TP2: +{TP2_PCT}%\n"
        f"Minimum score: {MIN_SCORE}/100\n\n"
        f"Daily target: ${DAILY_TARGET:.2f} _(reference only — not enforced)_\n\n"
        f"Sources: {', '.join(ENABLED_SOURCES)}\n"
        f"Status: 🟢 {status}\n"
        f"Mode: 🧪 PAPER TRADING"
    )


def fmt_today(pf: Portfolio) -> str:
    d = pf.daily
    return (
        "🤖 *JARVIS SOL — TODAY*\n\n"
        f"P&L: ${d.pnl_today:+.2f}\n"
        f"Target: +${DAILY_TARGET:.2f}\n\n"
        f"Entries: {d.entries_today}/{MAX_ENTRIES_PER_DAY}\n"
        f"W/L: {d.wins_today}/{d.losses_today}\n"
        f"Loss streak: {d.loss_streak}/{MAX_LOSS_STREAK}"
    )


def fmt_positions(pf: Portfolio) -> str:
    if not pf.positions:
        return "🤖 *JARVIS SOL — POSITIONS*\n\nNo open positions."
    lines = ["🤖 *JARVIS SOL — POSITIONS*\n"]
    for pos in pf.positions.values():
        lines.append(
            f"*{pos.symbol}*\n"
            f"  Entry: ${pos.entry_price:.8f} | Qty: {pos.qty:,.2f}\n"
            f"  Stop: ${pos.stop_price:.8f}\n"
            f"  TP1: ${pos.tp1_price:.8f} ({'hit' if pos.tp1_hit else 'pending'})\n"
            f"  TP2: ${pos.tp2_price:.8f}\n"
            f"  Score: {pos.score}/100\n"
        )
    return "\n".join(lines)


def fmt_sources() -> str:
    return "🤖 *JARVIS SOL — SOURCE HEALTH*\n\n" + HEALTH.summary()


def fmt_entry(coin: dict, score: ScoreResult, pos: Position) -> str:
    srcs = ", ".join(sorted(coin.get("sources") or []))
    return (
        f"🟢 *ENTRY: {coin['symbol']}*\n\n"
        f"Price: ${pos.entry_price:.8f}\n"
        f"Qty: {pos.qty:,.2f}\n"
        f"Cost: ${pos.cost_basis:.2f}\n"
        f"Score: {score.total}/100\n"
        f"Seen on: {srcs}\n\n"
        f"Stop: ${pos.stop_price:.8f} (-{STOP_LOSS_PCT}%)\n"
        f"TP1: ${pos.tp1_price:.8f} (+{TP1_PCT}%)\n"
        f"TP2: ${pos.tp2_price:.8f} (+{TP2_PCT}%)\n\n"
        f"Mint: `{coin['mint']}`"
    )


def fmt_exit(symbol: str, price: float, pnl: float, fraction: float, reason: str) -> str:
    emoji = "🟢" if pnl >= 0 else "🔴"
    label = "FULL" if fraction >= 1.0 else f"{int(fraction * 100)}%"
    return (
        f"{emoji} *EXIT ({label}): {symbol}*\n\n"
        f"Price: ${price:.8f}\n"
        f"P&L: ${pnl:+.2f}\n"
        f"Reason: {reason}"
    )


class CommandPoller:
    """Long-polls Telegram getUpdates for slash commands."""

    def __init__(self, portfolio: Portfolio, status_provider):
        self.pf = portfolio
        self.status_provider = status_provider
        self._offset = 0

    def poll_once(self):
        try:
            resp = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"offset": self._offset, "timeout": 10},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            log.error("getUpdates failed: %s", e)
            return

        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            msg = update.get("message", {})
            text = (msg.get("text") or "").strip()
            chat_id = msg.get("chat", {}).get("id")
            if text and chat_id:
                self._handle(text, str(chat_id))

    def _handle(self, text: str, chat_id: str):
        cmd = text.lower().split()[0].split("@")[0]
        if cmd == "/status":
            tg_send(fmt_status(self.pf, self.status_provider()), chat_id)
        elif cmd == "/today":
            tg_send(fmt_today(self.pf), chat_id)
        elif cmd == "/positions":
            tg_send(fmt_positions(self.pf), chat_id)
        elif cmd == "/sources":
            tg_send(fmt_sources(), chat_id)
        else:
            tg_send("Commands: /status /today /positions /sources", chat_id)


# ==============================================================================
# SECTION 13 — SCAN AND MONITOR LOOPS
# ==============================================================================

_current_status = "RESEARCHING"


def scan_for_entries(pf: Portfolio):
    global _current_status

    ok, reason = pf.can_enter_new_position()
    if not ok:
        _current_status = "PAUSED"
        log.info("Not scanning for entries: %s", reason)
        return

    _current_status = "RESEARCHING"
    try:
        candidates = get_new_launches()
    except AllSourcesFailedError as e:
        _current_status = "SOURCES DOWN"
        log.error("Scan failed — every source errored: %s", e)
        return

    log.info("Fetched %d unique candidates across %s",
             len(candidates), ", ".join(ENABLED_SOURCES))

    if not candidates:
        # Distinguishing this from "candidates rejected" is exactly what your
        # original bot could not do. This log line answers it directly.
        log.warning("Sources responded but returned zero coins — check /sources")
        return

    held = set(pf.positions.keys())
    rejected = 0
    best_seen = 0.0

    for coin in candidates:
        if not coin.get("mint") or coin["mint"] in held:
            continue

        result = score_coin(coin)
        best_seen = max(best_seen, result.total)

        if not result.passed:
            rejected += 1
            log.debug("%s rejected (%.1f): %s",
                      coin["symbol"], result.total, "; ".join(result.reasons))
            continue

        log.info("%s PASSED %.1f/100 — opening position", coin["symbol"], result.total)
        pos = pf.open_position(coin, result.total)
        if pos:
            tg_send(fmt_entry(coin, result, pos))
            _current_status = "IN POSITION"

        if not pf.can_enter_new_position()[0]:
            break

    if rejected:
        log.info("Rejected %d candidates this cycle (best score seen: %.1f, need %d)",
                 rejected, best_seen, MIN_SCORE)


def monitor_positions(pf: Portfolio):
    if not pf.positions:
        return

    for mint in list(pf.positions.keys()):
        pos = pf.positions.get(mint)
        if not pos:
            continue

        try:
            live = get_coin_detail(mint)
        except AllSourcesFailedError as e:
            log.warning("No live price for %s: %s", pos.symbol, e)
            continue

        price = live["price_usd"]
        if price <= 0:
            continue

        # Stop loss — checked first, so a candle that spans stop and TP is
        # treated conservatively as a loss rather than optimistically as a win.
        if price <= pos.stop_price:
            pnl = pf.close_position(mint, price, 1.0, "stop loss hit")
            tg_send(fmt_exit(pos.symbol, price, pnl, 1.0, "stop loss"))
            continue

        # TP1 — take partial profit, let the remainder run
        if not pos.tp1_hit and price >= pos.tp1_price:
            pnl = pf.close_position(mint, price, TP1_SELL_FRACTION, "TP1 hit")
            pf.mark_tp1_hit(mint)
            tg_send(fmt_exit(pos.symbol, price, pnl, TP1_SELL_FRACTION, "TP1"))
            continue

        # TP2 — close the remainder
        if pos.tp1_hit and price >= pos.tp2_price:
            pnl = pf.close_position(mint, price, 1.0, "TP2 hit")
            tg_send(fmt_exit(pos.symbol, price, pnl, 1.0, "TP2"))
            continue


# ==============================================================================
# SECTION 14 — ENTRY POINT
# ==============================================================================

def preflight() -> bool:
    problems = []
    if TELEGRAM_BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        problems.append("TELEGRAM_BOT_TOKEN is not set")
    if TELEGRAM_CHAT_ID == "PUT_YOUR_CHAT_ID_HERE":
        problems.append("TELEGRAM_CHAT_ID is not set")
    if not ENABLED_SOURCES:
        problems.append("ENABLED_SOURCES is empty — nothing to scan")

    if problems:
        print("\n".join("  ✗ " + p for p in problems))
        print("\n  export TELEGRAM_BOT_TOKEN=\"...\"   # from @BotFather")
        print("  export TELEGRAM_CHAT_ID=\"...\"     # from @userinfobot\n")
        return False
    return True


def main():
    if not preflight():
        sys.exit(1)

    pf = Portfolio()
    poller = CommandPoller(pf, status_provider=lambda: _current_status)

    log.info("=" * 70)
    log.info("JARVIS-SOL starting — PAPER TRADING, no real funds at risk")
    log.info("Cash $%.2f | Min score %d | Max positions %d | Sources: %s",
             pf.cash, MIN_SCORE, MAX_OPEN_POSITIONS, ", ".join(ENABLED_SOURCES))
    log.info("=" * 70)

    tg_send(fmt_status(pf, "STARTING"))

    last_scan = 0.0
    last_check = 0.0

    try:
        while True:
            now = time.time()

            if now - last_scan >= SCAN_INTERVAL_SECONDS:
                try:
                    scan_for_entries(pf)
                except Exception:
                    log.exception("Unhandled error in scan_for_entries")
                last_scan = now

            if now - last_check >= POSITION_CHECK_INTERVAL_SECONDS:
                try:
                    monitor_positions(pf)
                except Exception:
                    log.exception("Unhandled error in monitor_positions")
                last_check = now

            try:
                poller.poll_once()
            except Exception:
                log.exception("Unhandled error in command poller")

            time.sleep(MAIN_LOOP_SLEEP_SECONDS)

    except KeyboardInterrupt:
        log.info("Shutting down. State saved to %s", STATE_FILE)
        tg_send("🛑 *JARVIS SOL stopped.* State saved.")


if __name__ == "__main__":
    main()
