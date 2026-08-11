from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# HELPERS
# ============================================================

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(timezone.utc)


def money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def price(value: float) -> str:
    if value >= 1:
        return f"${value:,.4f}"
    if value >= 0.01:
        return f"${value:.6f}"
    if value >= 0.000001:
        return f"${value:.8f}"
    return f"${value:.12g}"


def percentage(value: float) -> str:
    return f"{value:+.2f}%"


# ============================================================
# CONFIGURATION
# ============================================================

class ConfigurationError(ValueError):
    pass


def env_text(
    env: Mapping[str, str],
    name: str,
    default: str = "",
) -> str:
    return str(env.get(name, default)).strip()


def env_int(
    env: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = env_text(env, name)

    if not raw:
        return default

    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} must be a whole number"
        ) from exc


def env_float(
    env: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    raw = env_text(env, name)

    if not raw:
        return default

    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"{name} must be a number"
        ) from exc


def env_bool(
    env: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    raw = env_text(env, name)

    if not raw:
        return default

    value = raw.lower()

    if value in {"1", "true", "yes", "on"}:
        return True

    if value in {"0", "false", "no", "off"}:
        return False

    raise ConfigurationError(
        f"{name} must be true or false"
    )


def parse_chat_ids(raw: str) -> frozenset[int]:
    result: set[int] = set()

    for item in raw.split(","):
        item = item.strip()

        if not item:
            continue

        try:
            result.add(int(item))
        except ValueError as exc:
            raise ConfigurationError(
                "TELEGRAM_ALLOWED_CHAT_IDS must contain "
                "comma-separated numbers"
            ) from exc

    return frozenset(result)


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: int
    allowed_chat_ids: frozenset[int]

    timezone_name: str = "Australia/Sydney"
    state_path: Path = Path("data/jarvis_state.json")
    port: int = 8080

    scan_interval_seconds: int = 30
    price_update_seconds: int = 10
    status_interval_seconds: int = 300
    max_tokens_per_scan: int = 30
    http_concurrency: int = 6
    api_timeout_seconds: int = 12
    rugcheck_cache_minutes: int = 30

    starting_balance_usd: float = 1_000.0
    risk_per_trade_pct: float = 0.50
    max_position_pct: float = 10.0
    max_open_positions: int = 2
    max_daily_entries: int = 10
    max_daily_loss_pct: float = 2.0
    max_losing_streak: int = 3
    trade_cooldown_minutes: int = 120

    stop_loss_pct: float = 6.0
    take_profit_pct: float = 15.0
    trailing_activation_pct: float = 8.0
    trailing_distance_pct: float = 5.0
    max_hold_minutes: int = 360

    momentum_exit_5m_pct: float = -3.0
    momentum_exit_buy_ratio: float = 0.45
    liquidity_exit_fraction: float = 0.50

    paper_slippage_bps: float = 100.0
    paper_fee_bps: float = 35.0

    min_liquidity_usd: float = 50_000.0
    min_volume_24h_usd: float = 100_000.0
    min_market_cap_usd: float = 50_000.0
    max_market_cap_usd: float = 5_000_000.0

    min_pair_age_minutes: float = 10.0
    max_pair_age_minutes: float = 43_200.0

    min_5m_change_pct: float = 1.0
    max_5m_change_pct: float = 25.0
    min_1h_change_pct: float = -5.0
    max_1h_change_pct: float = 100.0

    min_5m_buys: int = 8
    min_5m_buy_ratio: float = 0.58
    min_liquidity_to_mcap: float = 0.03
    max_volume_to_liquidity: float = 30.0
    min_score: int = 76

    rugcheck_required: bool = True
    rugcheck_max_normalized: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ

        for name in (
            "ENABLE_LIVE_TRADING",
            "ENABLE_MAINNET_TRADING",
            "LIVE_TRADING",
        ):
            if env_bool(env, name, False):
                raise ConfigurationError(
                    f"{name}=true is refused. "
                    "This bot is paper-trading only."
                )

        for name in (
            "SOLANA_PRIVATE_KEY",
            "WALLET_PRIVATE_KEY",
            "PHANTOM_PRIVATE_KEY",
        ):
            if env_text(env, name):
                raise ConfigurationError(
                    f"Remove {name}. No wallet key is "
                    "allowed in this paper bot."
                )

        bot_token = env_text(
            env,
            "TELEGRAM_BOT_TOKEN",
        )

        chat_id = env_int(
            env,
            "TELEGRAM_CHAT_ID",
            0,
        )

        if not bot_token:
            raise ConfigurationError(
                "TELEGRAM_BOT_TOKEN is required"
            )

        if not chat_id:
            raise ConfigurationError(
                "TELEGRAM_CHAT_ID is required"
            )

        allowed = parse_chat_ids(
            env_text(
                env,
                "TELEGRAM_ALLOWED_CHAT_IDS",
            )
        )

        allowed = frozenset(
            set(allowed) | {chat_id}
        )

        settings = cls(
            telegram_bot_token=bot_token,
            telegram_chat_id=chat_id,
            allowed_chat_ids=allowed,

            timezone_name=env_text(
                env,
                "TIMEZONE",
                "Australia/Sydney",
            ),
            state_path=Path(
                env_text(
                    env,
                    "STATE_PATH",
                    "data/jarvis_state.json",
                )
            ),
            port=env_int(env, "PORT", 8080),

            scan_interval_seconds=env_int(
                env,
                "SCAN_INTERVAL_SECONDS",
                30,
            ),
            price_update_seconds=env_int(
                env,
                "PRICE_UPDATE_SECONDS",
                10,
            ),
            status_interval_seconds=env_int(
                env,
                "STATUS_INTERVAL_SECONDS",
                300,
            ),
            max_tokens_per_scan=env_int(
                env,
                "MAX_TOKENS_PER_SCAN",
                30,
            ),
            http_concurrency=env_int(
                env,
                "HTTP_CONCURRENCY",
                6,
            ),
            api_timeout_seconds=env_int(
                env,
                "API_TIMEOUT_SECONDS",
                12,
            ),
            rugcheck_cache_minutes=env_int(
                env,
                "RUGCHECK_CACHE_MINUTES",
                30,
            ),

            starting_balance_usd=env_float(
                env,
                "STARTING_BALANCE_USD",
                1_000,
            ),
            risk_per_trade_pct=env_float(
                env,
                "RISK_PER_TRADE_PCT",
                0.50,
            ),
            max_position_pct=env_float(
                env,
                "MAX_POSITION_PCT",
                10.0,
            ),
            max_open_positions=env_int(
                env,
                "MAX_OPEN_POSITIONS",
                2,
            ),
            max_daily_entries=env_int(
                env,
                "MAX_DAILY_ENTRIES",
                10,
            ),
            max_daily_loss_pct=env_float(
                env,
                "MAX_DAILY_LOSS_PCT",
                2.0,
            ),
            max_losing_streak=env_int(
                env,
                "MAX_LOSING_STREAK",
                3,
            ),
            trade_cooldown_minutes=env_int(
                env,
                "TRADE_COOLDOWN_MINUTES",
                120,
            ),

            stop_loss_pct=env_float(
                env,
                "STOP_LOSS_PCT",
                6.0,
            ),
            take_profit_pct=env_float(
                env,
                "TAKE_PROFIT_PCT",
                15.0,
            ),
            trailing_activation_pct=env_float(
                env,
                "TRAILING_ACTIVATION_PCT",
                8.0,
            ),
            trailing_distance_pct=env_float(
                env,
                "TRAILING_DISTANCE_PCT",
                5.0,
            ),
            max_hold_minutes=env_int(
                env,
                "MAX_HOLD_MINUTES",
                360,
            ),

            momentum_exit_5m_pct=env_float(
                env,
                "MOMENTUM_EXIT_5M_PCT",
                -3.0,
            ),
            momentum_exit_buy_ratio=env_float(
                env,
                "MOMENTUM_EXIT_BUY_RATIO",
                0.45,
            ),
            liquidity_exit_fraction=env_float(
                env,
                "LIQUIDITY_EXIT_FRACTION",
                0.50,
            ),

            paper_slippage_bps=env_float(
                env,
                "PAPER_SLIPPAGE_BPS",
                100.0,
            ),
            paper_fee_bps=env_float(
                env,
                "PAPER_FEE_BPS",
                35.0,
            ),

            min_liquidity_usd=env_float(
                env,
                "MIN_LIQUIDITY_USD",
                50_000,
            ),
            min_volume_24h_usd=env_float(
                env,
                "MIN_VOLUME_24H_USD",
                100_000,
            ),
            min_market_cap_usd=env_float(
                env,
                "MIN_MARKET_CAP_USD",
                50_000,
            ),
            max_market_cap_usd=env_float(
                env,
                "MAX_MARKET_CAP_USD",
                5_000_000,
            ),

            min_pair_age_minutes=env_float(
                env,
                "MIN_PAIR_AGE_MINUTES",
                10,
            ),
            max_pair_age_minutes=env_float(
                env,
                "MAX_PAIR_AGE_MINUTES",
                43_200,
            ),

            min_5m_change_pct=env_float(
                env,
                "MIN_5M_CHANGE_PCT",
                1.0,
            ),
            max_5m_change_pct=env_float(
                env,
                "MAX_5M_CHANGE_PCT",
                25.0,
            ),
            min_1h_change_pct=env_float(
                env,
                "MIN_1H_CHANGE_PCT",
                -5.0,
            ),
            max_1h_change_pct=env_float(
                env,
                "MAX_1H_CHANGE_PCT",
                100.0,
            ),

            min_5m_buys=env_int(
                env,
                "MIN_5M_BUYS",
                8,
            ),
            min_5m_buy_ratio=env_float(
                env,
                "MIN_5M_BUY_RATIO",
                0.58,
            ),
            min_liquidity_to_mcap=env_float(
                env,
                "MIN_LIQUIDITY_TO_MCAP",
                0.03,
            ),
            max_volume_to_liquidity=env_float(
                env,
                "MAX_VOLUME_TO_LIQUIDITY",
                30.0,
            ),
            min_score=env_int(
                env,
                "MIN_SCORE",
                76,
            ),

            rugcheck_required=env_bool(
                env,
                "RUGCHECK_REQUIRED",
                True,
            ),
            rugcheck_max_normalized=env_int(
                env,
                "RUGCHECK_MAX_NORMALIZED",
                60,
            ),
        )

        settings.validate()
        return settings

    def validate(self) -> None:
        if self.starting_balance_usd <= 0:
            raise ConfigurationError(
                "Starting balance must be positive"
            )

        if not (
            0 < self.risk_per_trade_pct <= 100
        ):
            raise ConfigurationError(
                "Risk per trade must be between 0 and 100"
            )

        if not (
            0 < self.max_position_pct <= 100
        ):
            raise ConfigurationError(
                "Maximum position must be between 0 and 100"
            )

        if not (
            0 < self.stop_loss_pct
            < self.take_profit_pct
        ):
            raise ConfigurationError(
                "Stop must be positive and below target"
            )

        if not 0 <= self.min_score <= 100:
            raise ConfigurationError(
                "Minimum score must be between 0 and 100"
            )


# ============================================================
# MARKET MODELS
# ============================================================

@dataclass(slots=True)
class MarketSnapshot:
    token_address: str
    symbol: str
    name: str
    pair_address: str
    dex_id: str
    quote_symbol: str

    price_usd: float
    liquidity_usd: float
    volume_24h_usd: float
    market_cap_usd: float
    fdv_usd: float

    change_5m_pct: float
    change_1h_pct: float
    change_6h_pct: float
    change_24h_pct: float

    buys_5m: int
    sells_5m: int
    buys_1h: int
    sells_1h: int

    age_minutes: float
    pair_url: str
    boosts_active: int = 0
    sources: tuple[str, ...] = ()

    @property
    def buy_ratio_5m(self) -> float:
        total = self.buys_5m + self.sells_5m

        if total <= 0:
            return 0.0

        return self.buys_5m / total

    @property
    def liquidity_to_mcap(self) -> float:
        market_cap = (
            self.market_cap_usd
            or self.fdv_usd
        )

        if market_cap <= 0:
            return 0.0

        return self.liquidity_usd / market_cap

    @property
    def volume_to_liquidity(self) -> float:
        if self.liquidity_usd <= 0:
            return 0.0

        return (
            self.volume_24h_usd
            / self.liquidity_usd
        )


@dataclass(slots=True)
class RugReport:
    available: bool
    normalized_score: int | None = None
    raw_score: int | None = None
    lp_locked_pct: float | None = None
    danger_names: tuple[str, ...] = ()
    warning_names: tuple[str, ...] = ()
    error: str | None = None

    @property
    def has_danger(self) -> bool:
        return bool(self.danger_names)


@dataclass(slots=True)
class Evaluation:
    score: int
    passed: bool
    blocks: list[str] = field(
        default_factory=list
    )
    reasons: list[str] = field(
        default_factory=list
    )
    components: dict[str, int] = field(
        default_factory=dict
    )


@dataclass(slots=True)
class ExitDecision:
    should_exit: bool
    reason: str = ""
    pnl_pct: float = 0.0


# ============================================================
# SAVED PAPER STATE
# ============================================================

STATE_VERSION = 2


def create_new_state(
    settings: Settings,
    *,
    day: str = "",
) -> dict[str, Any]:
    now = iso_now()

    return {
        "schema_version": STATE_VERSION,
        "initial_balance":
            settings.starting_balance_usd,
        "cash":
            settings.starting_balance_usd,

        "positions": {},
        "trades": [],

        "manual_paused": False,
        "safety_pause_reason": None,

        "day": day,
        "day_start_equity":
            settings.starting_balance_usd,
        "day_entries": 0,
        "day_wins": 0,
        "day_losses": 0,
        "loss_streak": 0,

        "cooldown_until": {},

        "last_scan": {
            "at": None,
            "discovered": 0,
            "snapshots": 0,
            "market_passed": 0,
            "qualified": 0,
            "top_score": 0,
            "top_blocks": [],
        },

        "created_at": now,
        "updated_at": now,
    }


class StateStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.state_path
        self.lock = asyncio.Lock()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return create_new_state(
                self.settings
            )

        try:
            state = json.loads(
                self.path.read_text(
                    encoding="utf-8"
                )
            )

            if not isinstance(state, dict):
                raise ValueError(
                    "State is not a JSON object"
                )

            baseline = create_new_state(
                self.settings
            )

            baseline.update(state)

            baseline.setdefault(
                "positions",
                {},
            )
            baseline.setdefault(
                "trades",
                [],
            )
            baseline.setdefault(
                "cooldown_until",
                {},
            )
            baseline.setdefault(
                "last_scan",
                create_new_state(
                    self.settings
                )["last_scan"],
            )

            baseline["schema_version"] = (
                STATE_VERSION
            )

            return baseline

        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            backup_name = (
                f"{self.path.name}.corrupt-"
                f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
            )

            try:
                self.path.replace(
                    self.path.with_name(backup_name)
                )
            except OSError:
                pass

            return create_new_state(
                self.settings
            )

    async def save(
        self,
        state: dict[str, Any],
    ) -> None:
        async with self.lock:
            self.path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            state["updated_at"] = iso_now()
            state["trades"] = list(
                state.get("trades", [])
            )[-2_000:]

            content = json.dumps(
                state,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )

            temporary_path = (
                self.path.with_suffix(
                    self.path.suffix + ".tmp"
                )
            )

            temporary_path.write_text(
                content,
                encoding="utf-8",
            )

            os.replace(
                temporary_path,
                self.path,
            )


# ============================================================
# LIVE MARKET DATA
# ============================================================

LOGGER = logging.getLogger("jarvis")


class APIError(RuntimeError):
    pass


class MarketDataClient:
    DEX_BASE = "https://api.dexscreener.com"
    RUG_BASE = "https://api.rugcheck.xyz"

    ALLOWED_QUOTES = {
        "SOL",
        "WSOL",
        "USDC",
        "USDT",
    }

    def __init__(self, settings: Settings):
        self.settings = settings

        self.session: (
            aiohttp.ClientSession | None
        ) = None

        self.semaphore = asyncio.Semaphore(
            settings.http_concurrency
        )

        self.rug_cache: dict[
            str,
            tuple[float, RugReport],
        ] = {}

        self.request_count = 0
        self.last_error: str | None = None

    async def start(self) -> None:
        if (
            self.session
            and not self.session.closed
        ):
            return

        timeout = aiohttp.ClientTimeout(
            total=(
                self.settings
                .api_timeout_seconds
            )
        )

        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent":
                    "JarvisV2-PaperResearch/2.1"
            },
        )

    async def close(self) -> None:
        if (
            self.session
            and not self.session.closed
        ):
            await self.session.close()

    async def get_json(
        self,
        url: str,
        *,
        attempts: int = 3,
    ) -> Any:
        await self.start()
        assert self.session is not None

        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                async with self.semaphore:
                    self.request_count += 1

                    async with self.session.get(
                        url
                    ) as response:
                        if response.status == 429:
                            retry_after = safe_float(
                                response.headers.get(
                                    "Retry-After"
                                ),
                                1.0,
                            )

                            await asyncio.sleep(
                                min(
                                    8.0,
                                    max(
                                        0.5,
                                        retry_after,
                                    ),
                                )
                            )
                            continue

                        if response.status >= 500:
                            raise APIError(
                                "Market service returned "
                                f"HTTP {response.status}"
                            )

                        if response.status >= 400:
                            body = (
                                await response.text()
                            )[:200]

                            raise APIError(
                                f"HTTP {response.status}: "
                                f"{body}"
                            )

                        return await response.json(
                            content_type=None
                        )

            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                APIError,
            ) as exc:
                last_error = exc

                if attempt + 1 < attempts:
                    await asyncio.sleep(
                        0.75 * (2 ** attempt)
                    )

        self.last_error = str(
            last_error
            or "Unknown market API error"
        )

        raise APIError(self.last_error)

    async def discover_tokens(
        self,
    ) -> list[tuple[str, tuple[str, ...]]]:
        endpoints = (
            (
                f"{self.DEX_BASE}/token-profiles/latest/v1",
                "DexScreener profiles",
            ),
            (
                f"{self.DEX_BASE}/token-boosts/top/v1",
                "DexScreener top boosts",
            ),
            (
                f"{self.DEX_BASE}/token-boosts/latest/v1",
                "DexScreener latest boosts",
            ),
            (
                f"{self.RUG_BASE}/v1/stats/new_tokens",
                "RugCheck new",
            ),
            (
                f"{self.RUG_BASE}/v1/stats/trending",
                "RugCheck trending",
            ),
        )

        results = await asyncio.gather(
            *(
                self.get_json(
                    endpoint,
                    attempts=2,
                )
                for endpoint, _ in endpoints
            ),
            return_exceptions=True,
        )

        source_buckets: list[
            tuple[str, list[str]]
        ] = []

        for result, endpoint_info in zip(
            results,
            endpoints,
            strict=True,
        ):
            source = endpoint_info[1]

            if isinstance(result, Exception):
                LOGGER.warning(
                    "Discovery source unavailable: "
                    "%s — %s",
                    source,
                    result,
                )
                continue

            if isinstance(result, list):
                items = result
            elif isinstance(result, dict):
                items = result.get(
                    "tokens",
                    [],
                )
            else:
                items = []

            addresses: list[str] = []
            source_seen: set[str] = set()

            for item in items:
                if not isinstance(item, dict):
                    continue

                chain = str(
                    item.get(
                        "chainId",
                        "solana",
                    )
                ).lower()

                if chain != "solana":
                    continue

                address = (
                    item.get("tokenAddress")
                    or item.get("mint")
                    or item.get("address")
                )

                if not isinstance(address, str):
                    continue

                address = address.strip()

                if (
                    not address
                    or address in source_seen
                ):
                    continue

                source_seen.add(address)
                addresses.append(address)

            source_buckets.append(
                (
                    source,
                    addresses,
                )
            )

        discovered_sources: dict[
            str,
            set[str],
        ] = {}

        ordered_addresses: list[str] = []

        largest_bucket = max(
            (
                len(addresses)
                for _, addresses
                in source_buckets
            ),
            default=0,
        )

        scan_cap = (
            self.settings.max_tokens_per_scan
        )

        for index in range(largest_bucket):
            for source, addresses in source_buckets:
                if index >= len(addresses):
                    continue

                address = addresses[index]

                if address not in discovered_sources:
                    discovered_sources[address] = set()
                    ordered_addresses.append(address)

                discovered_sources[address].add(
                    source
                )

                if (
                    len(ordered_addresses)
                    >= scan_cap
                ):
                    return [
                        (
                            token_address,
                            tuple(
                                sorted(
                                    discovered_sources[
                                        token_address
                                    ]
                                )
                            ),
                        )
                        for token_address
                        in ordered_addresses
                    ]

        return [
            (
                token_address,
                tuple(
                    sorted(
                        discovered_sources[
                            token_address
                        ]
                    )
                ),
            )
            for token_address
            in ordered_addresses
        ]

    async def fetch_snapshot(
        self,
        token_address: str,
        sources: Iterable[str] = (),
        *,
        preferred_pair: str | None = None,
    ) -> MarketSnapshot | None:
        encoded = quote(
            token_address,
            safe="",
        )

        payload = await self.get_json(
            f"{self.DEX_BASE}/token-pairs/v1/"
            f"solana/{encoded}"
        )

        if not isinstance(payload, list):
            return None

        valid_pairs: list[
            dict[str, Any]
        ] = []

        for pair in payload:
            if not isinstance(pair, dict):
                continue

            if pair.get("chainId") != "solana":
                continue

            base_token = (
                pair.get("baseToken") or {}
            )

            quote_token = (
                pair.get("quoteToken") or {}
            )

            if (
                base_token.get("address")
                != token_address
            ):
                continue

            quote_symbol = str(
                quote_token.get("symbol", "")
            ).upper()

            if (
                quote_symbol
                not in self.ALLOWED_QUOTES
            ):
                continue

            if safe_float(
                pair.get("priceUsd")
            ) <= 0:
                continue

            valid_pairs.append(pair)

        if not valid_pairs:
            return None

        selected: dict[str, Any] | None = None

        if preferred_pair:
            selected = next(
                (
                    pair
                    for pair in valid_pairs
                    if pair.get("pairAddress")
                    == preferred_pair
                ),
                None,
            )

        if selected is None:
            selected = max(
                valid_pairs,
                key=lambda pair: safe_float(
                    (
                        pair.get("liquidity")
                        or {}
                    ).get("usd")
                ),
            )

        now_ms = time.time() * 1_000

        created_ms = safe_float(
            selected.get("pairCreatedAt"),
            now_ms,
        )

        age_minutes = max(
            0.0,
            (now_ms - created_ms) / 60_000,
        )

        base_token = (
            selected.get("baseToken") or {}
        )
        quote_token = (
            selected.get("quoteToken") or {}
        )
        transactions = (
            selected.get("txns") or {}
        )
        changes = (
            selected.get("priceChange") or {}
        )
        volume = (
            selected.get("volume") or {}
        )
        liquidity = (
            selected.get("liquidity") or {}
        )
        boosts = (
            selected.get("boosts") or {}
        )

        five_minute_transactions = (
            transactions.get("m5") or {}
        )

        one_hour_transactions = (
            transactions.get("h1") or {}
        )

        pair_address = str(
            selected.get("pairAddress") or ""
        )

        return MarketSnapshot(
            token_address=token_address,

            symbol=str(
                base_token.get("symbol")
                or "UNKNOWN"
            )[:30],

            name=str(
                base_token.get("name")
                or base_token.get("symbol")
                or "Unknown"
            )[:80],

            pair_address=pair_address,

            dex_id=str(
                selected.get("dexId")
                or "unknown"
            ),

            quote_symbol=str(
                quote_token.get("symbol")
                or ""
            ).upper(),

            price_usd=safe_float(
                selected.get("priceUsd")
            ),

            liquidity_usd=safe_float(
                liquidity.get("usd")
            ),

            volume_24h_usd=safe_float(
                volume.get("h24")
            ),

            market_cap_usd=safe_float(
                selected.get("marketCap")
            ),

            fdv_usd=safe_float(
                selected.get("fdv")
            ),

            change_5m_pct=safe_float(
                changes.get("m5")
            ),

            change_1h_pct=safe_float(
                changes.get("h1")
            ),

            change_6h_pct=safe_float(
                changes.get("h6")
            ),

            change_24h_pct=safe_float(
                changes.get("h24")
            ),

            buys_5m=safe_int(
                five_minute_transactions.get(
                    "buys"
                )
            ),

            sells_5m=safe_int(
                five_minute_transactions.get(
                    "sells"
                )
            ),

            buys_1h=safe_int(
                one_hour_transactions.get(
                    "buys"
                )
            ),

            sells_1h=safe_int(
                one_hour_transactions.get(
                    "sells"
                )
            ),

            age_minutes=age_minutes,

            pair_url=str(
                selected.get("url")
                or (
                    "https://dexscreener.com/"
                    f"solana/{pair_address}"
                )
            ),

            boosts_active=safe_int(
                boosts.get("active")
            ),

            sources=tuple(sources),
        )

    async def fetch_many_snapshots(
        self,
        discoveries: Iterable[
            tuple[str, tuple[str, ...]]
        ],
    ) -> list[MarketSnapshot]:
        results = await asyncio.gather(
            *(
                self.fetch_snapshot(
                    address,
                    sources,
                )
                for address, sources
                in discoveries
            ),
            return_exceptions=True,
        )

        snapshots: list[MarketSnapshot] = []

        for result in results:
            if isinstance(
                result,
                MarketSnapshot,
            ):
                snapshots.append(result)

            elif isinstance(result, Exception):
                LOGGER.debug(
                    "Snapshot failed: %s",
                    result,
                )

        return snapshots

    async def fetch_rug_report(
        self,
        token_address: str,
        *,
        force: bool = False,
    ) -> RugReport:
        cached = self.rug_cache.get(
            token_address
        )

        cache_seconds = (
            self.settings.rugcheck_cache_minutes
            * 60
        )

        if (
            not force
            and cached
            and (
                time.monotonic()
                - cached[0]
                < cache_seconds
            )
        ):
            return cached[1]

        encoded = quote(
            token_address,
            safe="",
        )

        url = (
            f"{self.RUG_BASE}/v1/tokens/"
            f"{encoded}/report/summary"
            "?cacheOnly=false"
        )

        try:
            payload = await self.get_json(
                url,
                attempts=2,
            )

        except APIError as exc:
            report = RugReport(
                available=False,
                error=str(exc),
            )

            self.rug_cache[token_address] = (
                time.monotonic(),
                report,
            )

            return report

        if (
            not isinstance(payload, dict)
            or payload.get("error")
        ):
            error = (
                str(
                    payload.get(
                        "error",
                        "Invalid RugCheck response",
                    )
                )
                if isinstance(payload, dict)
                else "Invalid RugCheck response"
            )

            report = RugReport(
                available=False,
                error=error,
            )

            self.rug_cache[token_address] = (
                time.monotonic(),
                report,
            )

            return report

        dangers: list[str] = []
        warnings: list[str] = []

        for risk in payload.get("risks") or []:
            if not isinstance(risk, dict):
                continue

            name = str(
                risk.get("name")
                or risk.get("description")
                or "Unnamed risk"
            )

            level = str(
                risk.get("level") or ""
            ).lower()

            if (
                level in {"danger", "critical"}
                or "honeypot" in name.lower()
            ):
                dangers.append(name)
            else:
                warnings.append(name)

        normalized = payload.get(
            "score_normalised"
        )

        raw_score = payload.get("score")
        lp_locked = payload.get("lpLockedPct")

        report = RugReport(
            available=True,

            normalized_score=(
                safe_int(normalized)
                if normalized is not None
                else None
            ),

            raw_score=(
                safe_int(raw_score)
                if raw_score is not None
                else None
            ),

            lp_locked_pct=(
                safe_float(lp_locked)
                if lp_locked is not None
                else None
            ),

            danger_names=tuple(dangers),
            warning_names=tuple(warnings),
        )

        self.rug_cache[token_address] = (
            time.monotonic(),
            report,
        )

        return report


