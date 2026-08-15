#!/usr/bin/env python3
"""
JARVIS SOL v2.0 — Railway-ready Solana meme-coin PAPER trader.

This program never signs or submits a blockchain transaction. It discovers
new Pump.fun tokens through PumpPortal, waits for a real Solana DEX pool, then
uses DexScreener data to simulate entries and exits. Paper fills include
configurable slippage and fees so results are less optimistic.

Important: PumpPortal's new-token stream is free. Its token-trade stream is
metered and requires an API key. It is OFF by default in this program.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import html
import json
import logging
import math
import os
import signal
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import websockets
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes
from websockets.exceptions import ConnectionClosed

# ======================================================================================
# CONFIGURATION
# ======================================================================================

# Auto-restart policy for the background loops. A crashed loop is restarted
# rather than shutting the bot down, so JARVIS stays online non-stop.
RESTART_BACKOFF_MIN_SECONDS = 5.0
RESTART_BACKOFF_MAX_SECONDS = 300.0
RESTART_HEALTHY_SECONDS = 120.0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def limit_label(value: float, suffix: str = "") -> str:
    """Render a configured limit. Zero means the gate is switched off."""

    if not value:
        return "off"
    if float(value).is_integer():
        return f"{int(value)}{suffix}"
    return f"{value:g}{suffix}"


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw.strip())
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    value = default if raw is None else float(raw.strip())
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclasses.dataclass(frozen=True)
class Settings:
    # Required Telegram settings.
    telegram_bot_token: str
    telegram_chat_id: str

    # PumpPortal. New-token events are free. Metered trade events are opt-in.
    pumpportal_api_key: str
    enable_metered_trade_stream: bool

    # Paper account and risk limits. Percent values are human percentages.
    starting_bankroll_usd: float
    daily_profit_target_usd: float
    stop_after_daily_target: bool
    risk_per_trade_pct: float
    max_position_pct: float
    max_open_positions: int
    max_entries_per_day: int
    max_daily_loss_pct: float
    max_consecutive_losses: int

    # Exit rules.
    stop_loss_pct: float
    tp1_pct: float
    tp1_sell_fraction_pct: float
    tp2_pct: float
    trailing_stop_pct: float
    max_hold_seconds: int

    # Realism assumptions for simulated execution.
    paper_slippage_bps: float
    paper_fee_bps: float

    # Candidate lifecycle and hard filters.
    observation_seconds: int
    candidate_ttl_seconds: int
    dex_retry_seconds: int
    min_liquidity_usd: float
    liquidity_drain_exit_pct: float
    enable_wallet_tracking: bool
    # Rug safety pre-entry checks.
    rug_checks_enabled: bool
    solana_rpc_url: str
    require_mint_authority_revoked: bool
    require_freeze_authority_revoked: bool
    max_top_holder_concentration_pct: float
    top_holder_count: int
    rug_check_timeout_seconds: int
    whale_min_sol: float
    whale_alerts_enabled: bool
    whale_alert_cooldown_seconds: int
    whale_buys_to_track: int
    tracked_wallets_file: Path
    smart_money_window_seconds: int
    smart_money_score_bonus: float
    smart_money_skip_observation: bool
    min_liquidity_to_mcap_pct: float
    min_pool_age_seconds: int
    min_market_cap_usd: float
    max_market_cap_usd: float
    min_volume_5m_usd: float
    min_buys_5m: int
    min_buy_sell_ratio: float
    min_price_change_5m_pct: float
    max_price_change_5m_pct: float
    min_score: int
    max_candidates: int

    # Runtime.
    monitor_interval_seconds: int
    evaluator_interval_seconds: int
    heartbeat_interval_seconds: int
    stale_price_alert_seconds: int
    http_timeout_seconds: int
    timezone_name: str
    state_file: Path

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(
            os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "."
        )
        configured_state = os.getenv("STATE_FILE", "").strip()
        state_file = (
            Path(configured_state)
            if configured_state
            else data_dir / "jarvis_state.json"
        )

        settings = cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            pumpportal_api_key=os.getenv("PUMPPORTAL_API_KEY", "").strip(),
            enable_metered_trade_stream=_env_bool("ENABLE_METERED_TRADE_STREAM", False),
            starting_bankroll_usd=_env_float("STARTING_BANKROLL_USD", 5000.0, 1.0),
            daily_profit_target_usd=_env_float("DAILY_PROFIT_TARGET_USD", 150.0, 0.0),
            stop_after_daily_target=_env_bool("STOP_AFTER_DAILY_TARGET", False),
            risk_per_trade_pct=_env_float("RISK_PER_TRADE_PCT", 0.5, 0.01),
            max_position_pct=_env_float("MAX_POSITION_PCT", 1.5, 0.1),
            max_open_positions=_env_int("MAX_OPEN_POSITIONS", 3, 1),
            # Non-stop defaults: 0 means the gate is disabled entirely.
            # Set any of these above 0 to switch the brake back on.
            max_entries_per_day=_env_int("MAX_ENTRIES_PER_DAY", 0, 0),
            max_daily_loss_pct=_env_float("MAX_DAILY_LOSS_PCT", 0.0, 0.0),
            max_consecutive_losses=_env_int("MAX_CONSECUTIVE_LOSSES", 0, 0),
            stop_loss_pct=_env_float("STOP_LOSS_PCT", 10.0, 0.1),
            tp1_pct=_env_float("TP1_PCT", 18.0, 0.1),
            tp1_sell_fraction_pct=_env_float("TP1_SELL_FRACTION_PCT", 50.0, 1.0),
            tp2_pct=_env_float("TP2_PCT", 45.0, 0.1),
            trailing_stop_pct=_env_float("TRAILING_STOP_PCT", 10.0, 0.1),
            max_hold_seconds=_env_int("MAX_HOLD_SECONDS", 45 * 60, 60),
            paper_slippage_bps=_env_float("PAPER_SLIPPAGE_BPS", 150.0, 0.0),
            paper_fee_bps=_env_float("PAPER_FEE_BPS", 100.0, 0.0),
            observation_seconds=_env_int("OBSERVATION_SECONDS", 120, 30),
            candidate_ttl_seconds=_env_int("CANDIDATE_TTL_SECONDS", 15 * 60, 60),
            dex_retry_seconds=_env_int("DEX_RETRY_SECONDS", 30, 5),
            min_liquidity_usd=_env_float("MIN_LIQUIDITY_USD", 4_000.0, 0.0),
            liquidity_drain_exit_pct=_env_float(
                "LIQUIDITY_DRAIN_EXIT_PCT", 45.0, 0.0
            ),
            enable_wallet_tracking=_env_bool("ENABLE_WALLET_TRACKING", False),
            rug_checks_enabled=_env_bool("RUG_CHECKS_ENABLED", True),
            solana_rpc_url=os.getenv(
                "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
            ).strip(),
            require_mint_authority_revoked=_env_bool(
                "REQUIRE_MINT_AUTHORITY_REVOKED", True
            ),
            require_freeze_authority_revoked=_env_bool(
                "REQUIRE_FREEZE_AUTHORITY_REVOKED", True
            ),
            max_top_holder_concentration_pct=_env_float(
                "MAX_TOP_HOLDER_CONCENTRATION_PCT", 25.0, 0.1
            ),
            top_holder_count=_env_int("TOP_HOLDER_COUNT", 10, 1),
            rug_check_timeout_seconds=_env_int("RUG_CHECK_TIMEOUT_SECONDS", 8, 1),
            whale_min_sol=_env_float("WHALE_MIN_SOL", 2.0, 0.01),
            whale_alerts_enabled=_env_bool("WHALE_ALERTS_ENABLED", True),
            whale_alert_cooldown_seconds=_env_int(
                "WHALE_ALERT_COOLDOWN_SECONDS", 120, 0
            ),
            whale_buys_to_track=_env_int("WHALE_BUYS_TO_TRACK", 3, 1),
            tracked_wallets_file=Path(
                os.getenv("TRACKED_WALLETS_FILE", "/data/tracked_wallets.json")
            ),
            smart_money_window_seconds=_env_int(
                "SMART_MONEY_WINDOW_SECONDS", 600, 60
            ),
            smart_money_score_bonus=_env_float("SMART_MONEY_SCORE_BONUS", 12.0, 0.0),
            smart_money_skip_observation=_env_bool(
                "SMART_MONEY_SKIP_OBSERVATION", True
            ),
            min_liquidity_to_mcap_pct=_env_float(
                "MIN_LIQUIDITY_TO_MCAP_PCT", 4.0, 0.0
            ),
            min_pool_age_seconds=_env_int("MIN_POOL_AGE_SECONDS", 180, 0),
            min_market_cap_usd=_env_float("MIN_MARKET_CAP_USD", 8_000.0, 0.0),
            max_market_cap_usd=_env_float("MAX_MARKET_CAP_USD", 1_500_000.0, 1.0),
            min_volume_5m_usd=_env_float("MIN_VOLUME_5M_USD", 1_500.0, 0.0),
            min_buys_5m=_env_int("MIN_BUYS_5M", 10, 0),
            min_buy_sell_ratio=_env_float("MIN_BUY_SELL_RATIO", 1.2, 0.0),
            min_price_change_5m_pct=_env_float("MIN_PRICE_CHANGE_5M_PCT", -15.0),
            max_price_change_5m_pct=_env_float("MAX_PRICE_CHANGE_5M_PCT", 80.0),
            min_score=_env_int("MIN_SCORE", 65, 0),
            max_candidates=_env_int("MAX_CANDIDATES", 500, 10),
            monitor_interval_seconds=_env_int("MONITOR_INTERVAL_SECONDS", 10, 3),
            evaluator_interval_seconds=_env_int("EVALUATOR_INTERVAL_SECONDS", 5, 1),
            heartbeat_interval_seconds=_env_int("HEARTBEAT_INTERVAL_SECONDS", 300, 30),
            stale_price_alert_seconds=_env_int("STALE_PRICE_ALERT_SECONDS", 180, 30),
            http_timeout_seconds=_env_int("HTTP_TIMEOUT_SECONDS", 10, 3),
            timezone_name=os.getenv("TIMEZONE", "Australia/Sydney").strip(),
            state_file=state_file,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        problems: list[str] = []
        if not self.telegram_bot_token or ":" not in self.telegram_bot_token:
            problems.append("TELEGRAM_BOT_TOKEN is missing or malformed")
        if not self.telegram_chat_id:
            problems.append("TELEGRAM_CHAT_ID is missing")
        if self.enable_metered_trade_stream and not self.pumpportal_api_key:
            problems.append(
                "ENABLE_METERED_TRADE_STREAM=true requires PUMPPORTAL_API_KEY"
            )
        if self.risk_per_trade_pct > 5:
            problems.append("RISK_PER_TRADE_PCT must be 5 or less")
        if self.max_position_pct > 100:
            problems.append("MAX_POSITION_PCT must be 100 or less")
        if self.max_daily_loss_pct > 100:
            problems.append("MAX_DAILY_LOSS_PCT must be 100 or less")
        if self.rug_checks_enabled and not self.solana_rpc_url.startswith("http"):
            problems.append("SOLANA_RPC_URL must be an http(s) URL")
        if not 0 < self.max_top_holder_concentration_pct <= 100:
            problems.append(
                "MAX_TOP_HOLDER_CONCENTRATION_PCT must be between 0 and 100"
            )
        if self.stop_loss_pct >= 100:
            problems.append("STOP_LOSS_PCT must be below 100")
        if self.tp1_pct >= self.tp2_pct:
            problems.append("TP2_PCT must be greater than TP1_PCT")
        if not 0 < self.tp1_sell_fraction_pct < 100:
            problems.append("TP1_SELL_FRACTION_PCT must be between 0 and 100")
        if self.min_market_cap_usd >= self.max_market_cap_usd:
            problems.append("MAX_MARKET_CAP_USD must exceed MIN_MARKET_CAP_USD")
        if self.min_price_change_5m_pct >= self.max_price_change_5m_pct:
            problems.append(
                "MAX_PRICE_CHANGE_5M_PCT must exceed MIN_PRICE_CHANGE_5M_PCT"
            )
        if not 0 <= self.min_score <= 100:
            problems.append("MIN_SCORE must be between 0 and 100")
        try:
            ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            problems.append(f"Unknown TIMEZONE: {self.timezone_name}")
        if problems:
            raise ValueError("; ".join(problems))


SETTINGS = Settings.from_env()
LOCAL_TZ = ZoneInfo(SETTINGS.timezone_name)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("jarvis")


def local_day(ts: float | None = None) -> str:
    moment = datetime.fromtimestamp(ts or time.time(), tz=LOCAL_TZ)
    return moment.date().isoformat()


def pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def money(value: float) -> str:
    return f"${value:,.2f}"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


# ======================================================================================
# MARKET DATA MODELS AND SCORING
# ======================================================================================


@dataclasses.dataclass
class PairData:
    mint: str
    symbol: str
    name: str
    price_usd: float
    liquidity_usd: float
    market_cap_usd: float
    volume_5m_usd: float
    volume_1h_usd: float
    volume_24h_usd: float
    buys_5m: int
    sells_5m: int
    buys_1h: int
    sells_1h: int
    price_change_5m_pct: float
    price_change_1h_pct: float
    pair_created_at: float
    pair_address: str
    dex_id: str
    fetched_at: float

    @property
    def buy_sell_ratio_5m(self) -> float:
        return self.buys_5m / max(self.sells_5m, 1)


@dataclasses.dataclass
class Candidate:
    mint: str
    symbol: str
    name: str
    creator: str
    created_at: float
    next_check_at: float
    expires_at: float
    trade_events: deque[tuple[float, str, str]] = dataclasses.field(
        default_factory=lambda: deque(maxlen=2000)
    )

    def record_trade(self, side: str, trader: str) -> None:
        self.trade_events.append((time.time(), side, trader or "unknown"))

    def metered_stats(self, window_seconds: int = 300) -> tuple[int, int, int]:
        cutoff = time.time() - window_seconds
        recent = [event for event in self.trade_events if event[0] >= cutoff]
        buys = [event for event in recent if event[1] == "buy"]
        sells = [event for event in recent if event[1] == "sell"]
        unique_buyers = len({event[2] for event in buys})
        return len(buys), len(sells), unique_buyers


@dataclasses.dataclass
class ScoreResult:
    score: int
    passed: bool
    code: str
    summary: str
    breakdown: dict[str, float] = dataclasses.field(default_factory=dict)
    metrics: dict[str, float] = dataclasses.field(default_factory=dict)


def _linear(value: float, low: float, high: float, points: float) -> float:
    if high <= low:
        return 0.0
    fraction = max(0.0, min(1.0, (value - low) / (high - low)))
    return fraction * points


def _market_cap_score(value: float, settings: Settings) -> float:
    """Prefer early but established pools; penalize chasing large caps."""
    peak = min(150_000.0, settings.max_market_cap_usd * 0.35)
    peak = max(peak, settings.min_market_cap_usd * 2)
    if value <= peak:
        return 5.0 + _linear(value, settings.min_market_cap_usd, peak, 10.0)
    decline = _linear(value, peak, settings.max_market_cap_usd, 15.0)
    return max(0.0, 15.0 - decline)


def _momentum_score(change: float, settings: Settings) -> float:
    """Reward positive momentum without rewarding vertical, late entries."""
    if change < 0:
        return _linear(change, settings.min_price_change_5m_pct, 0.0, 3.0)
    if change <= 25:
        return 3.0 + _linear(change, 0.0, 25.0, 7.0)
    penalty = _linear(change, 25.0, settings.max_price_change_5m_pct, 10.0)
    return max(0.0, 10.0 - penalty)


# Rejections that can never improve with time -> drop the candidate.
# Everything else is a "not yet" and gets re-checked until the TTL expires.
TERMINAL_REJECT_CODES = frozenset({"high_market_cap"})


def score_candidate(
    candidate: Candidate,
    pair: PairData,
    settings: Settings = SETTINGS,
    smart_buyers: int = 0,
) -> ScoreResult:
    if pair.price_usd <= 0:
        return ScoreResult(
            0, False, "invalid_price", "no usable USD price",
            metrics={"price_usd": pair.price_usd},
        )
    if pair.liquidity_usd < settings.min_liquidity_usd:
        return ScoreResult(
            0, False, "low_liquidity",
            f"liquidity {money(pair.liquidity_usd)} below minimum",
            metrics={"liquidity_usd": pair.liquidity_usd},
        )
    pool_age = time.time() - pair.pair_created_at / 1000.0 if pair.pair_created_at else 0.0
    if settings.min_pool_age_seconds and 0 < pool_age < settings.min_pool_age_seconds:
        # Buying in the first seconds is the sniper lane; we cannot win it, and
        # it is where the instant rugs live.
        return ScoreResult(
            0, False, "pool_too_new", f"pool only {pool_age:.0f}s old",
            metrics={"pool_age_seconds": pool_age},
        )
    if pair.market_cap_usd > 0 and settings.min_liquidity_to_mcap_pct > 0:
        liq_ratio = 100.0 * pair.liquidity_usd / pair.market_cap_usd
        if liq_ratio < settings.min_liquidity_to_mcap_pct:
            # A big cap sitting on a thin pool cannot be exited at anything
            # near the quoted price.
            return ScoreResult(
                0, False, "thin_pool_vs_mcap",
                f"liquidity is only {liq_ratio:.1f}% of market cap",
                metrics={"liquidity_to_mcap_pct": liq_ratio},
            )
    if pair.market_cap_usd < settings.min_market_cap_usd:
        return ScoreResult(
            0, False, "low_market_cap",
            f"market cap {money(pair.market_cap_usd)} below minimum",
            metrics={"market_cap_usd": pair.market_cap_usd},
        )
    if pair.market_cap_usd > settings.max_market_cap_usd:
        return ScoreResult(
            0, False, "high_market_cap",
            f"market cap {money(pair.market_cap_usd)} above maximum",
            metrics={"market_cap_usd": pair.market_cap_usd},
        )
    if pair.volume_5m_usd < settings.min_volume_5m_usd:
        return ScoreResult(
            0, False, "low_volume",
            f"5m volume {money(pair.volume_5m_usd)} below minimum",
            metrics={"volume_5m_usd": pair.volume_5m_usd},
        )
    if pair.buys_5m < settings.min_buys_5m:
        return ScoreResult(
            0, False, "low_buys", f"only {pair.buys_5m} buys in 5m",
            metrics={"buys_5m": float(pair.buys_5m)},
        )
    if pair.buy_sell_ratio_5m < settings.min_buy_sell_ratio:
        return ScoreResult(
            0, False, "weak_buy_pressure",
            f"buy/sell {pair.buy_sell_ratio_5m:.2f} too low",
            metrics={"buy_sell_ratio_5m": pair.buy_sell_ratio_5m},
        )
    if pair.price_change_5m_pct < settings.min_price_change_5m_pct:
        return ScoreResult(
            0, False, "falling_fast",
            f"5m move {pair.price_change_5m_pct:+.1f}% too negative",
            metrics={"price_change_5m_pct": pair.price_change_5m_pct},
        )
    if pair.price_change_5m_pct > settings.max_price_change_5m_pct:
        return ScoreResult(
            0, False, "chasing_spike",
            f"5m move {pair.price_change_5m_pct:+.1f}% too extended",
            metrics={"price_change_5m_pct": pair.price_change_5m_pct},
        )

    breakdown = {
        "liquidity": _linear(
            pair.liquidity_usd, settings.min_liquidity_usd, 75_000.0, 20.0
        ),
        "volume": _linear(
            pair.volume_5m_usd, settings.min_volume_5m_usd, 60_000.0, 20.0
        ),
        "buy_pressure": _linear(
            pair.buy_sell_ratio_5m, settings.min_buy_sell_ratio, 3.0, 20.0
        ),
        "transactions": _linear(pair.buys_5m, settings.min_buys_5m, 60.0, 15.0),
        "market_cap": _market_cap_score(pair.market_cap_usd, settings),
        "momentum": _momentum_score(pair.price_change_5m_pct, settings),
    }

    if settings.enable_metered_trade_stream:
        _, _, unique_buyers = candidate.metered_stats()
        # A small corroboration bonus only; the official DEX metrics still carry
        # the decision so a WebSocket gap cannot manufacture a false pass.
        breakdown["unique_buyers_bonus"] = _linear(unique_buyers, 3, 20, 5.0)

    if smart_buyers > 0 and settings.smart_money_score_bonus > 0:
        breakdown["smart_money"] = min(
            settings.smart_money_score_bonus,
            settings.smart_money_score_bonus * smart_buyers / 2.0,
        )

    total = min(100, round(sum(breakdown.values())))
    passed = total >= settings.min_score
    summary = (
        f"score {total}/100 | liq {money(pair.liquidity_usd)} | "
        f"vol5m {money(pair.volume_5m_usd)} | "
        f"B/S {pair.buy_sell_ratio_5m:.2f} | m5 {pair.price_change_5m_pct:+.1f}%"
    )
    return ScoreResult(
        total,
        passed,
        "passed" if passed else "low_score",
        summary,
        {key: round(value, 2) for key, value in breakdown.items()},
        metrics={"score": float(total)},
    )


# ======================================================================================
# RUG SAFETY CHECKS
# ======================================================================================
#
# Run against the Solana RPC just before a paper entry. These catch the most
# mechanical rugs:
#
#   - the dev can still mint unlimited new supply
#   - the dev can still freeze your wallet so you cannot sell
#   - a handful of non-pool wallets hold enough supply to dump price to zero
#
# What they do NOT catch is a dev pulling liquidity out of the pool -- the
# TSLAI "liquidity drain" case. That needs DEX-specific LP-burn data; see
# check_lp_burned() below, which is deliberately left unimplemented rather
# than faked. Nothing here makes a meme coin safe.


@dataclasses.dataclass
class RugCheck:
    """Outcome of the pre-entry safety checks on one mint."""

    mint: str
    passed: bool = False
    reasons: list[str] = dataclasses.field(default_factory=list)
    details: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def code(self) -> str:
        """Short label for the funnel counters and /rejects output."""
        return self.reasons[0] if self.reasons else "ok"

    @property
    def summary(self) -> str:
        if self.passed:
            return "rug checks passed"
        parts = [", ".join(self.reasons)]
        concentration = self.details.get("top_holder_concentration")
        if concentration is not None:
            parts.append(f"top holders {concentration * 100:.1f}%")
        return " | ".join(parts)


async def _rug_rpc(
    session: aiohttp.ClientSession,
    settings: Settings,
    method: str,
    params: list[Any],
) -> Any:
    """Single Solana JSON-RPC call. Raises on transport or RPC error."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    timeout = aiohttp.ClientTimeout(total=settings.rug_check_timeout_seconds)
    async with session.post(
        settings.solana_rpc_url, json=payload, timeout=timeout
    ) as response:
        response.raise_for_status()
        body = await response.json()
    if "error" in body:
        raise RuntimeError(f"RPC {method} error: {body['error']}")
    return body.get("result")


