from __future__ import annotations

"""
Jarvis V2.1 — single-file, paper-only Solana research bot.
Paste this entire file into BOT.PY, replacing all old code.
It intentionally contains no live-wallet execution code.
"""


# ========================================================================
# Combined from: jarvis/utils.py
# ========================================================================
import math
from datetime import datetime, timezone
from typing import Any


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


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
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


def pct(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}%"


def short_address(value: str) -> str:
    return value if len(value) <= 14 else f"{value[:6]}…{value[-5:]}"


# ========================================================================
# Combined from: jarvis/config.py
# ========================================================================
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Mapping


class ConfigurationError(ValueError):
    """Raised when required or safety-critical configuration is invalid."""


def _text(env: Mapping[str, str], name: str, default: str = "") -> str:
    return str(env.get(name, default)).strip()


def _integer(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _text(env, name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a whole number") from exc


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = _text(env, name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _text(env, name)
    if not raw:
        return default
    value = raw.lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _chat_ids(raw: str) -> frozenset[int]:
    result: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError as exc:
            raise ConfigurationError(
                "TELEGRAM_ALLOWED_CHAT_IDS must contain comma-separated numbers"
            ) from exc
    return frozenset(result)


@dataclass(frozen=True, slots=True)
class Settings:
    # Telegram and persistence
    telegram_bot_token: str = ""
    telegram_chat_id: int = 0
    allowed_chat_ids: frozenset[int] = frozenset()
    timezone_name: str = "Australia/Sydney"
    state_path: Path = Path("data/jarvis_state.json")
    port: int = 8080

    # Scheduler and API behavior
    scan_interval_seconds: int = 30
    price_update_seconds: int = 10
    status_interval_seconds: int = 300
    max_tokens_per_scan: int = 30
    http_concurrency: int = 6
    api_timeout_seconds: int = 12
    rugcheck_cache_minutes: int = 30

    # Paper account and portfolio limits
    starting_balance_usd: float = 1_000.0
    risk_per_trade_pct: float = 0.50
    max_position_pct: float = 10.0
    max_open_positions: int = 2
    max_daily_entries: int = 10
    max_daily_loss_pct: float = 2.0
    max_losing_streak: int = 3
    daily_profit_target_usd: float = 0.0
    trade_cooldown_minutes: int = 120

    # Exit model
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

    # Market hard filters
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

    # RugCheck safety layer. score_normalised is a risk score: lower is safer.
    rugcheck_required: bool = True
    rugcheck_max_normalized: int = 60

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source: Mapping[str, str] = os.environ if env is None else env
        defaults = cls()

        # This release is intentionally unable to trade real funds.
        live_flags = ("ENABLE_LIVE_TRADING", "ENABLE_MAINNET_TRADING", "LIVE_TRADING")
        for name in live_flags:
            if _boolean(source, name, False):
                raise ConfigurationError(
                    f"{name}=true is refused: Jarvis V2 is paper-trading only"
                )
        private_key_names = (
            "SOLANA_PRIVATE_KEY",
            "WALLET_PRIVATE_KEY",
            "PHANTOM_PRIVATE_KEY",
        )
        present_keys = [name for name in private_key_names if _text(source, name)]
        if present_keys:
            raise ConfigurationError(
                "Remove wallet secrets before starting this paper bot: "
                + ", ".join(present_keys)
            )

        token = _text(source, "TELEGRAM_BOT_TOKEN")
        chat_id = _integer(source, "TELEGRAM_CHAT_ID", 0)
        allowed = _chat_ids(_text(source, "TELEGRAM_ALLOWED_CHAT_IDS"))
        if chat_id:
            allowed = frozenset(set(allowed) | {chat_id})

        settings = cls(
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            allowed_chat_ids=allowed,
            timezone_name=_text(source, "TIMEZONE", defaults.timezone_name),
            state_path=Path(_text(source, "STATE_PATH", str(defaults.state_path))),
            port=_integer(source, "PORT", defaults.port),
            scan_interval_seconds=_integer(source, "SCAN_INTERVAL_SECONDS", defaults.scan_interval_seconds),
            price_update_seconds=_integer(source, "PRICE_UPDATE_SECONDS", defaults.price_update_seconds),
            status_interval_seconds=_integer(source, "STATUS_INTERVAL_SECONDS", defaults.status_interval_seconds),
            max_tokens_per_scan=_integer(source, "MAX_TOKENS_PER_SCAN", defaults.max_tokens_per_scan),
            http_concurrency=_integer(source, "HTTP_CONCURRENCY", defaults.http_concurrency),
            api_timeout_seconds=_integer(source, "API_TIMEOUT_SECONDS", defaults.api_timeout_seconds),
            rugcheck_cache_minutes=_integer(source, "RUGCHECK_CACHE_MINUTES", defaults.rugcheck_cache_minutes),
            starting_balance_usd=_number(source, "STARTING_BALANCE_USD", defaults.starting_balance_usd),
            risk_per_trade_pct=_number(source, "RISK_PER_TRADE_PCT", defaults.risk_per_trade_pct),
            max_position_pct=_number(source, "MAX_POSITION_PCT", defaults.max_position_pct),
            max_open_positions=_integer(source, "MAX_OPEN_POSITIONS", defaults.max_open_positions),
            max_daily_entries=_integer(source, "MAX_DAILY_ENTRIES", defaults.max_daily_entries),
            max_daily_loss_pct=_number(source, "MAX_DAILY_LOSS_PCT", defaults.max_daily_loss_pct),
            max_losing_streak=_integer(source, "MAX_LOSING_STREAK", defaults.max_losing_streak),
            daily_profit_target_usd=_number(source, "DAILY_PROFIT_TARGET_USD", defaults.daily_profit_target_usd),
            trade_cooldown_minutes=_integer(source, "TRADE_COOLDOWN_MINUTES", defaults.trade_cooldown_minutes),
            stop_loss_pct=_number(source, "STOP_LOSS_PCT", defaults.stop_loss_pct),
            take_profit_pct=_number(source, "TAKE_PROFIT_PCT", defaults.take_profit_pct),
            trailing_activation_pct=_number(source, "TRAILING_ACTIVATION_PCT", defaults.trailing_activation_pct),
            trailing_distance_pct=_number(source, "TRAILING_DISTANCE_PCT", defaults.trailing_distance_pct),
            max_hold_minutes=_integer(source, "MAX_HOLD_MINUTES", defaults.max_hold_minutes),
            momentum_exit_5m_pct=_number(source, "MOMENTUM_EXIT_5M_PCT", defaults.momentum_exit_5m_pct),
            momentum_exit_buy_ratio=_number(source, "MOMENTUM_EXIT_BUY_RATIO", defaults.momentum_exit_buy_ratio),
            liquidity_exit_fraction=_number(source, "LIQUIDITY_EXIT_FRACTION", defaults.liquidity_exit_fraction),
            paper_slippage_bps=_number(source, "PAPER_SLIPPAGE_BPS", defaults.paper_slippage_bps),
            paper_fee_bps=_number(source, "PAPER_FEE_BPS", defaults.paper_fee_bps),
            min_liquidity_usd=_number(source, "MIN_LIQUIDITY_USD", defaults.min_liquidity_usd),
            min_volume_24h_usd=_number(source, "MIN_VOLUME_24H_USD", defaults.min_volume_24h_usd),
            min_market_cap_usd=_number(source, "MIN_MARKET_CAP_USD", defaults.min_market_cap_usd),
            max_market_cap_usd=_number(source, "MAX_MARKET_CAP_USD", defaults.max_market_cap_usd),
            min_pair_age_minutes=_number(source, "MIN_PAIR_AGE_MINUTES", defaults.min_pair_age_minutes),
            max_pair_age_minutes=_number(source, "MAX_PAIR_AGE_MINUTES", defaults.max_pair_age_minutes),
            min_5m_change_pct=_number(source, "MIN_5M_CHANGE_PCT", defaults.min_5m_change_pct),
            max_5m_change_pct=_number(source, "MAX_5M_CHANGE_PCT", defaults.max_5m_change_pct),
            min_1h_change_pct=_number(source, "MIN_1H_CHANGE_PCT", defaults.min_1h_change_pct),
            max_1h_change_pct=_number(source, "MAX_1H_CHANGE_PCT", defaults.max_1h_change_pct),
            min_5m_buys=_integer(source, "MIN_5M_BUYS", defaults.min_5m_buys),
            min_5m_buy_ratio=_number(source, "MIN_5M_BUY_RATIO", defaults.min_5m_buy_ratio),
            min_liquidity_to_mcap=_number(source, "MIN_LIQUIDITY_TO_MCAP", defaults.min_liquidity_to_mcap),
            max_volume_to_liquidity=_number(source, "MAX_VOLUME_TO_LIQUIDITY", defaults.max_volume_to_liquidity),
            min_score=_integer(source, "MIN_SCORE", defaults.min_score),
            rugcheck_required=_boolean(source, "RUGCHECK_REQUIRED", defaults.rugcheck_required),
            rugcheck_max_normalized=_integer(source, "RUGCHECK_MAX_NORMALIZED", defaults.rugcheck_max_normalized),
        )
        settings.validate(require_telegram=True)
        return settings

    def validate(self, *, require_telegram: bool = False) -> None:
        if require_telegram and not self.telegram_bot_token:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN is required")
        if require_telegram and not self.telegram_chat_id:
            raise ConfigurationError("TELEGRAM_CHAT_ID is required")
        if self.starting_balance_usd <= 0:
            raise ConfigurationError("STARTING_BALANCE_USD must be positive")
        if not 0 < self.risk_per_trade_pct <= self.max_position_pct <= 100:
            raise ConfigurationError(
                "Risk and position percentages must satisfy 0 < risk <= max position <= 100"
            )
        if not 0 < self.stop_loss_pct < self.take_profit_pct:
            raise ConfigurationError("STOP_LOSS_PCT must be positive and below TAKE_PROFIT_PCT")
        if not 0 <= self.trailing_distance_pct < self.trailing_activation_pct:
            raise ConfigurationError(
                "TRAILING_DISTANCE_PCT must be below TRAILING_ACTIVATION_PCT"
            )
        if self.min_score < 0 or self.min_score > 100:
            raise ConfigurationError("MIN_SCORE must be between 0 and 100")
        positive_ints = (
            self.scan_interval_seconds,
            self.price_update_seconds,
            self.max_tokens_per_scan,
            self.max_open_positions,
            self.max_daily_entries,
        )
        if any(value <= 0 for value in positive_ints):
            raise ConfigurationError("Intervals and limits must be positive")

    def public_dict(self) -> dict[str, Any]:
        """Return safe settings for /settings (never includes bot credentials)."""
        hidden = {"telegram_bot_token", "telegram_chat_id", "allowed_chat_ids", "state_path"}
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name not in hidden
        }


# ========================================================================
# Combined from: jarvis/models.py
# ========================================================================
from dataclasses import dataclass, field


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
        return self.buys_5m / total if total else 0.0

    @property
    def buy_ratio_1h(self) -> float:
        total = self.buys_1h + self.sells_1h
        return self.buys_1h / total if total else 0.0

    @property
    def liquidity_to_mcap(self) -> float:
        cap = self.market_cap_usd or self.fdv_usd
        return self.liquidity_usd / cap if cap > 0 else 0.0

    @property
    def volume_to_liquidity(self) -> float:
        return self.volume_24h_usd / self.liquidity_usd if self.liquidity_usd > 0 else 0.0


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
    blocks: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    components: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ExitDecision:
    should_exit: bool
    reason: str = ""
    pnl_pct: float = 0.0


# ========================================================================
# Combined from: jarvis/state.py
# ========================================================================
import asyncio
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any



SCHEMA_VERSION = 2


def new_state(settings: Settings, *, day: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "initial_balance": settings.starting_balance_usd,
        "cash": settings.starting_balance_usd,
        "positions": {},
        "trades": [],
        "manual_paused": False,
        "safety_pause_reason": None,
        "day": day,
        "day_start_equity": settings.starting_balance_usd,
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
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


class StateStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.state_path
        self.lock = asyncio.Lock()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return new_state(self.settings)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("state root must be an object")
            if safe_schema := payload.get("schema_version"):
                if int(safe_schema) > SCHEMA_VERSION:
                    raise ValueError("state was created by a newer Jarvis version")
            return self._migrate(payload)
        except (OSError, ValueError, json.JSONDecodeError):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return new_state(self.settings)

    def _migrate(self, payload: dict[str, Any]) -> dict[str, Any]:
        baseline = new_state(self.settings)
        baseline.update(payload)
        baseline["schema_version"] = SCHEMA_VERSION
        baseline.setdefault("positions", {})
        baseline.setdefault("trades", [])
        baseline.setdefault("cooldown_until", {})
        baseline.setdefault("last_scan", new_state(self.settings)["last_scan"])
        return baseline

    async def save(self, state: dict[str, Any]) -> None:
        async with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            # Keep the state bounded for long-running Railway deployments.
            state["trades"] = list(state.get("trades", []))[-2_000:]
            serialized = json.dumps(state, indent=2, sort_keys=True, allow_nan=False)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(serialized, encoding="utf-8")
            os.replace(temp, self.path)

    @staticmethod
    def snapshot(state: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(state)


# ========================================================================
# Combined from: jarvis/api.py
# ========================================================================
import asyncio
import logging
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

import aiohttp


LOGGER = logging.getLogger(__name__)


class APIError(RuntimeError):
    pass


class MarketDataClient:
    DEX_BASE = "https://api.dexscreener.com"
    RUG_BASE = "https://api.rugcheck.xyz"
    ALLOWED_QUOTES = {"SOL", "WSOL", "USDC", "USDT"}

    def __init__(self, settings: Settings):
        self.settings = settings
        self.session: aiohttp.ClientSession | None = None
        self.semaphore = asyncio.Semaphore(settings.http_concurrency)
        self.rug_cache: dict[str, tuple[float, RugReport]] = {}
        self.last_error: str | None = None
        self.request_count = 0

    async def start(self) -> None:
        if self.session and not self.session.closed:
            return
        timeout = aiohttp.ClientTimeout(total=self.settings.api_timeout_seconds)
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "JarvisV2-PaperResearch/2.0"},
        )

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    async def _get_json(self, url: str, *, attempts: int = 3) -> Any:
        await self.start()
        assert self.session is not None
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with self.semaphore:
                    self.request_count += 1
                    async with self.session.get(url) as response:
                        if response.status == 429:
                            retry_after = safe_float(response.headers.get("Retry-After"), 1.0)
                            await asyncio.sleep(min(8.0, max(0.5, retry_after)))
                            continue
                        if response.status >= 500:
                            raise APIError(f"HTTP {response.status} from {url}")
                        if response.status >= 400:
                            body = (await response.text())[:200]
                            raise APIError(f"HTTP {response.status} from {url}: {body}")
                        return await response.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, APIError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.75 * (2**attempt))
        self.last_error = str(last_error or "unknown API error")
        raise APIError(self.last_error)

    async def discover_token_addresses(self) -> list[tuple[str, tuple[str, ...]]]:
        """Collect a balanced, deduplicated Solana token universe.

        Results are interleaved by source. This prevents one large endpoint
        (usually the latest profiles feed) from consuming the entire scan cap.
        """
        endpoints = (
            (f"{self.DEX_BASE}/token-profiles/latest/v1", "dex_profile"),
            (f"{self.DEX_BASE}/token-boosts/top/v1", "dex_boost_top"),
            (f"{self.DEX_BASE}/token-boosts/latest/v1", "dex_boost_latest"),
            (f"{self.RUG_BASE}/v1/stats/new_tokens", "rug_new"),
            (f"{self.RUG_BASE}/v1/stats/trending", "rug_trending"),
        )
        responses = await asyncio.gather(
            *(self._get_json(url, attempts=2) for url, _ in endpoints),
            return_exceptions=True,
        )
        buckets: list[tuple[str, list[str]]] = []
        for response, (_, source) in zip(responses, endpoints, strict=True):
            if isinstance(response, Exception):
                LOGGER.warning("Discovery source %s unavailable: %s", source, response)
                continue
            items = (
                response
                if isinstance(response, list)
                else response.get("tokens", [])
                if isinstance(response, dict)
                else []
            )
            bucket: list[str] = []
            seen_in_source: set[str] = set()
            for item in items:
                if not isinstance(item, dict):
                    continue
                address = item.get("tokenAddress") or item.get("mint") or item.get("address")
                chain = item.get("chainId", "solana")
                if (
                    isinstance(address, str)
                    and address.strip()
                    and str(chain).lower() == "solana"
                    and address.strip() not in seen_in_source
                ):
                    address = address.strip()
                    seen_in_source.add(address)
                    bucket.append(address)
            buckets.append((source, bucket))

        cap = self.settings.max_tokens_per_scan
        discovered: dict[str, set[str]] = {}
        order: list[str] = []
        largest_bucket = max((len(bucket) for _, bucket in buckets), default=0)
        for index in range(largest_bucket):
            for source, bucket in buckets:
                if index >= len(bucket):
                    continue
                address = bucket[index]
                if address not in discovered:
                    discovered[address] = set()
                    order.append(address)
                discovered[address].add(source)
                if len(order) >= cap:
                    return [
                        (item, tuple(sorted(discovered[item])))
                        for item in order
                    ]

        return [
            (address, tuple(sorted(discovered[address])))
            for address in order
        ]

    async def fetch_snapshot(
        self,
        token_address: str,
        sources: Iterable[str] = (),
        *,
        preferred_pair: str | None = None,
    ) -> MarketSnapshot | None:
        url = f"{self.DEX_BASE}/token-pairs/v1/solana/{quote(token_address, safe='')}"
        payload = await self._get_json(url)
        if not isinstance(payload, list):
            return None

        valid_pairs: list[dict[str, Any]] = []
        for pair in payload:
            if not isinstance(pair, dict) or pair.get("chainId") != "solana":
                continue
            base = pair.get("baseToken") or {}
            quote_token = pair.get("quoteToken") or {}
            # DexScreener priceUsd describes the base token. Never treat quote-token
            # data as if it were the discovered token.
            if base.get("address") != token_address:
                continue
            if str(quote_token.get("symbol", "")).upper() not in self.ALLOWED_QUOTES:
                continue
            if safe_float(pair.get("priceUsd")) <= 0:
                continue
            valid_pairs.append(pair)
        if not valid_pairs:
            return None

        selected = None
        if preferred_pair:
            selected = next(
                (pair for pair in valid_pairs if pair.get("pairAddress") == preferred_pair),
                None,
            )
        if selected is None:
            selected = max(
                valid_pairs,
                key=lambda pair: safe_float((pair.get("liquidity") or {}).get("usd")),
            )

        now_ms = time.time() * 1000
        created_ms = safe_float(selected.get("pairCreatedAt"), now_ms)
        age_minutes = max(0.0, (now_ms - created_ms) / 60_000)
        base = selected.get("baseToken") or {}
        quote_token = selected.get("quoteToken") or {}
        txns = selected.get("txns") or {}
        m5_tx = txns.get("m5") or {}
        h1_tx = txns.get("h1") or {}
        changes = selected.get("priceChange") or {}
        volume = selected.get("volume") or {}
        liquidity = selected.get("liquidity") or {}
        boosts = selected.get("boosts") or {}

        return MarketSnapshot(
            token_address=token_address,
            symbol=str(base.get("symbol") or "UNKNOWN")[:30],
            name=str(base.get("name") or base.get("symbol") or "Unknown")[:80],
            pair_address=str(selected.get("pairAddress") or ""),
            dex_id=str(selected.get("dexId") or "unknown"),
            quote_symbol=str(quote_token.get("symbol") or "").upper(),
            price_usd=safe_float(selected.get("priceUsd")),
            liquidity_usd=safe_float(liquidity.get("usd")),
            volume_24h_usd=safe_float(volume.get("h24")),
            market_cap_usd=safe_float(selected.get("marketCap")),
            fdv_usd=safe_float(selected.get("fdv")),
            change_5m_pct=safe_float(changes.get("m5")),
            change_1h_pct=safe_float(changes.get("h1")),
            change_6h_pct=safe_float(changes.get("h6")),
            change_24h_pct=safe_float(changes.get("h24")),
            buys_5m=safe_int(m5_tx.get("buys")),
            sells_5m=safe_int(m5_tx.get("sells")),
            buys_1h=safe_int(h1_tx.get("buys")),
            sells_1h=safe_int(h1_tx.get("sells")),
            age_minutes=age_minutes,
            pair_url=str(selected.get("url") or f"https://dexscreener.com/solana/{selected.get('pairAddress', '')}"),
            boosts_active=safe_int(boosts.get("active")),
            sources=tuple(sources),
        )

    async def fetch_many_snapshots(
        self, discoveries: Iterable[tuple[str, tuple[str, ...]]]
    ) -> list[MarketSnapshot]:
        results = await asyncio.gather(
            *(self.fetch_snapshot(address, sources) for address, sources in discoveries),
            return_exceptions=True,
        )
        snapshots: list[MarketSnapshot] = []
        for result in results:
            if isinstance(result, MarketSnapshot):
                snapshots.append(result)
            elif isinstance(result, Exception):
                LOGGER.debug("Snapshot fetch failed: %s", result)
        return snapshots

    async def fetch_rug_report(self, token_address: str, *, force: bool = False) -> RugReport:
        cached = self.rug_cache.get(token_address)
        ttl = self.settings.rugcheck_cache_minutes * 60
        if not force and cached and time.monotonic() - cached[0] < ttl:
            return cached[1]

        url = f"{self.RUG_BASE}/v1/tokens/{quote(token_address, safe='')}/report/summary?cacheOnly=false"
        try:
            payload = await self._get_json(url, attempts=2)
        except APIError as exc:
            report = RugReport(available=False, error=str(exc))
            self.rug_cache[token_address] = (time.monotonic(), report)
            return report

        if not isinstance(payload, dict) or payload.get("error"):
            report = RugReport(
                available=False,
                error=str(payload.get("error", "invalid RugCheck response")) if isinstance(payload, dict) else "invalid RugCheck response",
            )
            self.rug_cache[token_address] = (time.monotonic(), report)
            return report

        dangers: list[str] = []
        warnings: list[str] = []
        for risk in payload.get("risks") or []:
            if not isinstance(risk, dict):
                continue
            name = str(risk.get("name") or risk.get("description") or "unnamed risk")
            level = str(risk.get("level") or "").lower()
            normalized_name = name.lower()
            if level in {"danger", "critical"} or "honeypot" in normalized_name:
                dangers.append(name)
            else:
                warnings.append(name)

        normalized = payload.get("score_normalised")
        report = RugReport(
            available=True,
            normalized_score=safe_int(normalized) if normalized is not None else None,
            raw_score=safe_int(payload.get("score")) if payload.get("score") is not None else None,
            lp_locked_pct=safe_float(payload.get("lpLockedPct")) if payload.get("lpLockedPct") is not None else None,
            danger_names=tuple(dangers),
            warning_names=tuple(warnings),
        )
        self.rug_cache[token_address] = (time.monotonic(), report)
        return report


# ========================================================================
# Combined from: jarvis/strategy.py
# ========================================================================
class Strategy:
    """Deterministic, explainable token filter and 0-100 scoring model."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def market_blocks(self, snapshot: MarketSnapshot) -> list[str]:
        s = self.settings
        cap = snapshot.market_cap_usd or snapshot.fdv_usd
        blocks: list[str] = []
        if snapshot.price_usd <= 0:
            blocks.append("invalid price")
        if snapshot.liquidity_usd < s.min_liquidity_usd:
            blocks.append(f"liquidity below ${s.min_liquidity_usd:,.0f}")
        if snapshot.volume_24h_usd < s.min_volume_24h_usd:
            blocks.append(f"24h volume below ${s.min_volume_24h_usd:,.0f}")
        if cap <= 0:
            blocks.append("market cap unavailable")
        elif not s.min_market_cap_usd <= cap <= s.max_market_cap_usd:
            blocks.append("market cap outside configured range")
        if not s.min_pair_age_minutes <= snapshot.age_minutes <= s.max_pair_age_minutes:
            blocks.append("pair age outside configured range")
        if not s.min_5m_change_pct <= snapshot.change_5m_pct <= s.max_5m_change_pct:
            blocks.append("5m momentum outside safe entry range")
        if not s.min_1h_change_pct <= snapshot.change_1h_pct <= s.max_1h_change_pct:
            blocks.append("1h trend outside safe entry range")
        if snapshot.buys_5m < s.min_5m_buys:
            blocks.append(f"fewer than {s.min_5m_buys} buys in 5m")
        if snapshot.buy_ratio_5m < s.min_5m_buy_ratio:
            blocks.append("5m buy ratio too weak")
        if snapshot.liquidity_to_mcap < s.min_liquidity_to_mcap:
            blocks.append("liquidity is too small relative to market cap")
        if snapshot.volume_to_liquidity > s.max_volume_to_liquidity:
            blocks.append("volume/liquidity ratio suggests possible artificial activity")
        return blocks

    def evaluate(self, snapshot: MarketSnapshot, rug: RugReport) -> Evaluation:
        blocks = self.market_blocks(snapshot)
        s = self.settings

        if not rug.available and s.rugcheck_required:
            blocks.append("RugCheck unavailable")
        if rug.has_danger:
            blocks.append("RugCheck danger: " + ", ".join(rug.danger_names[:2]))
        if (
            rug.normalized_score is not None
            and rug.normalized_score > s.rugcheck_max_normalized
        ):
            blocks.append(
                f"RugCheck risk {rug.normalized_score}/100 exceeds {s.rugcheck_max_normalized}"
            )

        components = {
            "liquidity": self._liquidity_score(snapshot),
            "volume": self._volume_score(snapshot),
            "momentum_5m": self._momentum_score(snapshot),
            "trend_1h": self._trend_score(snapshot),
            "buy_pressure": self._buy_pressure_score(snapshot),
            "liquidity_quality": self._liquidity_quality_score(snapshot),
            "age": self._age_score(snapshot),
            "rug_safety": self._rug_score(rug),
        }
        score = max(0, min(100, sum(components.values())))
        reasons = self._reasons(snapshot, rug, components)
        passed = not blocks and score >= s.min_score
        if not blocks and score < s.min_score:
            blocks.append(f"score {score}/100 below {s.min_score}")
        return Evaluation(
            score=score,
            passed=passed,
            blocks=blocks,
            reasons=reasons,
            components=components,
        )

    @staticmethod
    def _liquidity_score(snapshot: MarketSnapshot) -> int:
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
    def _volume_score(snapshot: MarketSnapshot) -> int:
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
    def _momentum_score(snapshot: MarketSnapshot) -> int:
        value = snapshot.change_5m_pct
        if 2.0 <= value <= 8.0:
            return 15
        if 1.0 <= value <= 15.0:
            return 12
        if 0.0 <= value <= 25.0:
            return 8
        return 0

    @staticmethod
    def _trend_score(snapshot: MarketSnapshot) -> int:
        value = snapshot.change_1h_pct
        if 5.0 <= value <= 35.0:
            return 10
        if 0.0 <= value <= 60.0:
            return 8
        if -5.0 <= value <= 100.0:
            return 4
        return 0

    @staticmethod
    def _buy_pressure_score(snapshot: MarketSnapshot) -> int:
        ratio = snapshot.buy_ratio_5m
        if ratio >= 0.72:
            base = 12
        elif ratio >= 0.65:
            base = 10
        elif ratio >= 0.58:
            base = 7
        else:
            base = 0
        activity = 3 if snapshot.buys_5m >= 25 else 2 if snapshot.buys_5m >= 15 else 1
        return min(15, base + activity)

    @staticmethod
    def _liquidity_quality_score(snapshot: MarketSnapshot) -> int:
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
    def _age_score(snapshot: MarketSnapshot) -> int:
        age = snapshot.age_minutes
        if 15 <= age <= 1_440:
            return 5
        if 10 <= age <= 10_080:
            return 4
        if age <= 43_200:
            return 2
        return 0

    @staticmethod
    def _rug_score(rug: RugReport) -> int:
        if not rug.available or rug.has_danger:
            return 0
        value = rug.normalized_score
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
        if len(rug.warning_names) >= 4:
            score = max(0, score - 2)
        return score

    @staticmethod
    def _reasons(
        snapshot: MarketSnapshot,
        rug: RugReport,
        components: dict[str, int],
    ) -> list[str]:
        ranked = sorted(components.items(), key=lambda item: item[1], reverse=True)
        reasons = [name.replace("_", " ") for name, points in ranked[:3] if points > 0]
        reasons.append(f"5m buy ratio {snapshot.buy_ratio_5m:.0%}")
        if rug.normalized_score is not None:
            reasons.append(f"RugCheck risk {rug.normalized_score}/100")
        return reasons[:5]


# ========================================================================
# Combined from: jarvis/broker.py
# ========================================================================
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo



class PaperBroker:
    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store
        self.state = store.load()
        self.tz = ZoneInfo(settings.timezone_name)

    def sydney_day(self) -> str:
        return utc_now().astimezone(self.tz).date().isoformat()

    async def ensure_day(self) -> bool:
        today = self.sydney_day()
        if self.state.get("day") == today:
            return False
        self.state["day"] = today
        self.state["day_start_equity"] = self.equity()
        self.state["day_entries"] = 0
        self.state["day_wins"] = 0
        self.state["day_losses"] = 0
        self.state["loss_streak"] = 0
        self.state["safety_pause_reason"] = None
        await self.store.save(self.state)
        return True

    @property
    def cash(self) -> float:
        return float(self.state.get("cash", 0.0))

    @property
    def positions(self) -> dict[str, dict[str, Any]]:
        return self.state.setdefault("positions", {})

    def estimated_exit_value(self, position: dict[str, Any]) -> float:
        price = float(position.get("last_market_price") or position.get("entry_market_price") or 0)
        quantity = float(position.get("quantity", 0))
        slip = self.settings.paper_slippage_bps / 10_000
        fee = self.settings.paper_fee_bps / 10_000
        gross = quantity * price * (1 - slip)
        return max(0.0, gross * (1 - fee))

    def equity(self) -> float:
        return self.cash + sum(self.estimated_exit_value(position) for position in self.positions.values())

    def open_pnl(self) -> float:
        total = 0.0
        for position in self.positions.values():
            total += self.estimated_exit_value(position) - float(position.get("entry_total_cost", 0))
        return total

    def total_realized_pnl(self) -> float:
        return sum(float(trade.get("pnl_usd", 0)) for trade in self.state.get("trades", []))

    def today_pnl(self) -> float:
        return self.equity() - float(self.state.get("day_start_equity", self.equity()))

    def daily_loss_pct(self) -> float:
        start = float(self.state.get("day_start_equity", 0))
        return max(0.0, (start - self.equity()) / start * 100) if start > 0 else 0.0

    def entry_guard(self, token_address: str | None = None) -> str | None:
        if self.state.get("manual_paused"):
            return "manually paused"
        if self.state.get("safety_pause_reason"):
            return str(self.state["safety_pause_reason"])
        if len(self.positions) >= self.settings.max_open_positions:
            return "maximum open positions reached"
        if int(self.state.get("day_entries", 0)) >= self.settings.max_daily_entries:
            return "daily entry limit reached"
        if int(self.state.get("loss_streak", 0)) >= self.settings.max_losing_streak:
            return "maximum losing streak reached"
        if self.daily_loss_pct() >= self.settings.max_daily_loss_pct:
            return "daily loss limit reached"
        target = self.settings.daily_profit_target_usd
        if target > 0 and self.today_pnl() >= target:
            return "daily profit target reached"
        if token_address:
            if token_address in self.positions:
                return "token already has an open position"
            until = parse_iso(self.state.setdefault("cooldown_until", {}).get(token_address))
            if until and utc_now() < until:
                return "token is in cooldown"
        return None

    async def refresh_safety_pause(self) -> str | None:
        reason = None
        if self.daily_loss_pct() >= self.settings.max_daily_loss_pct:
            reason = "daily loss limit reached"
        elif int(self.state.get("loss_streak", 0)) >= self.settings.max_losing_streak:
            reason = "maximum losing streak reached"
        elif (
            self.settings.daily_profit_target_usd > 0
            and self.today_pnl() >= self.settings.daily_profit_target_usd
        ):
            reason = "daily profit target reached"
        if reason and self.state.get("safety_pause_reason") != reason:
            self.state["safety_pause_reason"] = reason
            await self.store.save(self.state)
        return reason

    async def open_position(
        self,
        snapshot: MarketSnapshot,
        evaluation: Evaluation,
    ) -> dict[str, Any] | None:
        await self.ensure_day()
        if self.entry_guard(snapshot.token_address):
            return None

        equity = self.equity()
        risk_usd = equity * self.settings.risk_per_trade_pct / 100
        stop_fraction = self.settings.stop_loss_pct / 100
        risk_notional = risk_usd / stop_fraction
        position_cap = equity * self.settings.max_position_pct / 100
        fee_fraction = self.settings.paper_fee_bps / 10_000
        cash_cap = self.cash / (1 + fee_fraction)
        notional = min(risk_notional, position_cap, cash_cap)
        if notional < 5:
            return None

        slip_fraction = self.settings.paper_slippage_bps / 10_000
        fill_price = snapshot.price_usd * (1 + slip_fraction)
        entry_fee = notional * fee_fraction
        quantity = notional / fill_price
        entry_total = notional + entry_fee
        now = iso_now()
        position = {
            "token_address": snapshot.token_address,
            "symbol": snapshot.symbol,
            "name": snapshot.name,
            "pair_address": snapshot.pair_address,
            "pair_url": snapshot.pair_url,
            "dex_id": snapshot.dex_id,
            "opened_at": now,
            "entry_market_price": snapshot.price_usd,
            "entry_fill_price": fill_price,
            "last_market_price": snapshot.price_usd,
            "highest_market_price": snapshot.price_usd,
            "quantity": quantity,
            "entry_notional": notional,
            "entry_fee": entry_fee,
            "entry_total_cost": entry_total,
            "entry_liquidity_usd": snapshot.liquidity_usd,
            "last_liquidity_usd": snapshot.liquidity_usd,
            "score": evaluation.score,
            "entry_reasons": evaluation.reasons,
            "updates": 0,
        }
        self.state["cash"] = max(0.0, self.cash - entry_total)
        self.positions[snapshot.token_address] = position
        self.state["day_entries"] = int(self.state.get("day_entries", 0)) + 1
        await self.store.save(self.state)
        return position

    def update_mark(self, snapshot: MarketSnapshot) -> None:
        position = self.positions.get(snapshot.token_address)
        if not position:
            return
        position["last_market_price"] = snapshot.price_usd
        position["last_liquidity_usd"] = snapshot.liquidity_usd
        position["highest_market_price"] = max(
            float(position.get("highest_market_price", 0)), snapshot.price_usd
        )
        position["updates"] = int(position.get("updates", 0)) + 1

    def exit_decision(self, snapshot: MarketSnapshot) -> ExitDecision:
        position = self.positions.get(snapshot.token_address)
        if not position:
            return ExitDecision(False)
        self.update_mark(snapshot)
        entry = float(position["entry_fill_price"])
        pnl_pct = (snapshot.price_usd / entry - 1) * 100 if entry > 0 else 0.0
        highest = float(position.get("highest_market_price", snapshot.price_usd))
        high_pct = (highest / entry - 1) * 100 if entry > 0 else 0.0
        pullback_pct = (highest - snapshot.price_usd) / highest * 100 if highest > 0 else 0.0

        entry_liquidity = float(position.get("entry_liquidity_usd", 0))
        if entry_liquidity > 0 and snapshot.liquidity_usd <= entry_liquidity * self.settings.liquidity_exit_fraction:
            return ExitDecision(True, "LIQUIDITY_DROP", pnl_pct)
        if pnl_pct <= -self.settings.stop_loss_pct:
            return ExitDecision(True, "STOP_LOSS", pnl_pct)
        if pnl_pct >= self.settings.take_profit_pct:
            return ExitDecision(True, "TAKE_PROFIT", pnl_pct)
        if (
            high_pct >= self.settings.trailing_activation_pct
            and pullback_pct >= self.settings.trailing_distance_pct
        ):
            return ExitDecision(True, "TRAILING_STOP", pnl_pct)
        if (
            snapshot.change_5m_pct <= self.settings.momentum_exit_5m_pct
            and snapshot.buy_ratio_5m <= self.settings.momentum_exit_buy_ratio
        ):
            return ExitDecision(True, "MOMENTUM_REVERSAL", pnl_pct)
        opened = parse_iso(str(position.get("opened_at", "")))
        if opened and utc_now() - opened >= timedelta(minutes=self.settings.max_hold_minutes):
            return ExitDecision(True, "MAX_HOLD_TIME", pnl_pct)
        return ExitDecision(False, pnl_pct=pnl_pct)

    async def close_position(
        self,
        snapshot: MarketSnapshot,
        reason: str,
    ) -> dict[str, Any] | None:
        position = self.positions.get(snapshot.token_address)
        if not position:
            return None
        slip_fraction = self.settings.paper_slippage_bps / 10_000
        fee_fraction = self.settings.paper_fee_bps / 10_000
        exit_fill = snapshot.price_usd * (1 - slip_fraction)
        gross = float(position["quantity"]) * exit_fill
        exit_fee = gross * fee_fraction
        net = gross - exit_fee
        entry_total = float(position["entry_total_cost"])
        pnl_usd = net - entry_total
        pnl_pct = pnl_usd / entry_total * 100 if entry_total > 0 else 0.0
        now = iso_now()
        trade = {
            "token_address": snapshot.token_address,
            "symbol": position["symbol"],
            "pair_address": position["pair_address"],
            "pair_url": position["pair_url"],
            "opened_at": position["opened_at"],
            "closed_at": now,
            "entry_fill_price": position["entry_fill_price"],
            "exit_fill_price": exit_fill,
            "quantity": position["quantity"],
            "entry_total_cost": entry_total,
            "exit_gross": gross,
            "exit_fee": exit_fee,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "exit_reason": reason,
            "entry_score": position["score"],
        }
        self.state["cash"] = self.cash + net
        self.state.setdefault("trades", []).append(trade)
        self.positions.pop(snapshot.token_address, None)
        self.state.setdefault("cooldown_until", {})[snapshot.token_address] = (
            utc_now() + timedelta(minutes=self.settings.trade_cooldown_minutes)
        ).isoformat()
        if pnl_usd > 0:
            self.state["day_wins"] = int(self.state.get("day_wins", 0)) + 1
            self.state["loss_streak"] = 0
        else:
            self.state["day_losses"] = int(self.state.get("day_losses", 0)) + 1
            self.state["loss_streak"] = int(self.state.get("loss_streak", 0)) + 1
        await self.refresh_safety_pause()
        await self.store.save(self.state)
        return trade

    async def set_manual_pause(self, paused: bool) -> None:
        self.state["manual_paused"] = paused
        await self.store.save(self.state)

    async def reset_paper_account(self) -> None:
        self.state = new_state(self.settings, day=self.sydney_day())
        await self.store.save(self.state)

    async def record_scan(self, stats: dict[str, Any]) -> None:
        self.state["last_scan"] = stats
        await self.store.save(self.state)

    async def persist_marks(self) -> None:
        await self.store.save(self.state)


# ========================================================================
# Combined from: jarvis/formatters.py
# ========================================================================
from html import escape
from typing import Any, Iterable



def status_text(broker: PaperBroker, settings: Settings, *, running: bool = True) -> str:
    state = broker.state
    last_scan = state.get("last_scan", {})
    trades_today = int(state.get("day_wins", 0)) + int(state.get("day_losses", 0))
    win_rate = int(state.get("day_wins", 0)) / trades_today * 100 if trades_today else 0.0
    guard = broker.entry_guard()
    status = "🟢 RESEARCHING" if running and not guard else f"🟡 {escape((guard or 'STOPPED').upper())}"
    return (
        "🤖 <b>JARVIS RESEARCH STATUS</b>\n\n"
        f"Cash: <b>{money(broker.cash)}</b>\n"
        f"Equity: <b>{money(broker.equity())}</b>\n"
        f"Total P/L: <b>{money(broker.total_realized_pnl() + broker.open_pnl())}</b>\n"
        f"Open P/L: <b>{money(broker.open_pnl())}</b>\n\n"
        f"Today's wins: <b>{state.get('day_wins', 0)}</b>\n"
        f"Today's losses: <b>{state.get('day_losses', 0)}</b>\n"
        f"Win rate: <b>{win_rate:.1f}%</b>\n\n"
        f"Open positions: <b>{len(broker.positions)}/{settings.max_open_positions}</b>\n"
        f"Entries: <b>{state.get('day_entries', 0)}/{settings.max_daily_entries}</b>\n"
        f"Loss streak: <b>{state.get('loss_streak', 0)}/{settings.max_losing_streak}</b>\n"
        f"Daily loss: <b>{broker.daily_loss_pct():.2f}%/{settings.max_daily_loss_pct:.2f}%</b>\n\n"
        f"Risk/trade: <b>{settings.risk_per_trade_pct:.2f}%</b>\n"
        f"Stop: <b>{settings.stop_loss_pct:.1f}%</b>\n"
        f"Target: <b>+{settings.take_profit_pct:.1f}%</b>\n"
        f"Minimum score: <b>{settings.min_score}/100</b>\n\n"
        f"Last scan: <b>{last_scan.get('discovered', 0)} discovered · "
        f"{last_scan.get('snapshots', 0)} priced · "
        f"{last_scan.get('qualified', 0)} qualified</b>\n\n"
        f"Status: {status}\n"
        "Mode: 🧪 <b>PAPER TRADING</b>"
    )


def entry_text(
    position: dict[str, Any],
    snapshot: MarketSnapshot,
    evaluation: Evaluation,
    rug: RugReport,
) -> str:
    rug_text = (
        f"{rug.normalized_score}/100 risk"
        if rug.available and rug.normalized_score is not None
        else "available" if rug.available else "unavailable"
    )
    reasons = ", ".join(escape(reason) for reason in evaluation.reasons[:4])
    return (
        "🧪 <b>PAPER ENTRY</b>\n\n"
        f"Token: <b>{escape(snapshot.symbol)}</b>\n"
        f"Score: <b>{evaluation.score}/100</b>\n"
        f"Position: <b>{money(float(position['entry_total_cost']))}</b>\n"
        f"Paper fill: <b>{price(float(position['entry_fill_price']))}</b>\n"
        f"Liquidity: <b>{money(snapshot.liquidity_usd)}</b>\n"
        f"24h volume: <b>{money(snapshot.volume_24h_usd)}</b>\n"
        f"5m / 1h: <b>{pct(snapshot.change_5m_pct)} / {pct(snapshot.change_1h_pct)}</b>\n"
        f"5m buy ratio: <b>{snapshot.buy_ratio_5m:.0%}</b>\n"
        f"RugCheck: <b>{escape(rug_text)}</b>\n"
        f"Why: {reasons}\n\n"
        f"<a href=\"{escape(snapshot.pair_url, quote=True)}\">Open DexScreener</a>\n"
        f"CA: <code>{escape(snapshot.token_address)}</code>"
    )


def exit_text(trade: dict[str, Any]) -> str:
    won = float(trade.get("pnl_usd", 0)) > 0
    icon = "✅" if won else "🛑"
    url = str(trade.get("pair_url") or "")
    link = f"\n<a href=\"{escape(url, quote=True)}\">Open DexScreener</a>" if url else ""
    return (
        f"{icon} <b>PAPER EXIT — {escape(str(trade.get('exit_reason', 'EXIT')))}</b>\n\n"
        f"Token: <b>{escape(str(trade.get('symbol', 'UNKNOWN')))}</b>\n"
        f"P/L: <b>{money(float(trade.get('pnl_usd', 0)))} "
        f"({pct(float(trade.get('pnl_pct', 0)))})</b>\n"
        f"Entry: <b>{price(float(trade.get('entry_fill_price', 0)))}</b>\n"
        f"Exit: <b>{price(float(trade.get('exit_fill_price', 0)))}</b>\n"
        f"Entry score: <b>{trade.get('entry_score', 0)}/100</b>"
        f"{link}"
    )


def positions_text(broker: PaperBroker) -> str:
    if not broker.positions:
        return "📭 <b>No open paper positions.</b>"
    lines = ["📊 <b>OPEN PAPER POSITIONS</b>"]
    for position in broker.positions.values():
        current_value = broker.estimated_exit_value(position)
        cost = float(position.get("entry_total_cost", 0))
        pnl = current_value - cost
        pnl_percent = pnl / cost * 100 if cost else 0.0
        lines.extend(
            [
                "",
                f"<b>{escape(str(position.get('symbol', 'UNKNOWN')))}</b> — {money(current_value)}",
                f"P/L: <b>{money(pnl)} ({pct(pnl_percent)})</b>",
                f"Score: {position.get('score', 0)}/100",
                f"CA: <code>{escape(short_address(str(position.get('token_address', ''))))}</code>",
            ]
        )
    return "\n".join(lines)


def trades_text(trades: Iterable[dict[str, Any]], limit: int = 10) -> str:
    selected = list(trades)[-limit:]
    if not selected:
        return "📭 <b>No completed paper trades yet.</b>"
    lines = [f"📚 <b>LAST {len(selected)} PAPER TRADES</b>"]
    for trade in reversed(selected):
        pnl_value = float(trade.get("pnl_usd", 0))
        icon = "✅" if pnl_value > 0 else "❌"
        lines.append(
            f"{icon} <b>{escape(str(trade.get('symbol', 'UNKNOWN')))}</b> "
            f"{money(pnl_value)} ({pct(float(trade.get('pnl_pct', 0)))}) — "
            f"{escape(str(trade.get('exit_reason', 'EXIT')))}"
        )
    return "\n".join(lines)


def candidate_text(
    candidates: Iterable[tuple[MarketSnapshot, Evaluation, RugReport]],
    limit: int = 5,
) -> str:
    selected = list(candidates)[:limit]
    if not selected:
        return "🔎 <b>No qualifying candidates in the latest scan.</b>"
    lines = ["🔎 <b>TOP LATEST CANDIDATES</b>"]
    for snapshot, evaluation, rug in selected:
        rug_value = str(rug.normalized_score) if rug.normalized_score is not None else "—"
        mark = "✅" if evaluation.passed else "⚪️"
        lines.extend(
            [
                "",
                f"{mark} <b>{escape(snapshot.symbol)}</b> — {evaluation.score}/100",
                f"5m {pct(snapshot.change_5m_pct)} · buys {snapshot.buy_ratio_5m:.0%} · rug {rug_value}",
                f"Liquidity {money(snapshot.liquidity_usd)} · volume {money(snapshot.volume_24h_usd)}",
                f"<code>{escape(snapshot.token_address)}</code>",
            ]
        )
    return "\n".join(lines)


def analysis_text(
    snapshot: MarketSnapshot,
    evaluation: Evaluation,
    rug: RugReport,
) -> str:
    verdict = "✅ QUALIFIES" if evaluation.passed else "⛔️ DOES NOT QUALIFY"
    blocks = ", ".join(escape(item) for item in evaluation.blocks[:5]) or "none"
    rug_value = f"{rug.normalized_score}/100" if rug.normalized_score is not None else "unavailable"
    return (
        f"🧠 <b>JARVIS ANALYSIS — {verdict}</b>\n\n"
        f"Token: <b>{escape(snapshot.symbol)}</b>\n"
        f"Score: <b>{evaluation.score}/100</b>\n"
        f"Price: <b>{price(snapshot.price_usd)}</b>\n"
        f"Liquidity: <b>{money(snapshot.liquidity_usd)}</b>\n"
        f"24h volume: <b>{money(snapshot.volume_24h_usd)}</b>\n"
        f"5m / 1h: <b>{pct(snapshot.change_5m_pct)} / {pct(snapshot.change_1h_pct)}</b>\n"
        f"Buy ratio: <b>{snapshot.buy_ratio_5m:.0%}</b>\n"
        f"RugCheck risk: <b>{escape(rug_value)}</b>\n"
        f"Blocks: {blocks}\n\n"
        f"<a href=\"{escape(snapshot.pair_url, quote=True)}\">Open DexScreener</a>\n"
        f"CA: <code>{escape(snapshot.token_address)}</code>"
    )


# ========================================================================
# Combined from: jarvis/engine.py
# ========================================================================
import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any


LOGGER = logging.getLogger(__name__)
Notifier = Callable[[str], Awaitable[None]]


class ResearchEngine:
    def __init__(
        self,
        settings: Settings,
        market: MarketDataClient,
        strategy: Strategy,
        broker: PaperBroker,
    ):
        self.settings = settings
        self.market = market
        self.strategy = strategy
        self.broker = broker
        self.notifier: Notifier | None = None
        self.running = False
        self.started_at = time.monotonic()
        self.scan_lock = asyncio.Lock()
        self.candidates: list[tuple[MarketSnapshot, Evaluation, RugReport]] = []
        self.last_scan_at: str | None = None
        self.last_loop_at: str | None = None
        self.last_error: str | None = None

    def set_notifier(self, notifier: Notifier) -> None:
        self.notifier = notifier

    async def notify(self, message: str) -> None:
        if not self.notifier:
            return
        try:
            await self.notifier(message)
        except Exception as exc:  # Telegram errors must never stop risk management.
            LOGGER.warning("Notification failed: %s", exc)

    async def run(self) -> None:
        self.running = True
        await self.market.start()
        next_scan = 0.0
        next_update = 0.0
        next_status = time.monotonic() + self.settings.status_interval_seconds
        while self.running:
            self.last_loop_at = iso_now()
            now = time.monotonic()
            try:
                changed = await self.broker.ensure_day()
                if changed:
                    await self.notify("🌅 <b>New Sydney trading day.</b> Daily paper limits have reset.")
                if now >= next_update:
                    await self.update_positions_once()
                    next_update = time.monotonic() + self.settings.price_update_seconds
                if now >= next_scan:
                    await self.scan_once()
                    next_scan = time.monotonic() + self.settings.scan_interval_seconds
                if now >= next_status:
                    await self.notify(status_text(self.broker, self.settings, running=True))
                    next_status = time.monotonic() + self.settings.status_interval_seconds
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                LOGGER.exception("Research loop error")
                await asyncio.sleep(3.0)

    async def stop(self) -> None:
        self.running = False
        await self.broker.persist_marks()
        await self.market.close()

    async def scan_once(self) -> dict[str, Any]:
        if self.scan_lock.locked():
            return dict(self.broker.state.get("last_scan", {}))
        async with self.scan_lock:
            discoveries = await self.market.discover_token_addresses()
            snapshots = await self.market.fetch_many_snapshots(discoveries)
            block_counts: Counter[str] = Counter()
            market_passed: list[MarketSnapshot] = []
            for snapshot in snapshots:
                blocks = self.strategy.market_blocks(snapshot)
                if blocks:
                    block_counts.update(blocks)
                else:
                    market_passed.append(snapshot)
            rug_reports = await asyncio.gather(
                *(self.market.fetch_rug_report(snapshot.token_address) for snapshot in market_passed),
                return_exceptions=True,
            )
            evaluated: list[tuple[MarketSnapshot, Evaluation, RugReport]] = []
            for snapshot, result in zip(market_passed, rug_reports, strict=True):
                rug = (
                    result
                    if isinstance(result, RugReport)
                    else RugReport(available=False, error=str(result))
                )
                evaluation = self.strategy.evaluate(snapshot, rug)
                if evaluation.blocks:
                    block_counts.update(evaluation.blocks)
                evaluated.append((snapshot, evaluation, rug))
            evaluated.sort(
                key=lambda item: (item[1].passed, item[1].score, item[0].liquidity_usd),
                reverse=True,
            )
            self.candidates = evaluated[:10]
            qualified = [item for item in evaluated if item[1].passed]
            stats = {
                "at": iso_now(),
                "discovered": len(discoveries),
                "snapshots": len(snapshots),
                "market_passed": len(market_passed),
                "qualified": len(qualified),
                "top_score": evaluated[0][1].score if evaluated else 0,
                "top_blocks": block_counts.most_common(5),
            }
            self.last_scan_at = stats["at"]
            await self.broker.record_scan(stats)

            for snapshot, evaluation, rug in qualified:
                guard = self.broker.entry_guard(snapshot.token_address)
                if guard:
                    continue
                position = await self.broker.open_position(snapshot, evaluation)
                if position:
                    await self.notify(entry_text(position, snapshot, evaluation, rug))
            await self.broker.refresh_safety_pause()
            return stats

    async def update_positions_once(self) -> None:
        if not self.broker.positions:
            return
        requested = [dict(position) for position in self.broker.positions.values()]
        results = await asyncio.gather(
            *(
                self.market.fetch_snapshot(
                    str(position["token_address"]),
                    ("open_position",),
                    preferred_pair=str(position.get("pair_address") or ""),
                )
                for position in requested
            ),
            return_exceptions=True,
        )
        changed_marks = False
        for old_position, result in zip(requested, results, strict=True):
            address = str(old_position["token_address"])
            if not isinstance(result, MarketSnapshot):
                current = self.broker.positions.get(address)
                if current:
                    current["data_failures"] = int(current.get("data_failures", 0)) + 1
                    changed_marks = True
                continue
            current = self.broker.positions.get(address)
            if current:
                current["data_failures"] = 0
            decision = self.broker.exit_decision(result)
            changed_marks = True

            # Re-use the cached safety report. When its TTL expires this becomes a
            # fresh risk check and can catch a delayed danger flag.
            rug = await self.market.fetch_rug_report(address)
            if rug.has_danger:
                decision.should_exit = True
                decision.reason = "RUGCHECK_DANGER"

            if decision.should_exit:
                trade = await self.broker.close_position(result, decision.reason)
                if trade:
                    await self.notify(exit_text(trade))
        if changed_marks:
            await self.broker.persist_marks()
        await self.broker.refresh_safety_pause()

    async def analyze_token(
        self, token_address: str
    ) -> tuple[MarketSnapshot, Evaluation, RugReport] | None:
        token_address = token_address.strip()
        if not token_address:
            return None
        snapshot = await self.market.fetch_snapshot(token_address, ("manual_scan",))
        if not snapshot:
            return None
        rug = await self.market.fetch_rug_report(token_address, force=True)
        evaluation = self.strategy.evaluate(snapshot, rug)
        return snapshot, evaluation, rug

    def health(self) -> dict[str, Any]:
        return {
            "ok": self.running,
            "mode": "paper",
            "uptime_seconds": int(time.monotonic() - self.started_at),
            "last_loop_at": self.last_loop_at,
            "last_scan_at": self.last_scan_at,
            "open_positions": len(self.broker.positions),
            "equity": round(self.broker.equity(), 2),
            "last_error": self.last_error,
            "market_last_error": self.market.last_error,
            "api_requests": self.market.request_count,
        }


# ========================================================================
# Combined from: jarvis/health.py
# ========================================================================
from aiohttp import web



class HealthServer:
    def __init__(self, engine: ResearchEngine, port: int):
        self.engine = engine
        self.port = port
        self.runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/health", self.health)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await site.start()

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def index(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"service": "Jarvis V2", "mode": "paper", "health": "/health"}
        )

    async def health(self, request: web.Request) -> web.Response:
        payload = self.engine.health()
        return web.json_response(payload, status=200 if payload["ok"] else 503)


# ========================================================================
# Combined from: jarvis/telegram_ui.py
# ========================================================================
import logging
import re
from html import escape

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes


LOGGER = logging.getLogger(__name__)
SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class TelegramInterface:
    def __init__(self, settings: Settings, engine: ResearchEngine, broker: PaperBroker):
        self.settings = settings
        self.engine = engine
        self.broker = broker

    def build_application(self, *, post_init, post_shutdown) -> Application:
        application = (
            ApplicationBuilder()
            .token(self.settings.telegram_bot_token)
            .post_init(post_init)
            .post_shutdown(post_shutdown)
            .build()
        )
        handlers = {
            "start": self.start,
            "status": self.status,
            "today": self.today,
            "positions": self.positions,
            "trades": self.trades,
            "pause": self.pause,
            "resume": self.resume,
            "paper": self.paper,
            "candidates": self.candidates,
            "scan": self.scan,
            "settings": self.show_settings,
            "health": self.health,
            "resetpaper": self.resetpaper,
            "help": self.help_command,
        }
        for command, callback in handlers.items():
            application.add_handler(CommandHandler(command, callback))
        application.add_error_handler(self.error_handler)
        return application

    async def configure_commands(self, application: Application) -> None:
        commands = [
            BotCommand("status", "Account and risk status"),
            BotCommand("today", "Today's paper result"),
            BotCommand("positions", "Open paper positions"),
            BotCommand("trades", "Completed paper trades"),
            BotCommand("candidates", "Latest top candidates"),
            BotCommand("scan", "Run market scan or analyse an address"),
            BotCommand("pause", "Pause new paper entries"),
            BotCommand("resume", "Resume new paper entries"),
            BotCommand("settings", "Show strategy settings"),
            BotCommand("help", "Show all commands"),
        ]
        await application.bot.set_my_commands(commands)

    def authorized(self, update: Update) -> bool:
        chat = update.effective_chat
        return bool(chat and chat.id in self.settings.allowed_chat_ids)

    async def reject(self, update: Update) -> None:
        LOGGER.warning("Rejected Telegram chat id %s", getattr(update.effective_chat, "id", None))
        if update.effective_message:
            await update.effective_message.reply_text("This chat is not authorised for Jarvis.")

    async def reply(self, update: Update, text: str) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await self.reply(
            update,
            "🤖 <b>JARVIS V2 IS ONLINE</b>\n\n"
            "Live Solana market research with simulated money only.\n"
            "No wallet is connected and this release cannot execute a real trade.\n\n"
            "Use /status to see the account or /help for every command.",
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await self.reply(update, status_text(self.broker, self.settings, running=self.engine.running))

    async def today(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        state = self.broker.state
        closed = int(state.get("day_wins", 0)) + int(state.get("day_losses", 0))
        win_rate = int(state.get("day_wins", 0)) / closed * 100 if closed else 0.0
        await self.reply(
            update,
            "📅 <b>TODAY — SYDNEY TIME</b>\n\n"
            f"Equity change: <b>{money(self.broker.today_pnl())}</b>\n"
            f"Entries: <b>{state.get('day_entries', 0)}/{self.settings.max_daily_entries}</b>\n"
            f"Wins / losses: <b>{state.get('day_wins', 0)} / {state.get('day_losses', 0)}</b>\n"
            f"Win rate: <b>{win_rate:.1f}%</b>\n"
            f"Daily loss: <b>{self.broker.daily_loss_pct():.2f}%</b>",
        )

    async def positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await self.reply(update, positions_text(self.broker))

    async def trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        limit = 10
        if context.args:
            try:
                limit = max(1, min(25, int(context.args[0])))
            except ValueError:
                pass
        await self.reply(update, trades_text(self.broker.state.get("trades", []), limit))

    async def pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await self.broker.set_manual_pause(True)
        await self.reply(
            update,
            "⏸ <b>NEW PAPER ENTRIES PAUSED.</b> Existing positions are still being monitored.",
        )

    async def resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await self.broker.set_manual_pause(False)
        safety = await self.broker.refresh_safety_pause()
        if safety:
            await self.reply(update, f"⚠️ Cannot resume entries: <b>{escape(safety)}</b>.")
        else:
            await self.reply(update, "▶️ <b>NEW PAPER ENTRIES RESUMED.</b>")

    async def paper(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.status(update, context)

    async def candidates(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await self.reply(update, candidate_text(self.engine.candidates))

    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        if not context.args:
            if self.engine.scan_lock.locked():
                await self.reply(update, "🔎 <b>A market scan is already running.</b>")
                return
            await self.reply(update, "🔎 <b>JARVIS MARKET SCAN STARTED</b>")
            stats = await self.engine.scan_once()
            top_blocks = stats.get("top_blocks", [])
            blocks_text = (
                "\n".join(
                    f"• {escape(str(reason))}: <b>{count}</b>"
                    for reason, count in top_blocks
                )
                if top_blocks
                else "None"
            )
            await self.reply(
                update,
                "✅ <b>MARKET SCAN FINISHED</b>\n\n"
                f"Discovered: <b>{stats.get('discovered', 0)}</b>\n"
                f"Valid prices: <b>{stats.get('snapshots', 0)}</b>\n"
                f"Passed market filters: <b>{stats.get('market_passed', 0)}</b>\n"
                f"Qualified: <b>{stats.get('qualified', 0)}</b>\n"
                f"Top score: <b>{stats.get('top_score', 0)}/100</b>\n\n"
                f"<b>Main rejection reasons</b>\n{blocks_text}",
            )
            return
        if not SOLANA_ADDRESS.fullmatch(context.args[0].strip()):
            await self.reply(update, "Usage: <code>/scan SOLANA_TOKEN_ADDRESS</code>")
            return
        address = context.args[0].strip()
        await self.reply(update, "🔎 Researching that token with DexScreener and RugCheck…")
        result = await self.engine.analyze_token(address)
        if not result:
            await self.reply(update, "No valid Solana trading pair was found for that address.")
            return
        snapshot, evaluation, rug = result
        await self.reply(update, analysis_text(snapshot, evaluation, rug))

    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        s = self.settings
        await self.reply(
            update,
            "⚙️ <b>JARVIS V2 SETTINGS</b>\n\n"
            f"Balance: <b>{money(s.starting_balance_usd)}</b>\n"
            f"Risk/trade: <b>{s.risk_per_trade_pct:.2f}%</b>\n"
            f"Max position: <b>{s.max_position_pct:.1f}%</b>\n"
            f"Positions: <b>{s.max_open_positions}</b>\n"
            f"Stop / target: <b>{s.stop_loss_pct:.1f}% / +{s.take_profit_pct:.1f}%</b>\n"
            f"Trailing: <b>activate +{s.trailing_activation_pct:.1f}%, distance {s.trailing_distance_pct:.1f}%</b>\n"
            f"Daily loss: <b>{s.max_daily_loss_pct:.1f}%</b>\n"
            f"Entries/day: <b>{s.max_daily_entries}</b>\n"
            f"Minimum score: <b>{s.min_score}/100</b>\n"
            f"Liquidity minimum: <b>{money(s.min_liquidity_usd)}</b>\n"
            f"Volume minimum: <b>{money(s.min_volume_24h_usd)}</b>\n"
            f"RugCheck required: <b>{'yes' if s.rugcheck_required else 'no'}</b>\n"
            "Mode: 🧪 <b>PAPER ONLY</b>",
        )

    async def health(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        data = self.engine.health()
        await self.reply(
            update,
            "🩺 <b>HEALTH</b>\n\n"
            f"Loop: <b>{'running' if data['ok'] else 'stopped'}</b>\n"
            f"Uptime: <b>{data['uptime_seconds']} seconds</b>\n"
            f"API requests: <b>{data['api_requests']}</b>\n"
            f"Last scan: <b>{escape(str(data['last_scan_at'] or 'not yet'))}</b>\n"
            f"Last error: <b>{escape(str(data['last_error'] or data['market_last_error'] or 'none'))}</b>",
        )

    async def resetpaper(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        if not context.args or context.args[0].upper() != "CONFIRM":
            await self.reply(
                update,
                "This erases the simulated balance, positions and history. To continue, send "
                "<code>/resetpaper CONFIRM</code>.",
            )
            return
        await self.broker.reset_paper_account()
        await self.reply(
            update,
            f"♻️ <b>PAPER ACCOUNT RESET TO {money(self.settings.starting_balance_usd)}.</b>",
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await self.reject(update)
            return
        await self.reply(
            update,
            "🤖 <b>JARVIS V2 COMMANDS</b>\n\n"
            "/status — full account status\n"
            "/today — today's performance\n"
            "/positions — open paper positions\n"
            "/trades [number] — completed trades\n"
            "/candidates — latest ranked candidates\n"
            "/scan — run a market scan now\n"
            "/scan &lt;address&gt; — analyse one Solana token\n"
            "/pause — stop new entries\n"
            "/resume — allow new entries\n"
            "/settings — risk and strategy settings\n"
            "/health — service health\n"
            "/resetpaper CONFIRM — erase simulated history\n\n"
            "Existing positions keep being monitored while entries are paused.",
        )

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        LOGGER.exception("Telegram handler error", exc_info=context.error)


# ========================================================================
# Combined from: app.py
# ========================================================================
import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application



def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def main() -> None:
    load_dotenv()
    configure_logging()
    logger = logging.getLogger("jarvis.startup")
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    store = StateStore(settings)
    broker = PaperBroker(settings, store)
    market = MarketDataClient(settings)
    strategy = Strategy(settings)
    engine = ResearchEngine(settings, market, strategy, broker)
    telegram_ui = TelegramInterface(settings, engine, broker)
    health_server = HealthServer(engine, settings.port)

    async def notify(message: str) -> None:
        await application.bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def post_init(app: Application) -> None:
        engine.set_notifier(notify)
        await telegram_ui.configure_commands(app)
        await health_server.start()
        task = asyncio.create_task(engine.run(), name="jarvis-research-engine")
        app.bot_data["engine_task"] = task
        await notify(
            "🤖 <b>JARVIS V2 STARTED</b>\n"
            "Mode: 🧪 <b>PAPER TRADING ONLY</b>\n"
            "No wallet connected. Use /status to check progress."
        )
        logger.info("Jarvis V2 started in paper-only mode")

    async def post_shutdown(app: Application) -> None:
        task = app.bot_data.get("engine_task")
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.stop()
        await health_server.stop()
        logger.info("Jarvis V2 stopped")

    application = telegram_ui.build_application(
        post_init=post_init,
        post_shutdown=post_shutdown,
    )
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=True,
    )


if __name__ == "__main__":
    main()