# ============================================================
# STRICT SCORING STRATEGY
# ============================================================

class Strategy:
    def __init__(self, settings: Settings):
        self.settings = settings

    def market_blocks(
        self,
        snapshot: MarketSnapshot,
    ) -> list[str]:
        settings = self.settings

        market_cap = (
            snapshot.market_cap_usd
            or snapshot.fdv_usd
        )

        blocks: list[str] = []

        if snapshot.price_usd <= 0:
            blocks.append("invalid price")

        if (
            snapshot.liquidity_usd
            < settings.min_liquidity_usd
        ):
            blocks.append(
                "liquidity below "
                f"${settings.min_liquidity_usd:,.0f}"
            )

        if (
            snapshot.volume_24h_usd
            < settings.min_volume_24h_usd
        ):
            blocks.append(
                "24h volume below "
                f"${settings.min_volume_24h_usd:,.0f}"
            )

        if market_cap <= 0:
            blocks.append(
                "market cap unavailable"
            )

        elif not (
            settings.min_market_cap_usd
            <= market_cap
            <= settings.max_market_cap_usd
        ):
            blocks.append(
                "market cap outside range"
            )

        if not (
            settings.min_pair_age_minutes
            <= snapshot.age_minutes
            <= settings.max_pair_age_minutes
        ):
            blocks.append(
                "pair age outside range"
            )

        if not (
            settings.min_5m_change_pct
            <= snapshot.change_5m_pct
            <= settings.max_5m_change_pct
        ):
            blocks.append(
                "5m momentum outside range"
            )

        if not (
            settings.min_1h_change_pct
            <= snapshot.change_1h_pct
            <= settings.max_1h_change_pct
        ):
            blocks.append(
                "1h trend outside range"
            )

        if (
            snapshot.buys_5m
            < settings.min_5m_buys
        ):
            blocks.append(
                "not enough 5m buys"
            )

        if (
            snapshot.buy_ratio_5m
            < settings.min_5m_buy_ratio
        ):
            blocks.append(
                "5m buy ratio too weak"
            )

        if (
            snapshot.liquidity_to_mcap
            < settings.min_liquidity_to_mcap
        ):
            blocks.append(
                "liquidity/mcap too low"
            )

        if (
            snapshot.volume_to_liquidity
            > settings.max_volume_to_liquidity
        ):
            blocks.append(
                "suspicious volume/liquidity"
            )

        return blocks

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        rug_report: RugReport,
    ) -> Evaluation:
        blocks = self.market_blocks(snapshot)

        if (
            not rug_report.available
            and self.settings.rugcheck_required
        ):
            blocks.append(
                "RugCheck unavailable"
            )

        if rug_report.has_danger:
            blocks.append(
                "RugCheck danger: "
                + ", ".join(
                    rug_report.danger_names[:2]
                )
            )

        if (
            rug_report.normalized_score
            is not None
            and rug_report.normalized_score
            > self.settings
            .rugcheck_max_normalized
        ):
            blocks.append(
                "RugCheck risk "
                f"{rug_report.normalized_score}/100"
            )

        components = {
            "liquidity":
                self.liquidity_score(snapshot),

            "volume":
                self.volume_score(snapshot),

            "momentum":
                self.momentum_score(snapshot),

            "trend":
                self.trend_score(snapshot),

            "buy pressure":
                self.buy_pressure_score(snapshot),

            "liquidity quality":
                self.liquidity_quality_score(
                    snapshot
                ),

            "age":
                self.age_score(snapshot),

            "rug safety":
                self.rug_score(rug_report),
        }

        score = max(
            0,
            min(
                100,
                sum(components.values()),
            ),
        )

        if (
            not blocks
            and score < self.settings.min_score
        ):
            blocks.append(
                f"score {score}/100 below "
                f"{self.settings.min_score}"
            )

        reasons = [
            name
            for name, points in sorted(
                components.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
            if points > 0
        ]

        reasons.append(
            "5m buy ratio "
            f"{snapshot.buy_ratio_5m:.0%}"
        )

        return Evaluation(
            score=score,
            passed=(
                not blocks
                and score
                >= self.settings.min_score
            ),
            blocks=blocks,
            reasons=reasons[:4],
            components=components,
        )

    @staticmethod
    def liquidity_score(
        snapshot: MarketSnapshot,
    ) -> int:
        value = snapshot.liquidity_usd

        if value >= 500_000:
            return 15
        if value >= 250_000:
            return 14
        if value >= 100_000:
            return 11
        if value >= 50_000:
            return 8
        return 0

    @staticmethod
    def volume_score(
        snapshot: MarketSnapshot,
    ) -> int:
        value = snapshot.volume_24h_usd

        if value >= 2_000_000:
            return 15
        if value >= 1_000_000:
            return 14
        if value >= 500_000:
            return 12
        if value >= 250_000:
            return 9
        if value >= 100_000:
            return 6
        return 0

    @staticmethod
    def momentum_score(
        snapshot: MarketSnapshot,
    ) -> int:
        value = snapshot.change_5m_pct

        if 2 <= value <= 8:
            return 15
        if 1 <= value <= 15:
            return 12
        if 0 <= value <= 25:
            return 8
        return 0

    @staticmethod
    def trend_score(
        snapshot: MarketSnapshot,
    ) -> int:
        value = snapshot.change_1h_pct

        if 5 <= value <= 35:
            return 10
        if 0 <= value <= 60:
            return 8
        if -5 <= value <= 100:
            return 4
        return 0

    @staticmethod
    def buy_pressure_score(
        snapshot: MarketSnapshot,
    ) -> int:
        ratio = snapshot.buy_ratio_5m

        if ratio >= 0.72:
            base = 12
        elif ratio >= 0.65:
            base = 10
        elif ratio >= 0.58:
            base = 7
        else:
            base = 0

        if snapshot.buys_5m >= 25:
            activity = 3
        elif snapshot.buys_5m >= 15:
            activity = 2
        else:
            activity = 1

        return min(15, base + activity)

    @staticmethod
    def liquidity_quality_score(
        snapshot: MarketSnapshot,
    ) -> int:
        ratio = snapshot.liquidity_to_mcap

        if ratio >= 0.20:
            return 10
        if ratio >= 0.12:
            return 9
        if ratio >= 0.08:
            return 7
        if ratio >= 0.05:
            return 5
        if ratio >= 0.03:
            return 3
        return 0

    @staticmethod
    def age_score(
        snapshot: MarketSnapshot,
    ) -> int:
        age = snapshot.age_minutes

        if 15 <= age <= 1_440:
            return 5
        if 10 <= age <= 10_080:
            return 4
        if age <= 43_200:
            return 2
        return 0

    @staticmethod
    def rug_score(
        rug_report: RugReport,
    ) -> int:
        if (
            not rug_report.available
            or rug_report.has_danger
        ):
            return 0

        value = rug_report.normalized_score

        if value is None:
            score = 6
        elif value <= 10:
            score = 15
        elif value <= 20:
            score = 13
        elif value <= 40:
            score = 10
        elif value <= 60:
            score = 5
        else:
            score = 0

        if len(rug_report.warning_names) >= 4:
            score = max(0, score - 2)

        return score


# ============================================================
# PAPER BROKER
# ============================================================

class PaperBroker:
    def __init__(
        self,
        settings: Settings,
        store: StateStore,
    ):
        self.settings = settings
        self.store = store
        self.state = store.load()

        self.timezone = ZoneInfo(
            settings.timezone_name
        )

    def local_day(self) -> str:
        return (
            utc_now()
            .astimezone(self.timezone)
            .date()
            .isoformat()
        )

    async def ensure_day(self) -> bool:
        today = self.local_day()

        if self.state.get("day") == today:
            return False

        self.state["day"] = today
        self.state["day_start_equity"] = (
            self.equity()
        )
        self.state["day_entries"] = 0
        self.state["day_wins"] = 0
        self.state["day_losses"] = 0
        self.state["loss_streak"] = 0
        self.state["safety_pause_reason"] = None

        await self.store.save(self.state)
        return True

    @property
    def cash(self) -> float:
        return float(
            self.state.get("cash", 0)
        )

    @property
    def positions(
        self,
    ) -> dict[str, dict[str, Any]]:
        return self.state.setdefault(
            "positions",
            {},
        )

    def estimated_exit_value(
        self,
        position: dict[str, Any],
    ) -> float:
        current_price = float(
            position.get("last_market_price")
            or position.get("entry_market_price")
            or 0
        )

        quantity = float(
            position.get("quantity", 0)
        )

        slippage = (
            self.settings.paper_slippage_bps
            / 10_000
        )

        fee = (
            self.settings.paper_fee_bps
            / 10_000
        )

        gross = (
            quantity
            * current_price
            * (1 - slippage)
        )

        return max(
            0,
            gross * (1 - fee),
        )

    def equity(self) -> float:
        return self.cash + sum(
            self.estimated_exit_value(
                position
            )
            for position
            in self.positions.values()
        )

    def open_pnl(self) -> float:
        return sum(
            self.estimated_exit_value(position)
            - float(
                position.get(
                    "entry_total_cost",
                    0,
                )
            )
            for position
            in self.positions.values()
        )

    def total_realized_pnl(self) -> float:
        return sum(
            float(trade.get("pnl_usd", 0))
            for trade
            in self.state.get("trades", [])
        )

    def today_pnl(self) -> float:
        return (
            self.equity()
            - float(
                self.state.get(
                    "day_start_equity",
                    self.equity(),
                )
            )
        )

    def daily_loss_pct(self) -> float:
        starting_equity = float(
            self.state.get(
                "day_start_equity",
                0,
            )
        )

        if starting_equity <= 0:
            return 0.0

        return max(
            0.0,
            (
                starting_equity
                - self.equity()
            )
            / starting_equity
            * 100,
        )

    def entry_guard(
        self,
        token_address: str | None = None,
    ) -> str | None:
        if self.state.get("manual_paused"):
            return "manually paused"

        if self.state.get(
            "safety_pause_reason"
        ):
            return str(
                self.state[
                    "safety_pause_reason"
                ]
            )

        if (
            len(self.positions)
            >= self.settings.max_open_positions
        ):
            return (
                "maximum open positions reached"
            )

        if (
            safe_int(
                self.state.get("day_entries")
            )
            >= self.settings.max_daily_entries
        ):
            return "daily entry limit reached"

        if (
            safe_int(
                self.state.get("loss_streak")
            )
            >= self.settings.max_losing_streak
        ):
            return (
                "maximum losing streak reached"
            )

        if (
            self.daily_loss_pct()
            >= self.settings.max_daily_loss_pct
        ):
            return "daily loss limit reached"

        if token_address:
            if token_address in self.positions:
                return (
                    "token already has an open position"
                )

            cooldown_until = parse_iso(
                self.state.setdefault(
                    "cooldown_until",
                    {},
                ).get(token_address)
            )

            if (
                cooldown_until
                and utc_now() < cooldown_until
            ):
                return "token is in cooldown"

        return None

    async def refresh_safety_pause(
        self,
    ) -> str | None:
        reason: str | None = None

        if (
            self.daily_loss_pct()
            >= self.settings.max_daily_loss_pct
        ):
            reason = "daily loss limit reached"

        elif (
            safe_int(
                self.state.get("loss_streak")
            )
            >= self.settings.max_losing_streak
        ):
            reason = (
                "maximum losing streak reached"
            )

        if (
            reason
            and self.state.get(
                "safety_pause_reason"
            ) != reason
        ):
            self.state[
                "safety_pause_reason"
            ] = reason

            await self.store.save(self.state)

        return reason

    async def open_position(
        self,
        snapshot: MarketSnapshot,
        evaluation: Evaluation,
    ) -> dict[str, Any] | None:
        await self.ensure_day()

        if self.entry_guard(
            snapshot.token_address
        ):
            return None

        equity = self.equity()

        risk_usd = (
            equity
            * self.settings.risk_per_trade_pct
            / 100
        )

        stop_fraction = (
            self.settings.stop_loss_pct
            / 100
        )

        risk_notional = (
            risk_usd / stop_fraction
        )

        position_limit = (
            equity
            * self.settings.max_position_pct
            / 100
        )

        fee_fraction = (
            self.settings.paper_fee_bps
            / 10_000
        )

        available_cash = (
            self.cash / (1 + fee_fraction)
        )

        notional = min(
            risk_notional,
            position_limit,
            available_cash,
        )

        if notional < 5:
            return None

        slippage_fraction = (
            self.settings.paper_slippage_bps
            / 10_000
        )

        fill_price = (
            snapshot.price_usd
            * (1 + slippage_fraction)
        )

        entry_fee = (
            notional * fee_fraction
        )

        quantity = (
            notional / fill_price
        )

        total_cost = (
            notional + entry_fee
        )

        position = {
            "token_address":
                snapshot.token_address,
            "symbol":
                snapshot.symbol,
            "name":
                snapshot.name,
            "pair_address":
                snapshot.pair_address,
            "pair_url":
                snapshot.pair_url,

            "opened_at":
                iso_now(),

            "entry_market_price":
                snapshot.price_usd,
            "entry_fill_price":
                fill_price,
            "last_market_price":
                snapshot.price_usd,
            "highest_market_price":
                snapshot.price_usd,

            "quantity":
                quantity,
            "entry_notional":
                notional,
            "entry_fee":
                entry_fee,
            "entry_total_cost":
                total_cost,

            "entry_liquidity_usd":
                snapshot.liquidity_usd,
            "last_liquidity_usd":
                snapshot.liquidity_usd,

            "score":
                evaluation.score,
            "entry_reasons":
                evaluation.reasons,
        }

        self.state["cash"] = max(
            0,
            self.cash - total_cost,
        )

        self.positions[
            snapshot.token_address
        ] = position

        self.state["day_entries"] = (
            safe_int(
                self.state.get("day_entries")
            )
            + 1
        )

        await self.store.save(self.state)
        return position

    def exit_decision(
        self,
        snapshot: MarketSnapshot,
    ) -> ExitDecision:
        position = self.positions.get(
            snapshot.token_address
        )

        if not position:
            return ExitDecision(False)

        position["last_market_price"] = (
            snapshot.price_usd
        )

        position["last_liquidity_usd"] = (
            snapshot.liquidity_usd
        )

        position["highest_market_price"] = max(
            float(
                position.get(
                    "highest_market_price",
                    0,
                )
            ),
            snapshot.price_usd,
        )

        entry_price = float(
            position["entry_fill_price"]
        )

        pnl_pct = (
            (
                snapshot.price_usd
                / entry_price
                - 1
            )
            * 100
            if entry_price > 0
            else 0
        )

        highest_price = float(
            position["highest_market_price"]
        )

        highest_gain = (
            (
                highest_price
                / entry_price
                - 1
            )
            * 100
            if entry_price > 0
            else 0
        )

        pullback = (
            (
                highest_price
                - snapshot.price_usd
            )
            / highest_price
            * 100
            if highest_price > 0
            else 0
        )

        entry_liquidity = float(
            position.get(
                "entry_liquidity_usd",
                0,
            )
        )

        if (
            entry_liquidity > 0
            and snapshot.liquidity_usd
            <= (
                entry_liquidity
                * self.settings
                .liquidity_exit_fraction
            )
        ):
            return ExitDecision(
                True,
                "LIQUIDITY_DROP",
                pnl_pct,
            )

        if (
            pnl_pct
            <= -self.settings.stop_loss_pct
        ):
            return ExitDecision(
                True,
                "STOP_LOSS",
                pnl_pct,
            )

        if (
            pnl_pct
            >= self.settings.take_profit_pct
        ):
            return ExitDecision(
                True,
                "TAKE_PROFIT",
                pnl_pct,
            )

        if (
            highest_gain
            >= self.settings
            .trailing_activation_pct
            and pullback
            >= self.settings
            .trailing_distance_pct
        ):
            return ExitDecision(
                True,
                "TRAILING_STOP",
                pnl_pct,
            )

        if (
            snapshot.change_5m_pct
            <= self.settings
            .momentum_exit_5m_pct
            and snapshot.buy_ratio_5m
            <= self.settings
            .momentum_exit_buy_ratio
        ):
            return ExitDecision(
                True,
                "MOMENTUM_REVERSAL",
                pnl_pct,
            )

        opened_at = parse_iso(
            str(position.get("opened_at", ""))
        )

        if (
            opened_at
            and utc_now() - opened_at
            >= timedelta(
                minutes=(
                    self.settings
                    .max_hold_minutes
                )
            )
        ):
            return ExitDecision(
                True,
                "MAX_HOLD_TIME",
                pnl_pct,
            )

        return ExitDecision(
            False,
            pnl_pct=pnl_pct,
        )

    async def close_position(
        self,
        snapshot: MarketSnapshot,
        reason: str,
    ) -> dict[str, Any] | None:
        position = self.positions.get(
            snapshot.token_address
        )

        if not position:
            return None

        slippage_fraction = (
            self.settings.paper_slippage_bps
            / 10_000
        )

        fee_fraction = (
            self.settings.paper_fee_bps
            / 10_000
        )

        exit_fill = (
            snapshot.price_usd
            * (1 - slippage_fraction)
        )

        gross = (
            float(position["quantity"])
            * exit_fill
        )

        exit_fee = gross * fee_fraction
        net_value = gross - exit_fee

        entry_cost = float(
            position["entry_total_cost"]
        )

        pnl_usd = net_value - entry_cost

        pnl_pct = (
            pnl_usd / entry_cost * 100
            if entry_cost > 0
            else 0
        )

        trade = {
            "token_address":
                snapshot.token_address,
            "symbol":
                position["symbol"],
            "pair_address":
                position["pair_address"],
            "pair_url":
                position["pair_url"],

            "opened_at":
                position["opened_at"],
            "closed_at":
                iso_now(),

            "entry_fill_price":
                position["entry_fill_price"],
            "exit_fill_price":
                exit_fill,
            "quantity":
                position["quantity"],

            "entry_total_cost":
                entry_cost,
            "exit_fee":
                exit_fee,

            "pnl_usd":
                pnl_usd,
            "pnl_pct":
                pnl_pct,
            "exit_reason":
                reason,
            "entry_score":
                position["score"],
        }

        self.state["cash"] = (
            self.cash + net_value
        )

        self.state.setdefault(
            "trades",
            [],
        ).append(trade)

        self.positions.pop(
            snapshot.token_address,
            None,
        )

        self.state.setdefault(
            "cooldown_until",
            {},
        )[snapshot.token_address] = (
            utc_now()
            + timedelta(
                minutes=(
                    self.settings
                    .trade_cooldown_minutes
                )
            )
        ).isoformat()

        if pnl_usd > 0:
            self.state["day_wins"] = (
                safe_int(
                    self.state.get("day_wins")
                )
                + 1
            )
            self.state["loss_streak"] = 0

        else:
            self.state["day_losses"] = (
                safe_int(
                    self.state.get("day_losses")
                )
                + 1
            )
            self.state["loss_streak"] = (
                safe_int(
                    self.state.get("loss_streak")
                )
                + 1
            )

        await self.refresh_safety_pause()
        await self.store.save(self.state)

        return trade

    async def set_manual_pause(
        self,
        paused: bool,
    ) -> None:
        self.state["manual_paused"] = paused
        await self.store.save(self.state)

    async def reset(self) -> None:
        self.state = create_new_state(
            self.settings,
            day=self.local_day(),
        )

        await self.store.save(self.state)


# ============================================================
# MESSAGE FORMATTERS
# ============================================================

def format_status(
    broker: PaperBroker,
    settings: Settings,
    *,
    running: bool,
) -> str:
    state = broker.state
    last_scan = state.get("last_scan", {})

    completed_today = (
        safe_int(state.get("day_wins"))
        + safe_int(state.get("day_losses"))
    )

    win_rate = (
        safe_int(state.get("day_wins"))
        / completed_today
        * 100
        if completed_today
        else 0
    )

    guard = broker.entry_guard()

    if running and not guard:
        status = "🟢 RESEARCHING"
    else:
        status = (
            "🟡 "
            + escape(
                (guard or "STOPPED").upper()
            )
        )

    return (
        "🤖 <b>JARVIS V2 STATUS</b>\n\n"

        f"Cash: <b>{money(broker.cash)}</b>\n"
        f"Equity: <b>{money(broker.equity())}</b>\n"
        "Total P/L: <b>"
        f"{money(broker.total_realized_pnl() + broker.open_pnl())}"
        "</b>\n"
        f"Open P/L: <b>{money(broker.open_pnl())}</b>\n\n"

        "Today's wins: "
        f"<b>{state.get('day_wins', 0)}</b>\n"

        "Today's losses: "
        f"<b>{state.get('day_losses', 0)}</b>\n"

        f"Win rate: <b>{win_rate:.1f}%</b>\n\n"

        "Open positions: "
        f"<b>{len(broker.positions)}/"
        f"{settings.max_open_positions}</b>\n"

        "Entries: "
        f"<b>{state.get('day_entries', 0)}/"
        f"{settings.max_daily_entries}</b>\n"

        "Loss streak: "
        f"<b>{state.get('loss_streak', 0)}/"
        f"{settings.max_losing_streak}</b>\n"

        "Daily loss: "
        f"<b>{broker.daily_loss_pct():.2f}%/"
        f"{settings.max_daily_loss_pct:.2f}%</b>\n\n"

        "Risk/trade: "
        f"<b>{settings.risk_per_trade_pct:.2f}%</b>\n"

        f"Stop: <b>{settings.stop_loss_pct:.1f}%</b>\n"

        "Target: "
        f"<b>+{settings.take_profit_pct:.1f}%</b>\n"

        "Minimum score: "
        f"<b>{settings.min_score}/100</b>\n\n"

        "Last scan: "
        f"<b>{last_scan.get('discovered', 0)} discovered · "
        f"{last_scan.get('snapshots', 0)} priced · "
        f"{last_scan.get('qualified', 0)} qualified</b>\n\n"

        f"Status: {status}\n"
        "Mode: 🧪 <b>PAPER TRADING</b>"
    )


def format_entry(
    position: dict[str, Any],
    snapshot: MarketSnapshot,
    evaluation: Evaluation,
    rug_report: RugReport,
) -> str:
    rug_value = (
        f"{rug_report.normalized_score}/100"
        if rug_report.normalized_score
        is not None
        else "Unavailable"
    )

    return (
        "🧪 <b>PAPER ENTRY</b>\n\n"

        f"Token: <b>{escape(snapshot.symbol)}</b>\n"
        f"Score: <b>{evaluation.score}/100</b>\n"

        "Position: <b>"
        f"{money(float(position['entry_total_cost']))}"
        "</b>\n"

        "Paper fill: <b>"
        f"{price(float(position['entry_fill_price']))}"
        "</b>\n"

        "Liquidity: "
        f"<b>{money(snapshot.liquidity_usd)}</b>\n"

        "Volume: "
        f"<b>{money(snapshot.volume_24h_usd)}</b>\n"

        "5m / 1h: "
        f"<b>{percentage(snapshot.change_5m_pct)} / "
        f"{percentage(snapshot.change_1h_pct)}</b>\n"

        "Buy ratio: "
        f"<b>{snapshot.buy_ratio_5m:.0%}</b>\n"

        f"RugCheck: <b>{escape(rug_value)}</b>\n\n"

        "<a href=\""
        f"{escape(snapshot.pair_url, quote=True)}"
        "\">Open DexScreener</a>\n"

        f"CA: <code>{escape(snapshot.token_address)}</code>"
    )


def format_exit(
    trade: dict[str, Any],
) -> str:
    pnl_usd = float(
        trade.get("pnl_usd", 0)
    )

    icon = "✅" if pnl_usd > 0 else "🛑"

    return (
        f"{icon} <b>PAPER EXIT — "
        f"{escape(str(trade.get('exit_reason', 'EXIT')))}"
        "</b>\n\n"

        "Token: <b>"
        f"{escape(str(trade.get('symbol', 'UNKNOWN')))}"
        "</b>\n"

        "P/L: <b>"
        f"{money(pnl_usd)} "
        f"({percentage(float(trade.get('pnl_pct', 0)))})"
        "</b>\n"

        "Entry: <b>"
        f"{price(float(trade.get('entry_fill_price', 0)))}"
        "</b>\n"

        "Exit: <b>"
        f"{price(float(trade.get('exit_fill_price', 0)))}"
        "</b>"
    )


# ============================================================
# RESEARCH ENGINE
# ============================================================

Notifier = Callable[
    [str],
    Awaitable[None],
]


class ResearchEngine:
    def __init__(
        self,
        settings: Settings,
        market: MarketDataClient,
        strategy: Strategy,
        broker: PaperBroker,
        store: StateStore,
    ):
        self.settings = settings
        self.market = market
        self.strategy = strategy
        self.broker = broker
        self.store = store

        self.notifier: Notifier | None = None
        self.scan_lock = asyncio.Lock()

        self.running = False
        self.started_at = time.monotonic()

        self.last_scan_at: str | None = None
        self.last_loop_at: str | None = None
        self.last_error: str | None = None

        self.candidates: list[
            tuple[
                MarketSnapshot,
                Evaluation,
                RugReport,
            ]
        ] = []

    def set_notifier(
        self,
        notifier: Notifier,
    ) -> None:
        self.notifier = notifier

    async def notify(
        self,
        message: str,
    ) -> None:
        if not self.notifier:
            return

        try:
            await self.notifier(message)
        except Exception as exc:
            LOGGER.warning(
                "Notification failed: %s",
                exc,
            )

    async def run(self) -> None:
        self.running = True
        await self.market.start()

        next_scan = 0.0
        next_position_update = 0.0

        next_status = (
            time.monotonic()
            + self.settings.status_interval_seconds
        )

        while self.running:
            self.last_loop_at = iso_now()
            now = time.monotonic()

            try:
                day_changed = (
                    await self.broker.ensure_day()
                )

                if day_changed:
                    await self.notify(
                        "🌅 <b>New Sydney trading day.</b> "
                        "Daily paper limits reset."
                    )

                if now >= next_position_update:
                    await self.update_positions()

                    next_position_update = (
                        time.monotonic()
                        + self.settings
                        .price_update_seconds
                    )

                if now >= next_scan:
                    await self.scan_market()

                    next_scan = (
                        time.monotonic()
                        + self.settings
                        .scan_interval_seconds
                    )

                if now >= next_status:
                    await self.notify(
                        format_status(
                            self.broker,
                            self.settings,
                            running=True,
                        )
                    )

                    next_status = (
                        time.monotonic()
                        + self.settings
                        .status_interval_seconds
                    )

                await asyncio.sleep(1)

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                self.last_error = str(exc)

                LOGGER.exception(
                    "Research loop error"
                )

                await asyncio.sleep(3)

    async def stop(self) -> None:
        self.running = False
        await self.store.save(
            self.broker.state
        )
        await self.market.close()

    async def scan_market(
        self,
    ) -> dict[str, Any]:
        if self.scan_lock.locked():
            return dict(
                self.broker.state.get(
                    "last_scan",
                    {},
                )
            )

        async with self.scan_lock:
            discoveries = (
                await self.market.discover_tokens()
            )

            snapshots = (
                await self.market
                .fetch_many_snapshots(
                    discoveries
                )
            )

            rejection_counts: Counter[str] = (
                Counter()
            )

            market_passed: list[
                MarketSnapshot
            ] = []

            for snapshot in snapshots:
                blocks = (
                    self.strategy.market_blocks(
                        snapshot
                    )
                )

                if blocks:
                    rejection_counts.update(
                        blocks
                    )
                else:
                    market_passed.append(
                        snapshot
                    )

            rug_results = await asyncio.gather(
                *(
                    self.market.fetch_rug_report(
                        snapshot.token_address
                    )
                    for snapshot in market_passed
                ),
                return_exceptions=True,
            )

            evaluated: list[
                tuple[
                    MarketSnapshot,
                    Evaluation,
                    RugReport,
                ]
            ] = []

            for snapshot, result in zip(
                market_passed,
                rug_results,
                strict=True,
            ):
                rug_report = (
                    result
                    if isinstance(
                        result,
                        RugReport,
                    )
                    else RugReport(
                        available=False,
                        error=str(result),
                    )
                )

                evaluation = (
                    self.strategy.evaluate(
                        snapshot,
                        rug_report,
                    )
                )

                if evaluation.blocks:
                    rejection_counts.update(
                        evaluation.blocks
                    )

                evaluated.append(
                    (
                        snapshot,
                        evaluation,
                        rug_report,
                    )
                )

            evaluated.sort(
                key=lambda item: (
                    item[1].passed,
                    item[1].score,
                    item[0].liquidity_usd,
                ),
                reverse=True,
            )

            self.candidates = evaluated[:10]

            qualified = [
                item
                for item in evaluated
                if item[1].passed
            ]

            stats = {
                "at": iso_now(),
                "discovered":
                    len(discoveries),
                "snapshots":
                    len(snapshots),
                "market_passed":
                    len(market_passed),
                "qualified":
                    len(qualified),
                "top_score": (
                    evaluated[0][1].score
                    if evaluated
                    else 0
                ),
                "top_blocks":
                    rejection_counts.most_common(5),
            }

            self.last_scan_at = stats["at"]

            self.broker.state[
                "last_scan"
            ] = stats

            await self.store.save(
                self.broker.state
            )

            for (
                snapshot,
                evaluation,
                rug_report,
            ) in qualified:
                if self.broker.entry_guard(
                    snapshot.token_address
                ):
                    continue

                position = (
                    await self.broker.open_position(
                        snapshot,
                        evaluation,
                    )
                )

                if position:
                    await self.notify(
                        format_entry(
                            position,
                            snapshot,
                            evaluation,
                            rug_report,
                        )
                    )

            await self.broker.refresh_safety_pause()

            self.last_error = None
            return stats

    async def update_positions(self) -> None:
        if not self.broker.positions:
            return

        saved_positions = [
            dict(position)
            for position
            in self.broker.positions.values()
        ]

        results = await asyncio.gather(
            *(
                self.market.fetch_snapshot(
                    str(
                        position["token_address"]
                    ),
                    ("open position",),
                    preferred_pair=str(
                        position.get(
                            "pair_address",
                            "",
                        )
                    ),
                )
                for position
                in saved_positions
            ),
            return_exceptions=True,
        )

        state_changed = False

        for saved_position, result in zip(
            saved_positions,
            results,
            strict=True,
        ):
            address = str(
                saved_position["token_address"]
            )

            if not isinstance(
                result,
                MarketSnapshot,
            ):
                continue

            decision = (
                self.broker.exit_decision(
                    result
                )
            )

            state_changed = True

            rug_report = (
                await self.market.fetch_rug_report(
                    address
                )
            )

            if rug_report.has_danger:
                decision.should_exit = True
                decision.reason = (
                    "RUGCHECK_DANGER"
                )

            if decision.should_exit:
                trade = (
                    await self.broker.close_position(
                        result,
                        decision.reason,
                    )
                )

                if trade:
                    await self.notify(
                        format_exit(trade)
                    )

        if state_changed:
            await self.store.save(
                self.broker.state
            )

    async def analyse_token(
        self,
        token_address: str,
    ) -> tuple[
        MarketSnapshot,
        Evaluation,
        RugReport,
    ] | None:
        snapshot = (
            await self.market.fetch_snapshot(
                token_address,
                ("manual scan",),
            )
        )

        if not snapshot:
            return None

        rug_report = (
            await self.market.fetch_rug_report(
                token_address,
                force=True,
            )
        )

        evaluation = self.strategy.evaluate(
            snapshot,
            rug_report,
        )

        return (
            snapshot,
            evaluation,
            rug_report,
        )

    def health(self) -> dict[str, Any]:
        return {
            "ok": self.running,
            "mode": "paper",
            "uptime_seconds": int(
                time.monotonic()
                - self.started_at
            ),
            "last_loop_at":
                self.last_loop_at,
            "last_scan_at":
                self.last_scan_at,
            "open_positions":
                len(self.broker.positions),
            "equity":
                round(self.broker.equity(), 2),
            "api_requests":
                self.market.request_count,
            "last_error":
                self.last_error
                or self.market.last_error,
        }


# ============================================================
# RAILWAY HEALTH SERVER
# ============================================================

class HealthServer:
    def __init__(
        self,
        engine: ResearchEngine,
        port: int,
    ):
        self.engine = engine
        self.port = port
        self.runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()

        app.router.add_get(
            "/",
            self.index,
        )

        app.router.add_get(
            "/health",
            self.health,
        )

        self.runner = web.AppRunner(app)
        await self.runner.setup()

        site = web.TCPSite(
            self.runner,
            "0.0.0.0",
            self.port,
        )

        await site.start()

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def index(
        self,
        request: web.Request,
    ) -> web.Response:
        return web.json_response(
            {
                "service": "Jarvis V2.1",
                "mode": "paper",
                "health": "/health",
            }
        )

    async def health(
        self,
        request: web.Request,
    ) -> web.Response:
        result = self.engine.health()

        return web.json_response(
            result,
            status=(
                200
                if result["ok"]
                else 503
            ),
        )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

SOLANA_ADDRESS_PATTERN = re.compile(
    r"^[1-9A-HJ-NP-Za-km-z]{32,44}$"
)


class TelegramInterface:
    def __init__(
        self,
        settings: Settings,
        engine: ResearchEngine,
        broker: PaperBroker,
    ):
        self.settings = settings
        self.engine = engine
        self.broker = broker

    def authorised(
        self,
        update: Update,
    ) -> bool:
        chat = update.effective_chat

        return bool(
            chat
            and chat.id
            in self.settings.allowed_chat_ids
        )

    async def reply(
        self,
        update: Update,
        message: str,
    ) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(
                message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

    async def reject(
        self,
        update: Update,
    ) -> None:
        await self.reply(
            update,
            "This chat is not authorised "
            "for Jarvis.",
        )

    def create_application(
        self,
        *,
        post_init: Callable,
        post_shutdown: Callable,
    ) -> Application:
        application = (
            ApplicationBuilder()
            .token(
                self.settings.telegram_bot_token
            )
            .post_init(post_init)
            .post_shutdown(post_shutdown)
            .build()
        )

        command_handlers = {
            "start": self.start,
            "status": self.status,
            "paper": self.status,
            "today": self.today,
            "positions": self.positions,
            "trades": self.trades,
            "scan": self.scan,
            "candidates": self.candidates,
            "health": self.health,
            "settings": self.show_settings,
            "pause": self.pause,
            "resume": self.resume,
            "resetpaper": self.resetpaper,
            "help": self.help_command,
        }

        for command, handler in (
            command_handlers.items()
        ):
            application.add_handler(
                CommandHandler(
                    command,
                    handler,
                )
            )

        application.add_error_handler(
            self.error_handler
        )

        return application

    async def configure_commands(
        self,
        application: Application,
    ) -> None:
        await application.bot.set_my_commands(
            [
                BotCommand(
                    "status",
                    "Account and risk status",
                ),
                BotCommand(
                    "scan",
                    "Scan market or token address",
                ),
                BotCommand(
                    "candidates",
                    "Latest ranked candidates",
                ),
                BotCommand(
                    "positions",
                    "Open paper positions",
                ),
                BotCommand(
                    "trades",
                    "Completed paper trades",
                ),
                BotCommand(
                    "today",
                    "Today's paper results",
                ),
                BotCommand(
                    "health",
                    "Service health",
                ),
                BotCommand(
                    "pause",
                    "Pause new entries",
                ),
                BotCommand(
                    "resume",
                    "Resume new entries",
                ),
                BotCommand(
                    "settings",
                    "Strategy settings",
                ),
                BotCommand(
                    "help",
                    "Show all commands",
                ),
            ]
        )

    async def start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        await self.reply(
            update,
            "🤖 <b>JARVIS V2.1 IS ONLINE</b>\n\n"
            "Live Solana research with "
            "simulated money only.\n"
            "No wallet is connected.\n\n"
            "Use /scan to scan the market now.",
        )

    async def status(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        await self.reply(
            update,
            format_status(
                self.broker,
                self.settings,
                running=self.engine.running,
            ),
        )

    async def scan(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        if not context.args:
            if self.engine.scan_lock.locked():
                await self.reply(
                    update,
                    "🔎 <b>A market scan is "
                    "already running.</b>",
                )
                return

            await self.reply(
                update,
                "🔎 <b>JARVIS MARKET SCAN "
                "STARTED</b>",
            )

            try:
                stats = (
                    await self.engine.scan_market()
                )

            except Exception as exc:
                LOGGER.exception(
                    "Manual scan failed"
                )

                await self.reply(
                    update,
                    "❌ <b>SCAN FAILED</b>\n\n"
                    f"{escape(str(exc))}",
                )
                return

            top_blocks = stats.get(
                "top_blocks",
                [],
            )

            rejection_text = (
                "\n".join(
                    "• "
                    f"{escape(str(reason))}: "
                    f"<b>{count}</b>"
                    for reason, count
                    in top_blocks
                )
                if top_blocks
                else "None"
            )

            await self.reply(
                update,
                "✅ <b>MARKET SCAN FINISHED</b>\n\n"

                "Discovered: "
                f"<b>{stats.get('discovered', 0)}</b>\n"

                "Valid prices: "
                f"<b>{stats.get('snapshots', 0)}</b>\n"

                "Passed market filters: "
                f"<b>{stats.get('market_passed', 0)}</b>\n"

                "Qualified: "
                f"<b>{stats.get('qualified', 0)}</b>\n"

                "Top score: "
                f"<b>{stats.get('top_score', 0)}/100</b>\n\n"

                "<b>Main rejection reasons</b>\n"
                f"{rejection_text}",
            )

            return

        token_address = (
            context.args[0].strip()
        )

        if not SOLANA_ADDRESS_PATTERN.fullmatch(
            token_address
        ):
            await self.reply(
                update,
                "Usage:\n"
                "<code>/scan</code>\n"
                "or\n"
                "<code>/scan SOLANA_TOKEN_ADDRESS</code>",
            )
            return

        await self.reply(
            update,
            "🔎 Researching that token with "
            "DexScreener and RugCheck…",
        )

        result = await self.engine.analyse_token(
            token_address
        )

        if not result:
            await self.reply(
                update,
                "No valid Solana pair was found.",
            )
            return

        snapshot, evaluation, rug_report = result

        verdict = (
            "✅ QUALIFIES"
            if evaluation.passed
            else "⛔️ DOES NOT QUALIFY"
        )

        blocks = (
            ", ".join(
                escape(block)
                for block
                in evaluation.blocks[:6]
            )
            or "None"
        )

        rug_value = (
            f"{rug_report.normalized_score}/100"
            if rug_report.normalized_score
            is not None
            else "Unavailable"
        )

        await self.reply(
            update,
            "🧠 <b>JARVIS ANALYSIS — "
            f"{verdict}</b>\n\n"

            f"Token: <b>{escape(snapshot.symbol)}</b>\n"
            f"Score: <b>{evaluation.score}/100</b>\n"
            f"Price: <b>{price(snapshot.price_usd)}</b>\n"

            "Liquidity: "
            f"<b>{money(snapshot.liquidity_usd)}</b>\n"

            "24h volume: "
            f"<b>{money(snapshot.volume_24h_usd)}</b>\n"

            "5m / 1h: "
            f"<b>{percentage(snapshot.change_5m_pct)} / "
            f"{percentage(snapshot.change_1h_pct)}</b>\n"

            "Buy ratio: "
            f"<b>{snapshot.buy_ratio_5m:.0%}</b>\n"

            f"RugCheck: <b>{escape(rug_value)}</b>\n"
            f"Blocks: {blocks}\n\n"

            "<a href=\""
            f"{escape(snapshot.pair_url, quote=True)}"
            "\">Open DexScreener</a>\n"

            f"CA: <code>{escape(snapshot.token_address)}</code>",
        )

    async def candidates(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        if not self.engine.candidates:
            await self.reply(
                update,
                "🔎 <b>No candidates passed the "
                "market filters in the latest scan.</b>\n\n"
                "Run /scan to see the rejection reasons.",
            )
            return

        lines = [
            "🔎 <b>LATEST RANKED CANDIDATES</b>"
        ]

        for (
            snapshot,
            evaluation,
            rug_report,
        ) in self.engine.candidates[:5]:
            icon = (
                "✅"
                if evaluation.passed
                else "⚪️"
            )

            rug_value = (
                rug_report.normalized_score
                if rug_report.normalized_score
                is not None
                else "—"
            )

            lines.extend(
                [
                    "",
                    f"{icon} <b>"
                    f"{escape(snapshot.symbol)}</b> "
                    f"— {evaluation.score}/100",

                    "Liquidity "
                    f"{money(snapshot.liquidity_usd)} "
                    "· Volume "
                    f"{money(snapshot.volume_24h_usd)}",

                    "5m "
                    f"{percentage(snapshot.change_5m_pct)} "
                    "· Buys "
                    f"{snapshot.buy_ratio_5m:.0%} "
                    f"· Rug {rug_value}",

                    f"<code>{escape(snapshot.token_address)}</code>",
                ]
            )

        await self.reply(
            update,
            "\n".join(lines),
        )

    async def today(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        state = self.broker.state

        await self.reply(
            update,
            "📅 <b>TODAY — SYDNEY TIME</b>\n\n"

            "Equity change: "
            f"<b>{money(self.broker.today_pnl())}</b>\n"

            "Entries: "
            f"<b>{state.get('day_entries', 0)}/"
            f"{self.settings.max_daily_entries}</b>\n"

            "Wins / losses: "
            f"<b>{state.get('day_wins', 0)} / "
            f"{state.get('day_losses', 0)}</b>\n"

            "Daily loss: "
            f"<b>{self.broker.daily_loss_pct():.2f}%</b>",
        )

    async def positions(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        if not self.broker.positions:
            await self.reply(
                update,
                "📭 <b>No open paper positions.</b>",
            )
            return

        lines = [
            "📊 <b>OPEN PAPER POSITIONS</b>"
        ]

        for position in (
            self.broker.positions.values()
        ):
            current_value = (
                self.broker.estimated_exit_value(
                    position
                )
            )

            entry_cost = float(
                position.get(
                    "entry_total_cost",
                    0,
                )
            )

            pnl_usd = (
                current_value - entry_cost
            )

            pnl_pct = (
                pnl_usd / entry_cost * 100
                if entry_cost
                else 0
            )

            lines.extend(
                [
                    "",
                    "<b>"
                    f"{escape(str(position.get('symbol', 'UNKNOWN')))}"
                    "</b>",

                    "Value: "
                    f"{money(current_value)}",

                    "P/L: "
                    f"<b>{money(pnl_usd)} "
                    f"({percentage(pnl_pct)})</b>",

                    "Score: "
                    f"{position.get('score', 0)}/100",
                ]
            )

        await self.reply(
            update,
            "\n".join(lines),
        )

    async def trades(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        trades = self.broker.state.get(
            "trades",
            [],
        )[-10:]

        if not trades:
            await self.reply(
                update,
                "📭 <b>No completed paper trades.</b>",
            )
            return

        lines = [
            "📚 <b>LAST PAPER TRADES</b>"
        ]

        for trade in reversed(trades):
            pnl_usd = float(
                trade.get("pnl_usd", 0)
            )

            icon = (
                "✅" if pnl_usd > 0 else "❌"
            )

            lines.append(
                f"{icon} <b>"
                f"{escape(str(trade.get('symbol', 'UNKNOWN')))}"
                "</b> "
                f"{money(pnl_usd)} "
                f"({percentage(float(trade.get('pnl_pct', 0)))}) "
                "— "
                f"{escape(str(trade.get('exit_reason', 'EXIT')))}"
            )

        await self.reply(
            update,
            "\n".join(lines),
        )

    async def health(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        result = self.engine.health()

        await self.reply(
            update,
            "🩺 <b>JARVIS HEALTH</b>\n\n"

            "Loop: "
            f"<b>{'running' if result['ok'] else 'stopped'}</b>\n"

            "Uptime: "
            f"<b>{result['uptime_seconds']} seconds</b>\n"

            "API requests: "
            f"<b>{result['api_requests']}</b>\n"

            "Last scan: "
            f"<b>{escape(str(result['last_scan_at'] or 'not yet'))}</b>\n"

            "Last error: "
            f"<b>{escape(str(result['last_error'] or 'none'))}</b>",
        )

    async def show_settings(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        settings = self.settings

        await self.reply(
            update,
            "⚙️ <b>JARVIS V2.1 SETTINGS</b>\n\n"

            "Balance: "
            f"<b>{money(settings.starting_balance_usd)}</b>\n"

            "Risk/trade: "
            f"<b>{settings.risk_per_trade_pct:.2f}%</b>\n"

            "Max position: "
            f"<b>{settings.max_position_pct:.1f}%</b>\n"

            "Open positions: "
            f"<b>{settings.max_open_positions}</b>\n"

            "Stop / target: "
            f"<b>{settings.stop_loss_pct:.1f}% / "
            f"+{settings.take_profit_pct:.1f}%</b>\n"

            "Daily loss limit: "
            f"<b>{settings.max_daily_loss_pct:.1f}%</b>\n"

            "Maximum loss streak: "
            f"<b>{settings.max_losing_streak}</b>\n"

            "Maximum entries: "
            f"<b>{settings.max_daily_entries}</b>\n"

            "Minimum score: "
            f"<b>{settings.min_score}/100</b>\n\n"

            "Mode: 🧪 <b>PAPER TRADING</b>",
        )

    async def pause(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        await self.broker.set_manual_pause(
            True
        )

        await self.reply(
            update,
            "⏸ <b>NEW PAPER ENTRIES PAUSED.</b>\n"
            "Open positions remain monitored.",
        )

    async def resume(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        await self.broker.set_manual_pause(
            False
        )

        safety_reason = (
            await self.broker
            .refresh_safety_pause()
        )

        if safety_reason:
            await self.reply(
                update,
                "⚠️ Cannot resume: "
                f"<b>{escape(safety_reason)}</b>",
            )
        else:
            await self.reply(
                update,
                "▶️ <b>NEW PAPER ENTRIES "
                "RESUMED.</b>",
            )

    async def resetpaper(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        if (
            not context.args
            or context.args[0].upper()
            != "CONFIRM"
        ):
            await self.reply(
                update,
                "To erase all simulated history, "
                "send:\n"
                "<code>/resetpaper CONFIRM</code>",
            )
            return

        await self.broker.reset()

        await self.reply(
            update,
            "♻️ <b>PAPER ACCOUNT RESET TO "
            f"{money(self.settings.starting_balance_usd)}</b>",
        )

    async def help_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorised(update):
            await self.reject(update)
            return

        await self.reply(
            update,
            "🤖 <b>JARVIS V2.1 COMMANDS</b>\n\n"

            "/scan — scan the market now\n"
            "/scan &lt;address&gt; — analyse token\n"
            "/status — account and latest scan\n"
            "/candidates — ranked candidates\n"
            "/today — today's performance\n"
            "/positions — open positions\n"
            "/trades — completed trades\n"
            "/health — service health\n"
            "/settings — current settings\n"
            "/pause — pause new entries\n"
            "/resume — resume entries\n"
            "/resetpaper CONFIRM — reset account",
        )

    async def error_handler(
        self,
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        LOGGER.exception(
            "Telegram command error",
            exc_info=context.error,
        )


# ============================================================
# APPLICATION
# ============================================================

def configure_logging() -> None:
    log_level = os.getenv(
        "LOG_LEVEL",
        "INFO",
    ).upper()

    logging.basicConfig(
        level=getattr(
            logging,
            log_level,
            logging.INFO,
        ),
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    logging.getLogger(
        "httpx"
    ).setLevel(logging.WARNING)

    logging.getLogger(
        "aiohttp.access"
    ).setLevel(logging.WARNING)


def main() -> None:
    load_dotenv()
    configure_logging()

    settings = Settings.from_env()

    store = StateStore(settings)

    broker = PaperBroker(
        settings,
        store,
    )

    market = MarketDataClient(settings)
    strategy = Strategy(settings)

    engine = ResearchEngine(
        settings,
        market,
        strategy,
        broker,
        store,
    )

    telegram_interface = TelegramInterface(
        settings,
        engine,
        broker,
    )

    health_server = HealthServer(
        engine,
        settings.port,
    )

    application: Application

    async def notify(message: str) -> None:
        await application.bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def post_init(
        app: Application,
    ) -> None:
        engine.set_notifier(notify)

        await telegram_interface.configure_commands(
            app
        )

        await health_server.start()

        task = asyncio.create_task(
            engine.run(),
            name="jarvis-research-engine",
        )

        app.bot_data["engine_task"] = task

        await notify(
            "🤖 <b>JARVIS V2.1 STARTED</b>\n"
            "Mode: 🧪 <b>PAPER TRADING ONLY</b>\n"
            "Use /scan to run a visible market scan."
        )

        LOGGER.info(
            "Jarvis V2.1 started"
        )

    async def post_shutdown(
        app: Application,
    ) -> None:
        task = app.bot_data.get(
            "engine_task"
        )

        if task:
            task.cancel()

            await asyncio.gather(
                task,
                return_exceptions=True,
            )

        await engine.stop()
        await health_server.stop()

    application = (
        telegram_interface.create_application(
            post_init=post_init,
            post_shutdown=post_shutdown,
        )
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=True,
    )


if __name__ == "__main__":
    main()