async def _check_authorities(
    session: aiohttp.ClientSession,
    settings: Settings,
    mint: str,
    out: RugCheck,
) -> None:
    """Verify the mint and freeze authorities have been revoked."""
    result = await _rug_rpc(
        session,
        settings,
        "getAccountInfo",
        [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}],
    )
    value = (result or {}).get("value")
    if not value:
        out.reasons.append("mint_account_not_found")
        return

    try:
        info = value["data"]["parsed"]["info"]
    except (KeyError, TypeError):
        out.reasons.append("mint_account_unparsable")
        return

    mint_authority = info.get("mintAuthority")
    freeze_authority = info.get("freezeAuthority")
    out.details["mint_authority"] = mint_authority
    out.details["freeze_authority"] = freeze_authority

    if settings.require_mint_authority_revoked and mint_authority:
        out.reasons.append("mint_authority_active")
    if settings.require_freeze_authority_revoked and freeze_authority:
        out.reasons.append("freeze_authority_active")


async def _check_holder_concentration(
    session: aiohttp.ClientSession,
    settings: Settings,
    mint: str,
    out: RugCheck,
) -> None:
    """Measure how much supply sits with the top non-pool wallets."""
    supply_result = await _rug_rpc(
        session, settings, "getTokenSupply", [mint, {"commitment": "confirmed"}]
    )
    supply_value = (supply_result or {}).get("value")
    if not supply_value:
        out.reasons.append("supply_unavailable")
        return

    total_supply = float(supply_value.get("amount") or 0)
    if total_supply <= 0:
        out.reasons.append("zero_supply")
        return

    largest = await _rug_rpc(
        session,
        settings,
        "getTokenLargestAccounts",
        [mint, {"commitment": "confirmed"}],
    )
    accounts = (largest or {}).get("value") or []
    if not accounts:
        out.reasons.append("holders_unavailable")
        return

    amounts = sorted((float(a.get("amount") or 0) for a in accounts), reverse=True)

    # The single biggest account is almost always the pool or bonding curve,
    # not a wallet that can dump on you. Exclude it from the risk measure.
    if len(amounts) > 1:
        out.details["pool_share"] = round(amounts[0] / total_supply, 4)
        holder_amounts = amounts[1:]
    else:
        holder_amounts = amounts

    top = holder_amounts[: settings.top_holder_count]
    concentration = sum(top) / total_supply
    out.details["top_holder_concentration"] = round(concentration, 4)
    out.details["holders_examined"] = len(top)

    if concentration * 100 > settings.max_top_holder_concentration_pct:
        out.reasons.append("holder_concentration")


async def check_lp_burned(
    session: aiohttp.ClientSession, settings: Settings, pool_address: str
) -> bool | None:
    """
    NOT IMPLEMENTED -- the hook for catching liquidity-pull rugs.

    To fill this in: resolve the pool's LP token mint, then compare its
    current supply against the amount sent to a burn address. Burned or
    locked LP means the dev cannot withdraw the liquidity.

    Returns None (unknown) until implemented, and callers treat unknown as
    unknown rather than as a pass, so this never silently approves anything.
    """
    return None


async def check_rug_safety(
    session: aiohttp.ClientSession | None, settings: Settings, mint: str
) -> RugCheck:
    """
    Run the pre-entry safety checks on one mint.

    Fails closed: if the RPC errors or times out, the token is rejected
    rather than waved through. A missed trade costs nothing; a rug costs a
    day of winners.
    """
    out = RugCheck(mint=mint)
    if not settings.rug_checks_enabled:
        out.passed = True
        return out

    if session is None or session.closed:
        out.reasons.append("rug_check_no_session")
        return out

    try:
        await _check_authorities(session, settings, mint, out)
        await _check_holder_concentration(session, settings, mint, out)
    except Exception as exc:
        log.warning("rug check failed for %s: %s", mint, exc)
        out.reasons.append("rug_check_error")
        out.details["error"] = str(exc)

    out.passed = not out.reasons
    return out


# ======================================================================================
# PERSISTENT PAPER ACCOUNT
# ======================================================================================


STATE_VERSION = 2


@dataclasses.dataclass
class Position:
    mint: str
    symbol: str
    pair_address: str
    dex_id: str
    opened_at: float
    observed_entry_price: float
    entry_fill_price: float
    initial_qty: float
    remaining_qty: float
    initial_cost: float
    cost_remaining: float
    entry_fee: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    peak_price: float
    score: int
    tp1_hit: bool = False
    realized_pnl: float = 0.0
    last_price: float = 0.0
    last_quote_at: float = 0.0
    stale_alert_sent: bool = False
    entry_liquidity_usd: float = 0.0

    def return_pct(self, observed_price: float) -> float:
        if self.entry_fill_price <= 0:
            return 0.0
        return observed_price / self.entry_fill_price - 1.0


@dataclasses.dataclass
class DailyStats:
    day: str
    start_equity: float
    entries: int = 0
    realized_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0


@dataclasses.dataclass
class CloseResult:
    symbol: str
    fraction: float
    observed_price: float
    fill_price: float
    qty_sold: float
    exit_fee: float
    realized_pnl: float
    position_total_pnl: float
    fully_closed: bool
    reason: str


def _dataclass_from_dict(cls, raw: dict[str, Any]):
    allowed = {field.name for field in dataclasses.fields(cls)}
    return cls(**{key: value for key, value in raw.items() if key in allowed})


class PaperAccount:
    def __init__(self, settings: Settings = SETTINGS):
        self.settings = settings
        self.cash = settings.starting_bankroll_usd
        self.open_positions: dict[str, Position] = {}
        self.closed_positions: list[dict[str, Any]] = []
        self.total_realized_pnl = 0.0
        self.total_wins = 0
        self.total_losses = 0
        self.paused = False
        self.daily = DailyStats(local_day(), settings.starting_bankroll_usd)

    @classmethod
    def load(cls, settings: Settings = SETTINGS) -> PaperAccount:
        account = cls(settings)
        path = settings.state_file
        if not path.exists():
            return account
        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            account.cash = safe_float(raw.get("cash"), settings.starting_bankroll_usd)
            account.open_positions = {
                mint: _dataclass_from_dict(Position, item)
                for mint, item in (raw.get("open_positions") or {}).items()
            }
            account.closed_positions = list(raw.get("closed_positions") or [])[-500:]
            account.total_realized_pnl = safe_float(raw.get("total_realized_pnl"))
            account.total_wins = int(raw.get("total_wins", 0))
            account.total_losses = int(raw.get("total_losses", 0))
            account.paused = bool(raw.get("paused", False))
            daily_raw = raw.get("daily") or {}
            account.daily = _dataclass_from_dict(DailyStats, daily_raw)
            if account.daily.day != local_day():
                account.roll_day()
            log.info(
                "state restored | cash=%s open=%d closed=%d",
                money(account.cash),
                len(account.open_positions),
                len(account.closed_positions),
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ) as error:
            log.error("state file could not be loaded; starting fresh: %s", error)
        return account

    def _exit_value(self, position: Position, observed_price: float) -> float:
        slip = self.settings.paper_slippage_bps / 10_000.0
        fee = self.settings.paper_fee_bps / 10_000.0
        fill = max(0.0, observed_price * (1.0 - slip))
        gross = position.remaining_qty * fill
        return gross * (1.0 - fee)

    def equity(self) -> float:
        value = self.cash
        for position in self.open_positions.values():
            price = position.last_price or position.entry_fill_price
            value += self._exit_value(position, price)
        return round(value, 8)

    def daily_pnl(self) -> float:
        return self.equity() - self.daily.start_equity

    def roll_day(self) -> DailyStats | None:
        today = local_day()
        if self.daily.day == today:
            return None
        previous = dataclasses.replace(self.daily)
        self.daily = DailyStats(today, self.equity())
        self.save()
        return previous

    def can_open(self) -> tuple[bool, str]:
        self.roll_day()
        if self.paused:
            return False, "manually paused"
        if len(self.open_positions) >= self.settings.max_open_positions:
            return False, "max_open_positions"
        # Each limit below is skipped when set to 0, so the bot keeps trading.
        if (
            self.settings.max_entries_per_day
            and self.daily.entries >= self.settings.max_entries_per_day
        ):
            return False, "max_daily_entries"
        if (
            self.settings.max_consecutive_losses
            and self.daily.consecutive_losses >= self.settings.max_consecutive_losses
        ):
            return False, "loss_streak_limit"
        if self.settings.max_daily_loss_pct:
            loss_limit = (
                self.daily.start_equity * self.settings.max_daily_loss_pct / 100.0
            )
            if self.daily_pnl() <= -loss_limit:
                return False, "daily_loss_limit"
        if (
            self.settings.stop_after_daily_target
            and self.daily_pnl() >= self.settings.daily_profit_target_usd
        ):
            return False, "daily_target_reached"
        if self.cash <= 0:
            return False, "no_cash"
        return True, ""

    def open_position(self, pair: PairData, score: int) -> Position | None:
        allowed, reason = self.can_open()
        if not allowed:
            log.info("entry blocked for %s: %s", pair.symbol, reason)
            return None
        if pair.mint in self.open_positions or pair.price_usd <= 0:
            return None

        equity = self.equity()
        risk_budget = equity * self.settings.risk_per_trade_pct / 100.0
        stop = self.settings.stop_loss_pct / 100.0
        slip = self.settings.paper_slippage_bps / 10_000.0
        fee = self.settings.paper_fee_bps / 10_000.0

        # Estimated full-stop loss includes entry fee, exit fee and exit slippage.
        stop_recovery = (1.0 - stop) * (1.0 - slip) * (1.0 - fee)
        loss_fraction = max(0.0001, 1.0 - stop_recovery / (1.0 + fee))
        notional_by_risk = risk_budget / loss_fraction
        notional_cap = equity * self.settings.max_position_pct / 100.0
        affordable_notional = self.cash / (1.0 + fee)
        notional = min(notional_by_risk, notional_cap, affordable_notional)
        if notional < 1.0:
            log.info("entry blocked for %s: paper position below $1", pair.symbol)
            return None

        fill_price = pair.price_usd * (1.0 + slip)
        qty = notional / fill_price
        entry_fee = notional * fee
        total_cost = notional + entry_fee

        position = Position(
            mint=pair.mint,
            symbol=pair.symbol or "?",
            pair_address=pair.pair_address,
            dex_id=pair.dex_id,
            opened_at=time.time(),
            observed_entry_price=pair.price_usd,
            entry_fill_price=fill_price,
            initial_qty=qty,
            remaining_qty=qty,
            initial_cost=total_cost,
            cost_remaining=total_cost,
            entry_fee=entry_fee,
            stop_price=fill_price * (1.0 - stop),
            tp1_price=fill_price * (1.0 + self.settings.tp1_pct / 100.0),
            tp2_price=fill_price * (1.0 + self.settings.tp2_pct / 100.0),
            peak_price=pair.price_usd,
            score=score,
            entry_liquidity_usd=pair.liquidity_usd,
            last_price=pair.price_usd,
            last_quote_at=time.time(),
        )
        self.cash -= total_cost
        self.open_positions[pair.mint] = position
        self.daily.entries += 1
        self.save()
        log.info(
            "PAPER BUY %s | cost=%s fill=$%.10f score=%d",
            position.symbol,
            money(total_cost),
            fill_price,
            score,
        )
        return position

    def close_fraction(
        self,
        mint: str,
        observed_price: float,
        fraction: float,
        reason: str,
    ) -> CloseResult | None:
        position = self.open_positions.get(mint)
        if position is None or observed_price <= 0:
            return None

        fraction = max(0.0, min(1.0, fraction))
        if fraction <= 0:
            return None
        fully_closed = fraction >= 0.999999
        qty_sold = (
            position.remaining_qty
            if fully_closed
            else position.remaining_qty * fraction
        )
        actual_fraction = qty_sold / position.remaining_qty

        slip = self.settings.paper_slippage_bps / 10_000.0
        fee_rate = self.settings.paper_fee_bps / 10_000.0
        fill_price = observed_price * (1.0 - slip)
        gross = qty_sold * fill_price
        exit_fee = gross * fee_rate
        net_proceeds = gross - exit_fee
        allocated_cost = position.cost_remaining * actual_fraction
        pnl = net_proceeds - allocated_cost

        self.cash += net_proceeds
        position.remaining_qty -= qty_sold
        position.cost_remaining -= allocated_cost
        position.realized_pnl += pnl
        self.total_realized_pnl += pnl
        self.daily.realized_pnl += pnl

        if fully_closed or position.remaining_qty <= position.initial_qty * 1e-9:
            fully_closed = True
            total_position_pnl = position.realized_pnl
            self.open_positions.pop(mint, None)
            if total_position_pnl >= 0:
                self.total_wins += 1
                self.daily.wins += 1
                self.daily.consecutive_losses = 0
            else:
                self.total_losses += 1
                self.daily.losses += 1
                self.daily.consecutive_losses += 1
            self.closed_positions.append(
                {
                    "mint": position.mint,
                    "symbol": position.symbol,
                    "opened_at": position.opened_at,
                    "closed_at": time.time(),
                    "entry_fill_price": position.entry_fill_price,
                    "exit_fill_price": fill_price,
                    "total_pnl": total_position_pnl,
                    "reason": reason,
                }
            )
            self.closed_positions = self.closed_positions[-500:]
        else:
            total_position_pnl = position.realized_pnl

        result = CloseResult(
            symbol=position.symbol,
            fraction=actual_fraction,
            observed_price=observed_price,
            fill_price=fill_price,
            qty_sold=qty_sold,
            exit_fee=exit_fee,
            realized_pnl=pnl,
            position_total_pnl=total_position_pnl,
            fully_closed=fully_closed,
            reason=reason,
        )
        self.save()
        log.info(
            "PAPER SELL %s %.0f%% | pnl=%s reason=%s",
            result.symbol,
            result.fraction * 100,
            money(result.realized_pnl),
            reason,
        )
        return result

    def save(self) -> None:
        path = self.settings.state_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "state_version": STATE_VERSION,
                "saved_at": time.time(),
                "cash": self.cash,
                "open_positions": {
                    mint: dataclasses.asdict(position)
                    for mint, position in self.open_positions.items()
                },
                "closed_positions": self.closed_positions[-500:],
                "total_realized_pnl": self.total_realized_pnl,
                "total_wins": self.total_wins,
                "total_losses": self.total_losses,
                "paused": self.paused,
                "daily": dataclasses.asdict(self.daily),
            }
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as error:
            log.error("state save failed: %s", error)

    def win_rate(self) -> float:
        total = self.total_wins + self.total_losses
        return 100.0 * self.total_wins / total if total else 0.0


# ======================================================================================
# DEXSCREENER CLIENT
# ======================================================================================


class AsyncRateLimiter:
    def __init__(self, requests_per_second: float):
        self.interval = 1.0 / requests_per_second
        self.next_allowed = 0.0
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_allowed - now)
            if delay:
                await asyncio.sleep(delay)
            self.next_allowed = max(self.next_allowed, time.monotonic()) + self.interval


class DexScreenerClient:
    BASE_URL = "https://api.dexscreener.com"

    def __init__(self, settings: Settings = SETTINGS):
        self.settings = settings
        self.session: aiohttp.ClientSession | None = None
        self.rate_limiter = AsyncRateLimiter(4.5)  # below the documented 300 rpm
        self.cache: dict[str, tuple[float, PairData | None]] = {}
        self.last_ok_at = 0.0
        self.last_error = ""
        self.error_count = 0

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "JarvisSolPaperBot/2.0",
                },
            )

    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()

    async def get_pairs(self, mints: list[str]) -> dict[str, PairData]:
        await self.start()
        now = time.time()
        result: dict[str, PairData] = {}
        uncached: list[str] = []
        for mint in dict.fromkeys(mints):
            cached = self.cache.get(mint)
            if cached and now - cached[0] <= 4.0:
                if cached[1] is not None:
                    result[mint] = cached[1]
            else:
                uncached.append(mint)

        for batch in chunks(uncached, 30):
            fetched = await self._fetch_batch(batch)
            fetched_at = time.time()
            for mint in batch:
                pair = fetched.get(mint)
                self.cache[mint] = (fetched_at, pair)
                if pair is not None:
                    result[mint] = pair
        return result

    async def _fetch_batch(self, mints: list[str]) -> dict[str, PairData]:
        if not mints or self.session is None:
            return {}
        addresses = ",".join(mints)
        url = f"{self.BASE_URL}/tokens/v1/solana/{addresses}"

        for attempt in range(3):
            await self.rate_limiter.wait()
            try:
                async with self.session.get(url) as response:
                    if response.status == 429:
                        retry_after = safe_float(
                            response.headers.get("Retry-After"), 2.0
                        )
                        await asyncio.sleep(max(1.0, min(retry_after, 15.0)))
                        continue
                    if response.status >= 500:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    if response.status != 200:
                        self.last_error = f"HTTP {response.status}"
                        self.error_count += 1
                        return {}
                    raw = await response.json(content_type=None)
                    pairs = raw if isinstance(raw, list) else (raw.get("pairs") or [])
                    parsed = self._parse_pairs(pairs, set(mints))
                    self.last_ok_at = time.time()
                    self.last_error = ""
                    return parsed
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
                self.last_error = str(error)
                self.error_count += 1
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
        return {}

    def _parse_pairs(
        self, pairs: list[dict[str, Any]], wanted: set[str]
    ) -> dict[str, PairData]:
        best: dict[str, PairData] = {}
        for raw in pairs:
            if raw.get("chainId") != "solana":
                continue
            base = raw.get("baseToken") or {}
            quote_token = raw.get("quoteToken") or {}
            base_address = str(base.get("address") or "")
            quote_address = str(quote_token.get("address") or "")
            mint = base_address if base_address in wanted else quote_address
            if mint not in wanted:
                continue

            liquidity = raw.get("liquidity") or {}
            volume = raw.get("volume") or {}
            txns = raw.get("txns") or {}
            m5 = txns.get("m5") or {}
            h1 = txns.get("h1") or {}
            changes = raw.get("priceChange") or {}
            token = base if mint == base_address else quote_token
            created = safe_float(raw.get("pairCreatedAt"))
            if created > 1_000_000_000_000:
                created /= 1000.0
            pair = PairData(
                mint=mint,
                symbol=str(token.get("symbol") or "?"),
                name=str(token.get("name") or "Unknown"),
                price_usd=safe_float(raw.get("priceUsd")),
                liquidity_usd=safe_float(liquidity.get("usd")),
                market_cap_usd=safe_float(raw.get("marketCap") or raw.get("fdv")),
                volume_5m_usd=safe_float(volume.get("m5")),
                volume_1h_usd=safe_float(volume.get("h1")),
                volume_24h_usd=safe_float(volume.get("h24")),
                buys_5m=int(safe_float(m5.get("buys"))),
                sells_5m=int(safe_float(m5.get("sells"))),
                buys_1h=int(safe_float(h1.get("buys"))),
                sells_1h=int(safe_float(h1.get("sells"))),
                price_change_5m_pct=safe_float(changes.get("m5")),
                price_change_1h_pct=safe_float(changes.get("h1")),
                pair_created_at=created,
                pair_address=str(raw.get("pairAddress") or ""),
                dex_id=str(raw.get("dexId") or "unknown"),
                fetched_at=time.time(),
            )
            current = best.get(mint)
            if current is None or pair.liquidity_usd > current.liquidity_usd:
                best[mint] = pair
        return best

    def summary(self) -> str:
        if self.last_ok_at:
            age = time.time() - self.last_ok_at
            return f"OK ({age:.0f}s ago), errors={self.error_count}"
        if self.last_error:
            return f"WAITING/ERROR — {self.last_error[:90]}"
        return "not queried yet"


# ======================================================================================
# PUMPPORTAL DISCOVERY STREAM
# ======================================================================================


class WalletTracker:
    """Watchlist of Solana wallets plus the tokens they just bought.

    A wallet only earns a place here by its own on-chain record. Tracking is
    a signal, never a guarantee: a tracked wallet can be exiting into you.
    """

    def __init__(self, settings: Settings = SETTINGS):
        self.settings = settings
        self.wallets: dict[str, str] = {}          # address -> label
        self.recent_buys: dict[str, list[tuple[float, str]]] = {}  # mint -> hits
        self.whale_hits: deque[tuple[float, str, str, float]] = deque(maxlen=500)
        self.whale_buy_counts: Counter[str] = Counter()
        self.load()

    def load(self) -> None:
        path = self.settings.tracked_wallets_file
        try:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.wallets = {
                        str(k): str(v) for k, v in raw.get("wallets", {}).items()
                    }
        except (OSError, json.JSONDecodeError) as error:
            log.warning("could not read tracked wallets: %s", error)

    def save(self) -> None:
        path = self.settings.tracked_wallets_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"wallets": self.wallets}, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as error:
            log.warning("could not save tracked wallets: %s", error)

    def add(self, address: str, label: str = "") -> bool:
        address = address.strip()
        if not (32 <= len(address) <= 44):
            return False
        self.wallets[address] = label.strip() or "unlabelled"
        self.save()
        return True

    def remove(self, address: str) -> bool:
        if address.strip() in self.wallets:
            del self.wallets[address.strip()]
            self.save()
            return True
        return False

    def record_whale_buy(self, mint: str, wallet: str, sol_amount: float) -> None:
        """A large buy on a token we are already watching.

        Size is a proxy for conviction, not for skill — plenty of large buys
        are the token's own team cycling their capital back through the pool.
        """
        self.record_buy(mint, wallet)
        self.whale_hits.append((time.time(), mint, wallet, sol_amount))
        seen = self.whale_buy_counts.get(wallet, 0) + 1
        self.whale_buy_counts[wallet] = seen
        if seen >= self.settings.whale_buys_to_track and wallet not in self.wallets:
            self.add(wallet, f"auto: {seen} large buys")
            log.info("auto-tracking repeat whale %s", wallet)

    def recent_whales(self, limit: int = 15) -> list[tuple[float, str, str, float]]:
        cutoff = time.time() - self.settings.smart_money_window_seconds
        rows = [hit for hit in self.whale_hits if hit[0] >= cutoff]
        return sorted(rows, key=lambda hit: -hit[3])[:limit]

    def record_buy(self, mint: str, wallet: str) -> None:
        hits = self.recent_buys.setdefault(mint, [])
        hits.append((time.time(), wallet))
        if len(hits) > 50:
            del hits[:-50]

    def buyers_of(self, mint: str) -> list[str]:
        """Distinct tracked wallets that bought this mint inside the window."""
        cutoff = time.time() - self.settings.smart_money_window_seconds
        hits = self.recent_buys.get(mint, [])
        return sorted({wallet for stamp, wallet in hits if stamp >= cutoff})

    def prune(self) -> None:
        cutoff = time.time() - self.settings.smart_money_window_seconds
        for mint in list(self.recent_buys):
            kept = [hit for hit in self.recent_buys[mint] if hit[0] >= cutoff]
            if kept:
                self.recent_buys[mint] = kept
            else:
                del self.recent_buys[mint]


class PumpScanner:
    BASE_URL = "wss://pumpportal.fun/api/data"

    def __init__(
        self,
        on_new_token: Callable[[dict[str, Any]], Awaitable[None]],
        on_trade: Callable[[dict[str, Any]], Awaitable[None]],
        settings: Settings = SETTINGS,
    ):
        self.settings = settings
        self.on_new_token = on_new_token
        self.on_trade = on_trade
        self.websocket: Any = None
        self.send_lock = asyncio.Lock()
        self.watched_mints: set[str] = set()
        self.connected = False
        self.last_event_at = 0.0
        self.last_error = ""
        self.reconnects = 0

    @property
    def uri(self) -> str:
        if self.settings.pumpportal_api_key:
            return f"{self.BASE_URL}?api-key={quote(self.settings.pumpportal_api_key)}"
        return self.BASE_URL

    async def run(self) -> None:
        backoff = 2.0
        while True:
            try:
                async with websockets.connect(
                    self.uri,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=4096,
                ) as websocket:
                    self.websocket = websocket
                    self.connected = True
                    self.last_error = ""
                    await self._send({"method": "subscribeNewToken"})
                    if self.settings.enable_metered_trade_stream and self.watched_mints:
                        for batch in chunks(sorted(self.watched_mints), 50):
                            await self._send(
                                {"method": "subscribeTokenTrade", "keys": batch}
                            )
                    log.info("PumpPortal connected; new-token stream subscribed")
                    backoff = 2.0
                    async for raw in websocket:
                        self.last_event_at = time.time()
                        await self._handle(raw)
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, OSError, TimeoutError, ValueError) as error:
                self.connected = False
                self.websocket = None
                self.last_error = str(error)
                self.reconnects += 1
                log.warning(
                    "PumpPortal disconnected: %s; retrying in %.0fs", error, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)
            except Exception as error:  # keep an upstream schema surprise non-fatal
                self.connected = False
                self.websocket = None
                self.last_error = f"unexpected: {error}"
                self.reconnects += 1
                log.exception("unexpected PumpPortal error; retrying in %.0fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)

    async def _handle(self, raw: str | bytes) -> None:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            message = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(message, dict):
            return
        event_type = message.get("txType")
        if event_type == "create" and message.get("mint"):
            await self.on_new_token(message)
        elif event_type in {"buy", "sell"} and message.get("mint"):
            await self.on_trade(message)

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.websocket is None:
            return
        async with self.send_lock:
            await self.websocket.send(json.dumps(payload))

    async def watch(self, mint: str) -> None:
        if not self.settings.enable_metered_trade_stream:
            return
        if mint in self.watched_mints:
            return
        self.watched_mints.add(mint)
        if self.connected:
            with contextlib.suppress(ConnectionClosed, OSError):
                await self._send({"method": "subscribeTokenTrade", "keys": [mint]})

    async def unwatch(self, mint: str) -> None:
        if mint not in self.watched_mints:
            return
        self.watched_mints.discard(mint)
        if self.connected:
            with contextlib.suppress(ConnectionClosed, OSError):
                await self._send({"method": "unsubscribeTokenTrade", "keys": [mint]})

    def summary(self) -> str:
        status = "CONNECTED" if self.connected else "DISCONNECTED"
        event_age = (
            f", last event {time.time() - self.last_event_at:.0f}s ago"
            if self.last_event_at
            else ", no event yet"
        )
        error = f", error={self.last_error[:80]}" if self.last_error else ""
        return f"{status}{event_age}, reconnects={self.reconnects}{error}"


# ======================================================================================
# TRADING ENGINE
# ======================================================================================


class TradingEngine:
    def __init__(
        self,
        account: PaperAccount,
        dex: DexScreenerClient,
        notify: Callable[[str], Awaitable[None]],
        settings: Settings = SETTINGS,
    ):
        self.settings = settings
        self.account = account
        self.dex = dex
        self.notify = notify
        self.scanner: PumpScanner | None = None
        self.tracker = WalletTracker(settings)
        self.whale_alert_sent_at: dict[str, float] = {}
        self.candidates: dict[str, Candidate] = {}
        self.funnel: Counter[str] = Counter()
        # Rolling window of recent rejections with the value that failed, so
        # /rejects can show where the thresholds actually sit.
        self.reject_log: deque[dict[str, Any]] = deque(maxlen=3000)

    def attach_scanner(self, scanner: PumpScanner) -> None:
        self.scanner = scanner

    async def on_new_token(self, message: dict[str, Any]) -> None:
        mint = str(message.get("mint") or "").strip()
        if not mint or mint in self.candidates or mint in self.account.open_positions:
            return
        now = time.time()
        if len(self.candidates) >= self.settings.max_candidates:
            oldest = min(
                self.candidates.values(), key=lambda candidate: candidate.created_at
            )
            await self._remove_candidate(oldest.mint, "capacity_eviction")

        candidate = Candidate(
            mint=mint,
            symbol=str(message.get("symbol") or "?"),
            name=str(message.get("name") or "Unknown"),
            creator=str(message.get("traderPublicKey") or ""),
            created_at=now,
            next_check_at=now + self.settings.observation_seconds,
            expires_at=now + self.settings.candidate_ttl_seconds,
        )
        self.candidates[mint] = candidate
        self.funnel["new_tokens"] += 1
        if self.scanner:
            await self.scanner.watch(mint)
        log.info("new token %s (%s) | %s", candidate.name, candidate.symbol, mint)

    async def on_trade(self, message: dict[str, Any]) -> None:
        mint = str(message.get("mint") or "")
        side = str(message.get("txType") or "")
        trader = str(message.get("traderPublicKey") or "unknown")
        candidate = self.candidates.get(mint)
        if candidate:
            candidate.record_trade(side, trader)

        if side != "buy" or not mint:
            return

        sol_amount = safe_float(
            message.get("solAmount") or message.get("sol_amount") or 0.0
        )
        is_whale = sol_amount >= self.settings.whale_min_sol
        is_tracked = trader in self.tracker.wallets

        if is_whale:
            self.tracker.record_whale_buy(mint, trader, sol_amount)
            self.funnel["whale_buys_seen"] += 1
            await self._maybe_whale_alert(mint, trader, sol_amount, candidate)
        elif is_tracked:
            self.tracker.record_buy(mint, trader)

        if not (is_whale or is_tracked):
            return

        self.funnel["smart_money_hits"] += 1
        # Bring the candidate forward so the DEX check happens now rather than
        # after the full observation delay.
        if candidate and self.settings.smart_money_skip_observation:
            candidate.next_check_at = min(candidate.next_check_at, time.time())

    async def _maybe_whale_alert(
        self,
        mint: str,
        wallet: str,
        sol_amount: float,
        candidate: Candidate | None,
    ) -> None:
        """Push a Telegram alert for a large buy, rate-limited per token."""
        if not self.settings.whale_alerts_enabled:
            return
        now = time.time()
        last = self.whale_alert_sent_at.get(mint, 0.0)
        if now - last < self.settings.whale_alert_cooldown_seconds:
            return
        self.whale_alert_sent_at[mint] = now

        symbol = candidate.symbol if candidate else "?"
        buyers = len(self.tracker.buyers_of(mint))
        safe_mint = html.escape(mint)
        await self.notify(
            f"🐋 <b>WHALE BUY — {html.escape(symbol)}</b>\n\n"
            f"Size: <b>{sol_amount:.2f} SOL</b>\n"
            f"Buyer: <code>{html.escape(wallet)}</code>\n"
            f"Tracked buyers on this token: {buyers}\n\n"
            f"Contract:\n<code>{safe_mint}</code>\n"
            f'<a href="https://dexscreener.com/solana/{safe_mint}">chart</a>\n\n'
            "Alert only — no paper entry unless it clears the filters."
        )

    async def evaluator_loop(self) -> None:
        while True:
            try:
                await self._evaluate_due_candidates()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("candidate evaluator error")
            await asyncio.sleep(self.settings.evaluator_interval_seconds)

    async def _evaluate_due_candidates(self) -> None:
        now = time.time()
        self.tracker.prune()
        expired = [
            mint for mint, item in self.candidates.items() if item.expires_at <= now
        ]
        for mint in expired:
            await self._remove_candidate(mint, "expired_without_pool")

        due = [
            item
            for item in self.candidates.values()
            if item.next_check_at <= now and item.expires_at > now
        ][:120]
        if not due:
            return

        self.funnel["dex_checks"] += len(due)
        pairs = await self.dex.get_pairs([item.mint for item in due])
        for candidate in due:
            pair = pairs.get(candidate.mint)
            if pair is None:
                candidate.next_check_at = now + self.settings.dex_retry_seconds
                self.funnel["waiting_for_dex_pool"] += 1
                continue

            self.funnel["dex_pool_found"] += 1
            smart_buyers = self.tracker.buyers_of(candidate.mint)
            result = score_candidate(
                candidate, pair, self.settings, smart_buyers=len(smart_buyers)
            )
            if not result.passed:
                self.funnel[f"reject_{result.code}"] += 1
                self._record_reject(candidate, result)
                log.info("reject %s | %s", candidate.symbol, result.summary)
                if result.code in TERMINAL_REJECT_CODES:
                    await self._remove_candidate(candidate.mint)
                else:
                    # A brand-new pool that is thin RIGHT NOW may be tradeable in
                    # two minutes. Keep re-checking until the candidate TTL ends
                    # instead of discarding it on the first look.
                    candidate.next_check_at = now + self.settings.dex_retry_seconds
                    self.funnel["recheck_scheduled"] += 1
                continue

            allowed, reason = self.account.can_open()
            if not allowed and reason == "max_open_positions":
                candidate.next_check_at = now + self.settings.dex_retry_seconds
                self.funnel["waiting_for_position_slot"] += 1
                continue
            if not allowed:
                self.funnel[f"entry_block_{reason}"] += 1
                await self._remove_candidate(candidate.mint)
                continue

            # Last gate before committing paper capital: mechanical rug checks
            # against the Solana RPC. Only runs for candidates that already
            # cleared scoring and the account gates, so RPC load stays low.
            rug = await check_rug_safety(self.dex.session, self.settings, candidate.mint)
            if not rug.passed:
                self.funnel[f"rug_{rug.code}"] += 1
                log.info("rug reject %s | %s", candidate.symbol, rug.summary)
                await self._remove_candidate(candidate.mint)
                continue

            position = self.account.open_position(pair, result.score)
            if position is None:
                self.funnel["entry_size_or_duplicate_block"] += 1
                await self._remove_candidate(candidate.mint)
                continue

            self.funnel["paper_buys"] += 1
            await self.notify(self._entry_message(candidate, pair, result, position))
            await self._remove_candidate(candidate.mint)

    async def _remove_candidate(
        self, mint: str, funnel_reason: str | None = None
    ) -> None:
        if self.candidates.pop(mint, None) is not None and funnel_reason:
            self.funnel[funnel_reason] += 1
        if self.scanner:
            await self.scanner.unwatch(mint)

    async def monitor_loop(self) -> None:
        while True:
            try:
                await self._monitor_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("position monitor error")
            await asyncio.sleep(self.settings.monitor_interval_seconds)

    async def _monitor_once(self) -> None:
        positions = list(self.account.open_positions.values())
        if not positions:
            return
        pair_map = await self.dex.get_pairs([position.mint for position in positions])
        now = time.time()

        for position in positions:
            # It may have closed earlier in this same loop.
            if position.mint not in self.account.open_positions:
                continue
            pair = pair_map.get(position.mint)
            if pair is None or pair.price_usd <= 0:
                stale_age = now - (position.last_quote_at or position.opened_at)
                if (
                    stale_age >= self.settings.stale_price_alert_seconds
                    and not position.stale_alert_sent
                ):
                    position.stale_alert_sent = True
                    self.account.save()
                    await self.notify(
                        f"⚠️ <b>STALE PAPER PRICE</b> {html.escape(position.symbol)}\n"
                        f"No fresh DexScreener quote for {stale_age / 60:.1f} minutes. "
                        "No simulated exit was invented."
                    )
                continue

            price = pair.price_usd
            position.last_price = price
            position.last_quote_at = now
            position.stale_alert_sent = False
            position.peak_price = max(position.peak_price, price)
            hold_seconds = now - position.opened_at

            drain_limit = self.settings.liquidity_drain_exit_pct / 100.0
            if (
                drain_limit > 0
                and position.entry_liquidity_usd > 0
                and pair.liquidity_usd
                < position.entry_liquidity_usd * (1.0 - drain_limit)
            ):
                # The pool is being pulled. Exit now rather than waiting for the
                # price stop, which on a rug fills at effectively zero.
                await self._close_and_notify(
                    position, price, 1.0, "liquidity drain"
                )
                continue

            if price <= position.stop_price:
                await self._close_and_notify(position, price, 1.0, "stop loss")
                continue

            if not position.tp1_hit and price >= position.tp1_price:
                fraction = self.settings.tp1_sell_fraction_pct / 100.0
                result = self.account.close_fraction(
                    position.mint, price, fraction, "TP1"
                )
                if result:
                    remaining = self.account.open_positions.get(position.mint)
                    if remaining:
                        remaining.tp1_hit = True
                        remaining.stop_price = max(
                            remaining.stop_price, remaining.entry_fill_price
                        )
                        remaining.peak_price = max(remaining.peak_price, price)
                        self.account.save()
                    await self.notify(self._exit_message(result))
                continue

            if price >= position.tp2_price:
                await self._close_and_notify(position, price, 1.0, "TP2")
                continue

            if position.tp1_hit and position.peak_price > 0:
                drop_from_peak = 1.0 - price / position.peak_price
                if drop_from_peak >= self.settings.trailing_stop_pct / 100.0:
                    await self._close_and_notify(position, price, 1.0, "trailing stop")
                    continue

            if hold_seconds >= self.settings.max_hold_seconds:
                await self._close_and_notify(position, price, 1.0, "max hold time")

    async def _close_and_notify(
        self,
        position: Position,
        observed_price: float,
        fraction: float,
        reason: str,
    ) -> None:
        result = self.account.close_fraction(
            position.mint, observed_price, fraction, reason
        )
        if result:
            await self.notify(self._exit_message(result))

    def _entry_message(
        self,
        candidate: Candidate,
        pair: PairData,
        result: ScoreResult,
        position: Position,
    ) -> str:
        return (
            f"🟢 <b>PAPER BUY — {html.escape(position.symbol)}</b>\n\n"
            f"Score: {result.score}/100\n"
            f"Observed: ${pair.price_usd:.10f}\n"
            f"Simulated fill: ${position.entry_fill_price:.10f}\n"
            f"Paper cost: {money(position.initial_cost)}\n"
            f"Liquidity: {money(pair.liquidity_usd)}\n"
            f"5m volume: {money(pair.volume_5m_usd)}\n"
            f"5m buys/sells: {pair.buys_5m}/{pair.sells_5m}\n\n"
            f"Stop: ${position.stop_price:.10f}\n"
            f"TP1: ${position.tp1_price:.10f}\n"
            f"TP2: ${position.tp2_price:.10f}\n\n"
            f"Mint: <code>{html.escape(candidate.mint)}</code>\n"
            + (
                f"🐋 Smart-money buyers in window: "
                f"{len(self.tracker.buyers_of(candidate.mint))}\n"
                if self.tracker.buyers_of(candidate.mint)
                else ""
            )
            + "Mode: 🧪 PAPER ONLY"
        )

    def _exit_message(self, result: CloseResult) -> str:
        emoji = "🟢" if result.realized_pnl >= 0 else "🔴"
        label = "FULL" if result.fully_closed else f"{result.fraction * 100:.0f}%"
        return (
            f"{emoji} <b>PAPER SELL ({label}) — {html.escape(result.symbol)}</b>\n\n"
            f"Reason: {html.escape(result.reason)}\n"
            f"Observed: ${result.observed_price:.10f}\n"
            f"Simulated fill: ${result.fill_price:.10f}\n"
            f"Realized P/L: {money(result.realized_pnl)}\n"
            f"Exit fee: {money(result.exit_fee)}"
        )

    def funnel_summary(self) -> str:
        if not self.funnel:
            return "No launch events received yet."
        ordered = sorted(self.funnel.items(), key=lambda item: item[0])
        return "\n".join(f"• {name}: {count}" for name, count in ordered)

    def _record_reject(self, candidate: Candidate, result: ScoreResult) -> None:
        self.reject_log.append(
            {
                "code": result.code,
                "symbol": candidate.symbol,
                "summary": result.summary,
                "metrics": dict(result.metrics),
            }
        )

    def reject_summary(self) -> str:
        """Show the distribution of the values that failed each filter.

        The point is to answer 'is my threshold slightly too tight, or wildly
        off?' — a median liquidity of $8k means lower the bar a little, $300
        means these tokens were never tradeable.
        """
        if not self.reject_log:
            return "No rejections recorded yet."

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.reject_log:
            grouped.setdefault(row["code"], []).append(row)

        thresholds = {
            "liquidity_usd": self.settings.min_liquidity_usd,
            "volume_5m_usd": self.settings.min_volume_5m_usd,
            "market_cap_usd": self.settings.max_market_cap_usd,
            "buys_5m": float(self.settings.min_buys_5m),
            "buy_sell_ratio_5m": self.settings.min_buy_sell_ratio,
            "price_change_5m_pct": self.settings.max_price_change_5m_pct,
            "score": float(self.settings.min_score),
        }

        lines = [f"(last {len(self.reject_log)} rejections)"]
        for code, rows in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"\n• {code}: {len(rows)}")
            keys = {key for row in rows for key in row["metrics"]}
            for key in sorted(keys):
                values = sorted(
                    float(row["metrics"][key]) for row in rows if key in row["metrics"]
                )
                if not values:
                    continue
                median = values[len(values) // 2]
                p90 = values[min(len(values) - 1, int(len(values) * 0.9))]
                limit = thresholds.get(key)
                limit_text = f" | limit {limit:,.2f}" if limit is not None else ""
                lines.append(
                    f"    {key}: median {median:,.2f} | p90 {p90:,.2f}{limit_text}"
                )
            for row in rows[:2]:
                lines.append(f"    e.g. {row['symbol']}: {row['summary']}")
        return "\n".join(lines)


# ======================================================================================
# TELEGRAM CONTROLLER AND APP LIFECYCLE
# ======================================================================================


class JarvisBot:
    def __init__(self, settings: Settings = SETTINGS):
        self.settings = settings
        self.account = PaperAccount.load(settings)
        self.dex = DexScreenerClient(settings)
        self.app: Application | None = None
        self.stop_event = asyncio.Event()
        self.background_tasks: list[asyncio.Task] = []

        self.engine = TradingEngine(self.account, self.dex, self.notify, settings)
        self.scanner = PumpScanner(
            on_new_token=self.engine.on_new_token,
            on_trade=self.engine.on_trade,
            settings=settings,
        )
        self.engine.attach_scanner(self.scanner)

    async def notify(self, text: str) -> None:
        if self.app is None:
            log.info(
                "notification before Telegram start: %s", text.replace("\n", " | ")
            )
            return
        try:
            await self.app.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except (TelegramError, RuntimeError) as error:
            log.warning("Telegram notification failed: %s", error)

    async def _authorized(self, update: Update) -> bool:
        chat = update.effective_chat
        if chat and str(chat.id) == self.settings.telegram_chat_id:
            return True
        if update.effective_message:
            with contextlib.suppress(Exception):
                await update.effective_message.reply_text("This bot is private.")
        return False

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text(
            "JARVIS SOL is running in PAPER mode. No real wallet or trades.\n\n"
            "/status /today /positions /sources /funnel /settings /pause /resume /help"
        )

    async def cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        self.account.roll_day()
        gate_ok, gate_reason = self.account.can_open()
        trading = "READY" if gate_ok else f"BLOCKED — {gate_reason.replace('_', ' ')}"
        await update.effective_message.reply_text(
            "🤖 JARVIS SOL — PAPER\n\n"
            f"Cash: {money(self.account.cash)}\n"
            f"Equity: {money(self.account.equity())}\n"
            f"Total realized P/L: {money(self.account.total_realized_pnl)}\n"
            f"Today P/L: {money(self.account.daily_pnl())}\n"
            f"Open: {len(self.account.open_positions)}/{self.settings.max_open_positions}\n"
            f"Entries today: {self.account.daily.entries}/"
            f"{limit_label(self.settings.max_entries_per_day)}\n"
            f"W/L: {self.account.total_wins}/{self.account.total_losses}\n"
            f"Win rate: {self.account.win_rate():.1f}%\n"
            f"Candidates waiting: {len(self.engine.candidates)}\n"
            f"Trading: {trading}\n"
            "Mode: PAPER ONLY"
        )

    async def cmd_today(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        self.account.roll_day()
        target = self.settings.daily_profit_target_usd
        progress = self.account.daily_pnl() / target * 100.0 if target else 0.0
        await update.effective_message.reply_text(
            f"📅 {self.account.daily.day} ({self.settings.timezone_name})\n\n"
            f"P/L: {money(self.account.daily_pnl())}\n"
            f"Realized: {money(self.account.daily.realized_pnl)}\n"
            f"Reference target: {money(target)}\n"
            f"Progress: {progress:.0f}%\n"
            f"Entries: {self.account.daily.entries}/"
            f"{limit_label(self.settings.max_entries_per_day)}\n"
            f"W/L: {self.account.daily.wins}/{self.account.daily.losses}\n"
            f"Loss streak: {self.account.daily.consecutive_losses}/"
            f"{limit_label(self.settings.max_consecutive_losses)}"
        )

    async def cmd_positions(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        if not self.account.open_positions:
            await update.effective_message.reply_text("No open paper positions.")
            return
        lines = ["🧪 OPEN PAPER POSITIONS\n"]
        for position in self.account.open_positions.values():
            current = position.last_price or position.entry_fill_price
            held = (time.time() - position.opened_at) / 60.0
            lines.append(
                f"{position.symbol}: {pct(position.return_pct(current))}\n"
                f"  fill ${position.entry_fill_price:.10f}\n"
                f"  now  ${current:.10f}\n"
                f"  remaining {position.remaining_qty / position.initial_qty * 100:.0f}% | "
                f"held {held:.0f}m | score {position.score}"
            )
        await update.effective_message.reply_text("\n\n".join(lines))

    async def cmd_sources(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        metered = (
            "ON — PumpPortal charges may apply"
            if self.settings.enable_metered_trade_stream
            else "OFF — free new-token stream only"
        )
        await update.effective_message.reply_text(
            "📡 SOURCE HEALTH\n\n"
            f"PumpPortal: {self.scanner.summary()}\n"
            f"DexScreener: {self.dex.summary()}\n"
            f"Metered token trades: {metered}"
        )

    async def cmd_funnel(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text(
            "🔎 SCANNER FUNNEL\n\n" + self.engine.funnel_summary()
        )

    async def cmd_whales(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        tracker = self.engine.tracker
        if not self.settings.enable_metered_trade_stream:
            await update.effective_message.reply_text(
                "🐋 Whale tracking needs the PumpPortal trade stream.\n"
                "Set PUMPPORTAL_API_KEY and ENABLE_METERED_TRADE_STREAM=true.\n"
                "That stream is metered, so it bills per subscription."
            )
            return
        rows = tracker.recent_whales()
        if not rows:
            await update.effective_message.reply_text(
                f"🐋 No buys over {self.settings.whale_min_sol:.2f} SOL "
                "in the current window."
            )
            return
        lines = [f"🐋 <b>RECENT WHALE BUYS</b> (&gt; {self.settings.whale_min_sol:.2f} SOL)\n"]
        for stamp, mint, wallet, amount in rows:
            age = (time.time() - stamp) / 60.0
            safe_mint = html.escape(mint)
            lines.append(
                f"\n<b>{amount:.2f} SOL</b> · {age:.0f}m ago\n"
                f"<code>{safe_mint}</code>\n"
                f"Buyer: <code>{html.escape(wallet)}</code>\n"
                f'<a href="https://dexscreener.com/solana/{safe_mint}">chart</a>'
            )
        lines.append(f"\n\nAuto-tracked wallets: {len(tracker.wallets)}")
        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def cmd_watch(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            await update.effective_message.reply_text(
                "Usage: /watch <wallet address> [label]"
            )
            return
        address = context.args[0]
        label = " ".join(context.args[1:])
        if self.engine.tracker.add(address, label):
            await update.effective_message.reply_text(f"👁 Tracking {address[:8]}…")
        else:
            await update.effective_message.reply_text(
                "That does not look like a Solana address."
            )

    async def cmd_unwatch(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        if not context.args:
            await update.effective_message.reply_text("Usage: /unwatch <wallet address>")
            return
        if self.engine.tracker.remove(context.args[0]):
            await update.effective_message.reply_text("Removed.")
        else:
            await update.effective_message.reply_text("That wallet was not tracked.")

    async def cmd_watchlist(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        wallets = self.engine.tracker.wallets
        if not wallets:
            await update.effective_message.reply_text("No wallets tracked yet.")
            return
        lines = [f"👁 TRACKED WALLETS ({len(wallets)})\n"]
        for address, label in sorted(wallets.items(), key=lambda kv: kv[1]):
            lines.append(f"• {address[:6]}…{address[-4:]} — {label}")
        await update.effective_message.reply_text("\n".join(lines))

    async def cmd_rejects(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        text = "📉 REJECT DETAIL\n\n" + self.engine.reject_summary()
        # Telegram caps messages at 4096 characters.
        for chunk in (text[i : i + 3900] for i in range(0, len(text), 3900)):
            await update.effective_message.reply_text(chunk)

    async def cmd_settings(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        s = self.settings
        await update.effective_message.reply_text(
            "⚙️ ACTIVE SETTINGS\n\n"
            f"Risk/trade: {s.risk_per_trade_pct:.2f}%\n"
            f"Max allocation: {s.max_position_pct:.1f}%\n"
            f"Daily loss limit: {limit_label(s.max_daily_loss_pct, '%')}\n"
            f"Daily entry limit: {limit_label(s.max_entries_per_day)}\n"
            f"Loss streak limit: {limit_label(s.max_consecutive_losses)}\n"
            f"Stop: -{s.stop_loss_pct:.1f}%\n"
            f"TP1: +{s.tp1_pct:.1f}% (sell {s.tp1_sell_fraction_pct:.0f}%)\n"
            f"TP2: +{s.tp2_pct:.1f}%\n"
            f"Trailing stop after TP1: {s.trailing_stop_pct:.1f}%\n"
            f"Paper slippage: {s.paper_slippage_bps:.0f} bps/side\n"
            f"Paper fees: {s.paper_fee_bps:.0f} bps/side\n"
            f"Min score: {s.min_score}/100\n"
            f"Max position: {s.max_position_pct:.2f}% of equity\n"
            f"Liquidity-drain exit: -{s.liquidity_drain_exit_pct:.0f}%\n"
            f"Min liquidity: {money(s.min_liquidity_usd)}\n"
            f"Min 5m volume: {money(s.min_volume_5m_usd)}"
        )

    async def cmd_pause(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        self.account.paused = True
        self.account.save()
        await update.effective_message.reply_text(
            "⏸ New paper entries paused. Existing positions will still be monitored and exited."
        )

    async def cmd_resume(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        self.account.paused = False
        self.account.save()
        allowed, reason = self.account.can_open()
        message = "▶️ New paper entries resumed."
        if not allowed:
            message += f" Safety gate still blocks entries: {reason.replace('_', ' ')}."
        await update.effective_message.reply_text(message)

    async def cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._authorized(update):
            return
        await update.effective_message.reply_text(
            "/status — account and scanner status\n"
            "/today — today's P/L and limits\n"
            "/positions — open paper positions\n"
            "/sources — live feed health\n"
            "/funnel — exact rejection counts\n"
            "/rejects — failing values vs your thresholds\n"
            "/whales — recent large buys\n"
            "/watch <wallet> [label] — track a wallet\n"
            "/unwatch <wallet> — stop tracking\n"
            "/watchlist — wallets currently tracked\n"
            "/settings — active risk/filter values\n"
            "/pause — stop new entries; keep managing exits\n"
            "/resume — allow new entries again\n\n"
            "This is a simulation, not financial advice."
        )

    async def error_handler(
        self,
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        log.error("Telegram handler error", exc_info=context.error)

    def _register_handlers(self) -> None:
        assert self.app is not None
        handlers = {
            "start": self.cmd_start,
            "status": self.cmd_status,
            "today": self.cmd_today,
            "positions": self.cmd_positions,
            "sources": self.cmd_sources,
            "funnel": self.cmd_funnel,
            "rejects": self.cmd_rejects,
            "whales": self.cmd_whales,
            "watch": self.cmd_watch,
            "unwatch": self.cmd_unwatch,
            "watchlist": self.cmd_watchlist,
            "settings": self.cmd_settings,
            "pause": self.cmd_pause,
            "resume": self.cmd_resume,
            "help": self.cmd_help,
        }
        for command, callback in handlers.items():
            self.app.add_handler(CommandHandler(command, callback))
        self.app.add_error_handler(self.error_handler)

    async def heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)
            previous = self.account.roll_day()
            if previous:
                await self.notify(
                    f"📅 <b>NEW PAPER DAY</b>\n"
                    f"Previous realized P/L: {money(previous.realized_pnl)}\n"
                    f"W/L: {previous.wins}/{previous.losses}"
                )
            self.account.save()
            log.info(
                "heartbeat | equity=%s open=%d candidates=%d | PumpPortal=%s | Dex=%s",
                money(self.account.equity()),
                len(self.account.open_positions),
                len(self.engine.candidates),
                self.scanner.summary(),
                self.dex.summary(),
            )

    async def _supervise(
        self, name: str, factory: Callable[[], Awaitable[None]]
    ) -> None:
        """Keep a background loop alive forever, restarting it on any error."""

        backoff = RESTART_BACKOFF_MIN_SECONDS
        while not self.stop_event.is_set():
            started = time.time()
            try:
                await factory()
                log.warning("background task %s returned; restarting", name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error(
                    "background task %s crashed; restarting in %.0fs",
                    name,
                    backoff,
                    exc_info=exc,
                )
                with contextlib.suppress(Exception):
                    await self.notify(
                        f"♻️ <b>{name}</b> hit an error and restarts in "
                        f"{backoff:.0f}s.\nBot stays online."
                    )
            # A long clean run means the failure was transient, so reset backoff.
            if time.time() - started >= RESTART_HEALTHY_SECONDS:
                backoff = RESTART_BACKOFF_MIN_SECONDS
            else:
                backoff = min(backoff * 2, RESTART_BACKOFF_MAX_SECONDS)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), timeout=backoff)

    def _task_done(self, task: asyncio.Task) -> None:
        # Supervised tasks never end on their own, so only log if one does.
        if task.cancelled():
            return
        exception = task.exception()
        if exception:
            log.error("supervisor %s stopped", task.get_name(), exc_info=exception)

    async def run(self) -> None:
        self.app = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .get_updates_connect_timeout(10)
            .get_updates_read_timeout(30)
            .build()
        )
        self._register_handlers()
        await self.dex.start()

        await self.app.initialize()
        await self.app.start()
        if self.app.updater is None:
            raise RuntimeError("Telegram updater was not created")
        await self.app.updater.start_polling(drop_pending_updates=True)
        await self.app.bot.set_my_commands(
            [
                BotCommand("status", "Account and scanner status"),
                BotCommand("today", "Today's paper P/L"),
                BotCommand("positions", "Open paper positions"),
                BotCommand("sources", "Data source health"),
                BotCommand("funnel", "Scanner rejection counts"),
                BotCommand("rejects", "Why candidates failed, with numbers"),
                BotCommand("whales", "Recent large buys on watched tokens"),
                BotCommand("watchlist", "Wallets being tracked"),
                BotCommand("settings", "Active settings"),
                BotCommand("pause", "Pause new entries"),
                BotCommand("resume", "Resume new entries"),
                BotCommand("help", "Command help"),
            ]
        )

        self.background_tasks = [
            asyncio.create_task(
                self._supervise("pumpportal-scanner", self.scanner.run),
                name="pumpportal-scanner",
            ),
            asyncio.create_task(
                self._supervise("candidate-evaluator", self.engine.evaluator_loop),
                name="candidate-evaluator",
            ),
            asyncio.create_task(
                self._supervise("position-monitor", self.engine.monitor_loop),
                name="position-monitor",
            ),
            asyncio.create_task(
                self._supervise("heartbeat", self.heartbeat_loop),
                name="heartbeat",
            ),
        ]
        for task in self.background_tasks:
            task.add_done_callback(self._task_done)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.stop_event.set)

        metered_warning = (
            "\n⚠️ Metered PumpPortal token trades are ON and may incur PumpPortal charges."
            if self.settings.enable_metered_trade_stream
            else ""
        )
        await self.notify(
            "🤖 <b>JARVIS SOL v2 STARTED</b>\n"
            "Mode: ♾️ NON-STOP\n"
            "Mode: 🧪 PAPER ONLY\n"
            f"Equity: {money(self.account.equity())}\n"
            f"Restored positions: {len(self.account.open_positions)}"
            f"{metered_warning}"
        )
        log.info("JARVIS SOL v2 started in PAPER mode")

        try:
            await self.stop_event.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        log.info("shutdown requested")
        for task in self.background_tasks:
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.account.save()
        await self.dex.close()
        if self.app is not None:
            if self.app.updater is not None and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()


async def main() -> None:
    bot = JarvisBot()
    await bot.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
