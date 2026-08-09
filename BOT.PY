import os
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# JARVIS REAL-MARKET PAPER TRADING BOT
# SOLANA + DEXSCREENER + TELEGRAM
# ============================================================

SYDNEY = ZoneInfo("Australia/Sydney")

CONFIG = {

    # ========================================================
    # PAPER ACCOUNT
    # ========================================================

    "starting_balance": 1000.00,

    # ========================================================
    # RISK
    # ========================================================

    "risk_per_trade_pct": 1.0,
    "max_position_pct": 10.0,
    "max_open_positions": 3,

    # ========================================================
    # EXITS
    # ========================================================

    "stop_loss_pct": 8.0,
    "take_profit_pct": 18.0,

    # Trailing stop activates after profit reaches this
    "trailing_activation_pct": 8.0,

    # Sell if price drops this much from highest price
    "trailing_stop_pct": 6.0,

    # ========================================================
    # DAILY SAFETY
    # ========================================================

    "max_daily_loss_pct": 5.0,
    "max_daily_entries": 30,

    # ========================================================
    # TOKEN FILTERS
    # ========================================================

    "min_liquidity": 25000,
    "min_volume_24h": 50000,

    "min_market_cap": 40000,
    "max_market_cap": 5000000,

    "min_5m_change": 2.0,
    "max_5m_change": 40.0,

    "min_1h_change": -5.0,
    "max_1h_change": 150.0,

    "min_5m_buys": 5,

    "min_score": 65,

    # Avoid pairs created only seconds ago
    "min_pair_age_minutes": 3,

    # Ignore old pairs if looking for newer meme coins
    "max_pair_age_days": 30,

    # ========================================================
    # LOOP TIMING
    # ========================================================

    "scan_every_seconds": 30,
    "price_update_seconds": 10,

    # Number of recent profile tokens to inspect per scan
    "max_tokens_per_scan": 25,

    # Telegram automatic status frequency
    "status_every_seconds": 300,

    # ========================================================
    # MODE
    # ========================================================

    "paper_mode": True
}


DEX_BASE = "https://api.dexscreener.com"

STATE_FILE = "jarvis_state.json"


# ============================================================
# HELPERS
# ============================================================

def now_sydney():

    return datetime.now(SYDNEY)


def today_key():

    return now_sydney().strftime("%Y-%m-%d")


def safe_float(value, default=0.0):

    try:

        if value is None:
            return default

        return float(value)

    except:

        return default


def safe_int(value, default=0):

    try:

        if value is None:
            return default

        return int(value)

    except:

        return default


def http_json(url, method="GET", data=None, timeout=15):

    try:

        headers = {
            "User-Agent": "JarvisPaperTrader/1.0",
            "Accept": "application/json"
        }

        if data is not None:

            encoded = urllib.parse.urlencode(
                data
            ).encode("utf-8")

            request = urllib.request.Request(
                url,
                data=encoded,
                headers=headers,
                method=method
            )

        else:

            request = urllib.request.Request(
                url,
                headers=headers,
                method=method
            )

        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            text = response.read().decode(
                "utf-8"
            )

            return json.loads(text)

    except Exception as error:

        print(
            "HTTP ERROR:",
            url,
            error
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

class TelegramManager:

    def __init__(self):

        self.token = os.getenv(
            "TELEGRAM_BOT_TOKEN",
            ""
        ).strip()

        self.chat_id = os.getenv(
            "TELEGRAM_CHAT_ID",
            ""
        ).strip()

        self.offset = None

        if not self.token:

            print(
                "WARNING: TELEGRAM_BOT_TOKEN missing"
            )

        else:

            print(
                "Telegram token loaded."
            )

            self.delete_webhook()

            self.set_commands()


    def api(
        self,
        method,
        data=None
    ):

        if not self.token:
            return None

        url = (
            "https://api.telegram.org/"
            f"bot{self.token}/"
            f"{method}"
        )

        return http_json(
            url,
            method="POST",
            data=data
        )


    def delete_webhook(self):

        result = self.api(
            "deleteWebhook",
            {
                "drop_pending_updates": "false"
            }
        )

        if result:
            print(
                "Telegram webhook cleared."
            )


    def set_commands(self):

        commands = json.dumps([
            {
                "command": "status",
                "description": "Account dashboard"
            },
            {
                "command": "positions",
                "description": "Open paper trades"
            },
            {
                "command": "trades",
                "description": "Recent closed trades"
            },
            {
                "command": "today",
                "description": "Today's performance"
            },
            {
                "command": "pause",
                "description": "Pause new entries"
            },
            {
                "command": "resume",
                "description": "Resume entries"
            },
            {
                "command": "paper",
                "description": "Show trading mode"
            },
            {
                "command": "help",
                "description": "Show commands"
            }
        ])

        self.api(
            "setMyCommands",
            {
                "commands": commands
            }
        )


    def send(
        self,
        message
    ):

        if not self.token:
            return

        if not self.chat_id:

            print(
                "Telegram chat ID missing."
            )

            return

        self.api(
            "sendMessage",
            {
                "chat_id":
                    self.chat_id,

                "text":
                    message
            }
        )


    def get_updates(self):

        if not self.token:
            return []

        data = {
            "timeout": 1
        }

        if self.offset is not None:

            data["offset"] = (
                self.offset
            )

        result = self.api(
            "getUpdates",
            data
        )

        if not result:
            return []

        if not result.get("ok"):
            return []

        updates = result.get(
            "result",
            []
        )

        if updates:

            self.offset = (
                updates[-1]["update_id"]
                + 1
            )

        return updates


    def handle_commands(
        self,
        bot
    ):

        updates = self.get_updates()

        for update in updates:

            message = update.get(
                "message",
                {}
            )

            chat = message.get(
                "chat",
                {}
            )

            incoming_chat_id = str(
                chat.get(
                    "id",
                    ""
                )
            )

            text = message.get(
                "text",
                ""
            ).strip()

            if not incoming_chat_id:
                continue

            if not self.chat_id:

                self.chat_id = (
                    incoming_chat_id
                )

                print(
                    "Telegram connected:",
                    self.chat_id
                )

            if (
                incoming_chat_id
                !=
                self.chat_id
            ):

                continue

            command = (
                text.lower()
                .split()[0]
                if text
                else ""
            )


            if command in [
                "/start",
                "start"
            ]:

                self.send(
                    "🤖 JARVIS CONNECTED\n\n"
                    "Real Solana market data: ACTIVE\n"
                    "Trading mode: PAPER ONLY\n\n"
                    "Commands:\n"
                    "/status\n"
                    "/positions\n"
                    "/trades\n"
                    "/today\n"
                    "/pause\n"
                    "/resume\n"
                    "/paper\n"
                    "/help"
                )


            elif command == "/status":

                self.send(
                    bot.status_message()
                )


            elif command == "/positions":

                self.send(
                    bot.positions_message()
                )


            elif command == "/trades":

                self.send(
                    bot.trades_message()
                )


            elif command == "/today":

                self.send(
                    bot.today_message()
                )


            elif command == "/paper":

                self.send(
                    "📝 JARVIS MODE\n\n"
                    "PAPER TRADING ONLY\n\n"
                    "Real market prices are used,\n"
                    "but no wallet or real funds are connected."
                )


            elif command == "/pause":

                bot.entries_paused = True

                bot.save_state()

                self.send(
                    "⏸ JARVIS PAUSED\n\n"
                    "No new paper entries will open.\n"
                    "Existing positions will still be managed."
                )


            elif command == "/resume":

                bot.entries_paused = False

                bot.save_state()

                self.send(
                    "▶️ JARVIS RESUMED\n\n"
                    "New paper entries are allowed again."
                )


            elif command == "/help":

                self.send(
                    "🤖 JARVIS COMMANDS\n\n"
                    "/status - dashboard\n"
                    "/positions - open trades\n"
                    "/trades - recent trades\n"
                    "/today - daily performance\n"
                    "/pause - stop new entries\n"
                    "/resume - restart entries\n"
                    "/paper - show mode\n"
                    "/help - commands"
                )


# ============================================================
# DEXSCREENER
# ============================================================

class DexScreener:

    def recent_profiles(self):

        url = (
            f"{DEX_BASE}/"
            "token-profiles/latest/v1"
        )

        result = http_json(
            url
        )

        if isinstance(
            result,
            list
        ):

            return result

        return []


    def token_pairs(
        self,
        token_address
    ):

        url = (
            f"{DEX_BASE}/"
            "token-pairs/v1/"
            f"solana/"
            f"{token_address}"
        )

        result = http_json(
            url
        )

        if isinstance(
            result,
            list
        ):

            return result

        return []


    def search_pair(
        self,
        query
    ):

        encoded = urllib.parse.quote(
            query
        )

        url = (
            f"{DEX_BASE}/latest/"
            f"dex/search?q="
            f"{encoded}"
        )

        result = http_json(
            url
        )

        if not result:
            return []

        return result.get(
            "pairs",
            []
        )


    def best_pair(
        self,
        token_address
    ):

        pairs = self.token_pairs(
            token_address
        )

        solana_pairs = []

        for pair in pairs:

            if (
                pair.get(
                    "chainId"
                )
                !=
                "solana"
            ):
                continue

            liquidity = safe_float(
                pair.get(
                    "liquidity",
                    {}
                ).get(
                    "usd"
                )
            )

            if liquidity <= 0:
                continue

            solana_pairs.append(
                pair
            )

        if not solana_pairs:
            return None

        solana_pairs.sort(
            key=lambda p:
                safe_float(
                    p.get(
                        "liquidity",
                        {}
                    ).get(
                        "usd"
                    )
                ),
            reverse=True
        )

        return solana_pairs[0]


# ============================================================
# JARVIS
# ============================================================

class JarvisBot:

    def __init__(self):

        self.dex = DexScreener()

        self.telegram = (
            TelegramManager()
        )

        self.balance = (
            CONFIG[
                "starting_balance"
            ]
        )

        self.positions = {}

        self.trade_history = []

        self.total_profit = 0.0

        self.total_wins = 0

        self.total_losses = 0

        self.daily_profit = 0.0

        self.daily_entries = 0

        self.day_key = (
            today_key()
        )

        self.day_start_equity = (
            CONFIG[
                "starting_balance"
            ]
        )

        self.entries_paused = False

        self.last_scan = 0

        self.last_status = 0

        self.scanned_addresses = {}

        self.load_state()

        self.reset_day_if_needed()

        print(
            "=" * 60
        )

        print(
            "JARVIS REAL MARKET PAPER BOT"
        )

        print(
            "=" * 60
        )

        print(
            "Mode: PAPER TRADING"
        )

        print(
            "Data: DEXSCREENER"
        )

        print(
            f"Equity: "
            f"${self.get_equity():,.2f}"
        )

        print(
            "=" * 60
        )


    # ========================================================
    # STATE
    # ========================================================

    def load_state(self):

        if not os.path.exists(
            STATE_FILE
        ):

            return

        try:

            with open(
                STATE_FILE,
                "r"
            ) as file:

                data = json.load(
                    file
                )

            self.balance = safe_float(
                data.get(
                    "balance",
                    self.balance
                ),
                self.balance
            )

            self.positions = data.get(
                "positions",
                {}
            )

            self.trade_history = (
                data.get(
                    "trade_history",
                    []
                )
            )

            self.total_profit = safe_float(
                data.get(
                    "total_profit"
                )
            )

            self.total_wins = safe_int(
                data.get(
                    "total_wins"
                )
            )

            self.total_losses = safe_int(
                data.get(
                    "total_losses"
                )
            )

            self.daily_profit = safe_float(
                data.get(
                    "daily_profit"
                )
            )

            self.daily_entries = safe_int(
                data.get(
                    "daily_entries"
                )
            )

            self.day_key = data.get(
                "day_key",
                today_key()
            )

            self.day_start_equity = safe_float(
                data.get(
                    "day_start_equity",
                    CONFIG[
                        "starting_balance"
                    ]
                )
            )

            self.entries_paused = bool(
                data.get(
                    "entries_paused",
                    False
                )
            )

            print(
                "Saved state loaded."
            )

        except Exception as error:

            print(
                "State load error:",
                error
            )


    def save_state(self):

        try:

            data = {

                "balance":
                    self.balance,

                "positions":
                    self.positions,

                "trade_history":
                    self.trade_history[-500:],

                "total_profit":
                    self.total_profit,

                "total_wins":
                    self.total_wins,

                "total_losses":
                    self.total_losses,

                "daily_profit":
                    self.daily_profit,

                "daily_entries":
                    self.daily_entries,

                "day_key":
                    self.day_key,

                "day_start_equity":
                    self.day_start_equity,

                "entries_paused":
                    self.entries_paused
            }

            with open(
                STATE_FILE,
                "w"
            ) as file:

                json.dump(
                    data,
                    file,
                    indent=2
                )

        except Exception as error:

            print(
                "State save error:",
                error
            )


    # ========================================================
    # DAY RESET
    # ========================================================

    def reset_day_if_needed(
        self
    ):

        current = today_key()

        if current == self.day_key:
            return

        self.day_key = current

        self.daily_profit = 0.0

        self.daily_entries = 0

        self.day_start_equity = (
            self.get_equity()
        )

        self.save_state()

        self.telegram.send(
            "🌅 NEW TRADING DAY\n\n"
            f"Starting equity: "
            f"${self.day_start_equity:,.2f}\n"
            "Daily counters reset."
        )


    # ========================================================
    # EQUITY
    # ========================================================

    def get_equity(self):

        open_value = 0.0

        for pos in (
            self.positions.values()
        ):

            price = safe_float(
                pos.get(
                    "current_price"
                )
            )

            quantity = safe_float(
                pos.get(
                    "quantity"
                )
            )

            open_value += (
                price
                *
                quantity
            )

        return (
            self.balance
            +
            open_value
        )


    # ========================================================
    # DAILY LOSS LIMIT
    # ========================================================

    def daily_loss_limit_hit(
        self
    ):

        if (
            self.day_start_equity
            <= 0
        ):

            return False

        equity = (
            self.get_equity()
        )

        loss_pct = (
            (
                self.day_start_equity
                -
                equity
            )
            /
            self.day_start_equity
            *
            100
        )

        return (
            loss_pct
            >=
            CONFIG[
                "max_daily_loss_pct"
            ]
        )


    # ========================================================
    # PAIR AGE
    # ========================================================

    def pair_age_minutes(
        self,
        pair
    ):

        created_ms = safe_int(
            pair.get(
                "pairCreatedAt"
            )
        )

        if created_ms <= 0:

            return None

        created_seconds = (
            created_ms
            /
            1000
        )

        age_seconds = (
            time.time()
            -
            created_seconds
        )

        return (
            age_seconds
            /
            60
        )


    # ========================================================
    # TOKEN NORMALISER
    # ========================================================

    def pair_to_token(
        self,
        pair
    ):

        base = pair.get(
            "baseToken",
            {}
        )

        symbol = (
            base.get(
                "symbol",
                "UNKNOWN"
            )
        )

        name = (
            base.get(
                "name",
                symbol
            )
        )

        token_address = (
            base.get(
                "address",
                ""
            )
        )

        price = safe_float(
            pair.get(
                "priceUsd"
            )
        )

        liquidity = safe_float(
            pair.get(
                "liquidity",
                {}
            ).get(
                "usd"
            )
        )

        volume24 = safe_float(
            pair.get(
                "volume",
                {}
            ).get(
                "h24"
            )
        )

        market_cap = safe_float(
            pair.get(
                "marketCap"
            )
        )

        if market_cap <= 0:

            market_cap = safe_float(
                pair.get(
                    "fdv"
                )
            )

        price_change = (
            pair.get(
                "priceChange",
                {}
            )
        )

        change5 = safe_float(
            price_change.get(
                "m5"
            )
        )

        change1h = safe_float(
            price_change.get(
                "h1"
            )
        )

        txns = pair.get(
            "txns",
            {}
        )

        m5_txns = txns.get(
            "m5",
            {}
        )

        buys5 = safe_int(
            m5_txns.get(
                "buys"
            )
        )

        sells5 = safe_int(
            m5_txns.get(
                "sells"
            )
        )

        age_minutes = (
            self.pair_age_minutes(
                pair
            )
        )

        return {

            "symbol":
                symbol,

            "name":
                name,

            "token_address":
                token_address,

            "pair_address":
                pair.get(
                    "pairAddress",
                    ""
                ),

            "dex":
                pair.get(
                    "dexId",
                    ""
                ),

            "price":
                price,

            "liquidity":
                liquidity,

            "volume24":
                volume24,

            "market_cap":
                market_cap,

            "change5":
                change5,

            "change1h":
                change1h,

            "buys5":
                buys5,

            "sells5":
                sells5,

            "age_minutes":
                age_minutes,

            "url":
                pair.get(
                    "url",
                    ""
                )
        }


    # ========================================================
    # SCORE
    # ========================================================

    def score_token(
        self,
        token
    ):

        score = 0


        liquidity = token[
            "liquidity"
        ]

        if liquidity >= 250000:
            score += 20

        elif liquidity >= 100000:
            score += 18

        elif liquidity >= 50000:
            score += 14

        elif liquidity >= 25000:
            score += 10


        volume = token[
            "volume24"
        ]

        if volume >= 1000000:
            score += 20

        elif volume >= 500000:
            score += 18

        elif volume >= 150000:
            score += 14

        elif volume >= 50000:
            score += 10


        change5 = token[
            "change5"
        ]

        if 5 <= change5 <= 15:
            score += 20

        elif 2 <= change5 < 5:
            score += 14

        elif 15 < change5 <= 30:
            score += 12


        change1h = token[
            "change1h"
        ]

        if 5 <= change1h <= 40:
            score += 15

        elif 0 <= change1h < 5:
            score += 8

        elif 40 < change1h <= 100:
            score += 7


        buys = token[
            "buys5"
        ]

        sells = token[
            "sells5"
        ]

        total = (
            buys
            +
            sells
        )

        if total > 0:

            buy_ratio = (
                buys
                /
                total
            )

            if (
                buy_ratio
                >= 0.70
            ):

                score += 20

            elif (
                buy_ratio
                >= 0.60
            ):

                score += 15

            elif (
                buy_ratio
                >= 0.52
            ):

                score += 8


        mc = token[
            "market_cap"
        ]

        if (
            75000
            <= mc
            <= 1000000
        ):

            score += 10

        elif (
            1000000
            < mc
            <= 5000000
        ):

            score += 6


        age = token[
            "age_minutes"
        ]

        if age is not None:

            if (
                10
                <= age
                <= 24 * 60
            ):

                score += 10

            elif (
                3
                <= age
                <= 3 * 24 * 60
            ):

                score += 7


        return min(
            score,
            100
        )


    # ========================================================
    # FILTER
    # ========================================================

    def valid_token(
        self,
        token
    ):

        if (
            token[
                "price"
            ]
            <= 0
        ):

            return False


        if (
            token[
                "liquidity"
            ]
            <
            CONFIG[
                "min_liquidity"
            ]
        ):

            return False


        if (
            token[
                "volume24"
            ]
            <
            CONFIG[
                "min_volume_24h"
            ]
        ):

            return False


        mc = token[
            "market_cap"
        ]

        if (
            mc
            <
            CONFIG[
                "min_market_cap"
            ]
        ):

            return False


        if (
            mc
            >
            CONFIG[
                "max_market_cap"
            ]
        ):

            return False


        change5 = token[
            "change5"
        ]

        if (
            change5
            <
            CONFIG[
                "min_5m_change"
            ]
        ):

            return False


        if (
            change5
            >
            CONFIG[
                "max_5m_change"
            ]
        ):

            return False


        change1h = token[
            "change1h"
        ]

        if (
            change1h
            <
            CONFIG[
                "min_1h_change"
            ]
        ):

            return False


        if (
            change1h
            >
            CONFIG[
                "max_1h_change"
            ]
        ):

            return False


        if (
            token[
                "buys5"
            ]
            <
            CONFIG[
                "min_5m_buys"
            ]
        ):

            return False


        age = token[
            "age_minutes"
        ]

        if age is not None:

            if (
                age
                <
                CONFIG[
                    "min_pair_age_minutes"
                ]
            ):

                return False


            max_age = (
                CONFIG[
                    "max_pair_age_days"
                ]
                *
                24
                *
                60
            )

            if (
                age
                >
                max_age
            ):

                return False


        return True


    # ========================================================
    # MARKET SCAN
    # ========================================================

    def scan_market(
        self
    ):

        print(
            "Scanning live Solana market..."
        )

        profiles = (
            self.dex.recent_profiles()
        )

        candidates = []

        seen = set()

        checked = 0


        for profile in profiles:

            if (
                checked
                >=
                CONFIG[
                    "max_tokens_per_scan"
                ]
            ):

                break


            chain_id = profile.get(
                "chainId"
            )

            if (
                chain_id
                !=
                "solana"
            ):

                continue


            token_address = profile.get(
                "tokenAddress"
            )

            if not token_address:
                continue


            if token_address in seen:
                continue


            seen.add(
                token_address
            )

            checked += 1


            pair = (
                self.dex.best_pair(
                    token_address
                )
            )


            if not pair:
                continue


            token = (
                self.pair_to_token(
                    pair
                )
            )


            if not self.valid_token(
                token
            ):

                continue


            token["score"] = (
                self.score_token(
                    token
                )
            )


            if (
                token["score"]
                <
                CONFIG[
                    "min_score"
                ]
            ):

                continue


            candidates.append(
                token
            )


            time.sleep(
                0.15
            )


        candidates.sort(
            key=lambda item:
                item[
                    "score"
                ],
            reverse=True
        )


        print(
            "Valid live candidates:",
            len(candidates)
        )


        for token in (
            candidates[:5]
        ):

            print(
                token[
                    "symbol"
                ],
                "| Score",
                token[
                    "score"
                ],
                "| 5m",
                f"{token['change5']:+.2f}%",
                "| Liq",
                f"${token['liquidity']:,.0f}",
                "| MC",
                f"${token['market_cap']:,.0f}"
            )


        return candidates


    # ========================================================
    # POSITION SIZE
    # ========================================================

    def position_size(
        self
    ):

        equity = (
            self.get_equity()
        )

        risk_dollars = (
            equity
            *
            CONFIG[
                "risk_per_trade_pct"
            ]
            /
            100
        )

        stop_fraction = (
            CONFIG[
                "stop_loss_pct"
            ]
            /
            100
        )

        calculated = (
            risk_dollars
            /
            stop_fraction
        )

        max_position = (
            equity
            *
            CONFIG[
                "max_position_pct"
            ]
            /
            100
        )

        return max(
            0,
            min(
                calculated,
                max_position,
                self.balance
            )
        )


    # ========================================================
    # BUY
    # ========================================================

    def buy(
        self,
        token
    ):

        self.reset_day_if_needed()


        if self.entries_paused:

            return


        if self.daily_loss_limit_hit():

            return


        if (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):

            return


        if (
            len(
                self.positions
            )
            >=
            CONFIG[
                "max_open_positions"
            ]
        ):

            return


        token_address = (
            token[
                "token_address"
            ]
        )


        if (
            token_address
            in
            self.positions
        ):

            return


        amount = (
            self.position_size()
        )


        if amount <= 0:

            return


        price = token[
            "price"
        ]


        quantity = (
            amount
            /
            price
        )


        self.positions[
            token_address
        ] = {

            "symbol":
                token[
                    "symbol"
                ],

            "name":
                token[
                    "name"
                ],

            "token_address":
                token_address,

            "pair_address":
                token[
                    "pair_address"
                ],

            "entry_price":
                price,

            "current_price":
                price,

            "highest_price":
                price,

            "quantity":
                quantity,

            "amount":
                amount,

            "score":
                token[
                    "score"
                ],

            "opened_at":
                now_sydney()
                .isoformat()
        }


        self.balance -= amount


        # IMPORTANT:
        # increment on ENTRY, not exit.
        # This fixes 32/30 bug.

        self.daily_entries += 1


        self.save_state()


        self.telegram.send(

            "🟢 PAPER SNIPE OPENED\n\n"

            f"{token['name']} "
            f"({token['symbol']})\n\n"

            f"Score: "
            f"{token['score']}/100\n"

            f"Entry: "
            f"${price:.10f}\n"

            f"Position: "
            f"${amount:.2f}\n\n"

            f"5m: "
            f"{token['change5']:+.2f}%\n"

            f"1h: "
            f"{token['change1h']:+.2f}%\n"

            f"5m buys/sells: "
            f"{token['buys5']}/"
            f"{token['sells5']}\n"

            f"Liquidity: "
            f"${token['liquidity']:,.0f}\n"

            f"24h volume: "
            f"${token['volume24']:,.0f}\n"

            f"Market cap: "
            f"${token['market_cap']:,.0f}\n\n"

            f"Target: "
            f"+{CONFIG['take_profit_pct']}%\n"

            f"Stop: "
            f"-{CONFIG['stop_loss_pct']}%\n\n"

            f"Today's entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n\n"

            "Mode: PAPER"
        )


    # ========================================================
    # LIVE POSITION PRICE UPDATE
    # ========================================================

    def update_positions(
        self
    ):

        if not self.positions:
            return


        for token_address in list(
            self.positions.keys()
        ):

            pos = (
                self.positions[
                    token_address
                ]
            )


            pair = (
                self.dex.best_pair(
                    token_address
                )
            )


            if not pair:

                print(
                    "No live pair found:",
                    pos[
                        "symbol"
                    ]
                )

                continue


            new_price = safe_float(
                pair.get(
                    "priceUsd"
                )
            )


            if new_price <= 0:
                continue


            pos[
                "current_price"
            ] = new_price


            if (
                new_price
                >
                pos[
                    "highest_price"
                ]
            ):

                pos[
                    "highest_price"
                ] = new_price


            entry = safe_float(
                pos[
                    "entry_price"
                ]
            )


            pnl_pct = (
                (
                    new_price
                    -
                    entry
                )
                /
                entry
                *
                100
            )


            high = safe_float(
                pos[
                    "highest_price"
                ]
            )


            trailing_drop = (
                (
                    high
                    -
                    new_price
                )
                /
                high
                *
                100
            )


            print(
                pos[
                    "symbol"
                ],
                "| LIVE PnL",
                f"{pnl_pct:+.2f}%",
                "|",
                f"${new_price:.10f}"
            )


            # -----------------------------------------------
            # HARD STOP
            # -----------------------------------------------

            if (
                pnl_pct
                <=
                -CONFIG[
                    "stop_loss_pct"
                ]
            ):

                self.sell(
                    token_address,
                    "STOP LOSS"
                )

                continue


            # -----------------------------------------------
            # TAKE PROFIT
            # -----------------------------------------------

            if (
                pnl_pct
                >=
                CONFIG[
                    "take_profit_pct"
                ]
            ):

                self.sell(
                    token_address,
                    "TAKE PROFIT"
                )

                continue


            # -----------------------------------------------
            # TRAILING STOP
            # -----------------------------------------------

            high_profit_pct = (
                (
                    high
                    -
                    entry
                )
                /
                entry
                *
                100
            )


            if (

                high_profit_pct
                >=
                CONFIG[
                    "trailing_activation_pct"
                ]

                and

                trailing_drop
                >=
                CONFIG[
                    "trailing_stop_pct"
                ]

            ):

                self.sell(
                    token_address,
                    "TRAILING STOP"
                )


            time.sleep(
                0.15
            )


        self.save_state()


    # ========================================================
    # SELL
    # ========================================================

    def sell(
        self,
        token_address,
        reason
    ):

        if (
            token_address
            not in
            self.positions
        ):

            return


        pos = (
            self.positions[
                token_address
            ]
        )


        exit_price = safe_float(
            pos[
                "current_price"
            ]
        )


        value = (
            safe_float(
                pos[
                    "quantity"
                ]
            )
            *
            exit_price
        )


        amount = safe_float(
            pos[
                "amount"
            ]
        )


        profit = (
            value
            -
            amount
        )


        pnl_pct = (
            profit
            /
            amount
            *
            100
        )


        self.balance += value

        self.total_profit += (
            profit
        )

        self.daily_profit += (
            profit
        )


        if profit >= 0:

            self.total_wins += 1

            icon = "🟢"

        else:

            self.total_losses += 1

            icon = "🔴"


        trade = {

            "symbol":
                pos[
                    "symbol"
                ],

            "token_address":
                token_address,

            "entry_price":
                pos[
                    "entry_price"
                ],

            "exit_price":
                exit_price,

            "profit":
                profit,

            "pnl_pct":
                pnl_pct,

            "reason":
                reason,

            "closed_at":
                now_sydney()
                .isoformat()
        }


        self.trade_history.append(
            trade
        )


        del self.positions[
            token_address
        ]


        self.save_state()


        self.telegram.send(

            f"{icon} PAPER SNIPE CLOSED\n\n"

            f"{pos['name']} "
            f"({pos['symbol']})\n\n"

            f"Reason: "
            f"{reason}\n"

            f"Entry: "
            f"${pos['entry_price']:.10f}\n"

            f"Exit: "
            f"${exit_price:.10f}\n\n"

            f"Return: "
            f"{pnl_pct:+.2f}%\n"

            f"P/L: "
            f"${profit:+.2f}\n\n"

            f"Today's P/L: "
            f"${self.daily_profit:+.2f}\n"

            f"Today's entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}"
        )


    # ========================================================
    # STATUS
    # ========================================================

    def status_message(
        self
    ):

        total_closed = (
            self.total_wins
            +
            self.total_losses
        )


        win_rate = (

            self.total_wins
            /
            total_closed
            *
            100

            if total_closed > 0

            else 0
        )


        if self.entries_paused:

            status = (
                "⏸ PAUSED"
            )

        elif self.daily_loss_limit_hit():

            status = (
                "🛑 DAILY LOSS STOP"
            )

        elif (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):

            status = (
                "🟡 DAILY TRADE LIMIT"
            )

        else:

            status = (
                "🟢 ACTIVE"
            )


        return (

            "🤖 JARVIS STATUS\n\n"

            f"Cash: "
            f"${self.balance:,.2f}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n"

            f"Total P/L: "
            f"${self.total_profit:+,.2f}\n"

            f"Today's P/L: "
            f"${self.daily_profit:+,.2f}\n\n"

            f"Wins: "
            f"{self.total_wins}\n"

            f"Losses: "
            f"{self.total_losses}\n"

            f"Win rate: "
            f"{win_rate:.1f}%\n"

            f"Open trades: "
            f"{len(self.positions)}\n"

            f"Today's entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n\n"

            f"Status: "
            f"{status}\n"

            "Market data: LIVE\n"

            "Mode: PAPER TRADING"
        )


    # ========================================================
    # TODAY
    # ========================================================

    def today_message(
        self
    ):

        return (

            "📅 TODAY'S PERFORMANCE\n\n"

            f"Date: "
            f"{self.day_key}\n"

            f"P/L: "
            f"${self.daily_profit:+.2f}\n"

            f"Entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n"

            f"Open trades: "
            f"{len(self.positions)}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n\n"

            "Prices: REAL MARKET\n"

            "Execution: PAPER ONLY"
        )


    # ========================================================
    # POSITIONS
    # ========================================================

    def positions_message(
        self
    ):

        if not self.positions:

            return (
                "📊 OPEN POSITIONS\n\n"
                "No open paper trades."
            )


        message = (
            "📊 OPEN POSITIONS\n\n"
        )


        for pos in (
            self.positions.values()
        ):

            entry = safe_float(
                pos[
                    "entry_price"
                ]
            )

            current = safe_float(
                pos[
                    "current_price"
                ]
            )

            pnl_pct = (
                (
                    current
                    -
                    entry
                )
                /
                entry
                *
                100
            )


            message += (

                f"{pos['symbol']}\n"

                f"Entry: "
                f"${entry:.10f}\n"

                f"Current: "
                f"${current:.10f}\n"

                f"P/L: "
                f"{pnl_pct:+.2f}%\n"

                f"Position: "
                f"${pos['amount']:.2f}\n\n"
            )


        return message


    # ========================================================
    # TRADES
    # ========================================================

    def trades_message(
        self
    ):

        if not self.trade_history:

            return (
                "📜 RECENT TRADES\n\n"
                "No closed trades yet."
            )


        message = (
            "📜 RECENT TRADES\n\n"
        )


        for trade in reversed(
            self.trade_history[
                -10:
            ]
        ):

            message += (

                f"{trade['symbol']} | "
                f"{trade['reason']}\n"

                f"{trade['pnl_pct']:+.2f}% | "

                f"${trade['profit']:+.2f}\n\n"
            )


        return message


    # ========================================================
    # ENTRY DECISION
    # ========================================================

    def try_new_entry(
        self
    ):

        self.reset_day_if_needed()


        if self.entries_paused:
            return


        if self.daily_loss_limit_hit():
            return


        if (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):

            return


        if (
            len(
                self.positions
            )
            >=
            CONFIG[
                "max_open_positions"
            ]
        ):

            return


        candidates = (
            self.scan_market()
        )


        for token in candidates:

            token_address = (
                token[
                    "token_address"
                ]
            )


            if (
                token_address
                in
                self.positions
            ):

                continue


            self.buy(
                token
            )

            break


    # ========================================================
    # RUN
    # ========================================================

    def run(
        self
    ):

        self.telegram.send(

            "🟢 JARVIS ONLINE\n\n"

            "Real Solana market data connected.\n"

            "Execution mode: PAPER ONLY\n\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n"

            f"Daily entry limit: "
            f"{CONFIG['max_daily_entries']}\n\n"

            "Send /status anytime."
        )


        last_price_update = 0


        while True:

            try:

                self.reset_day_if_needed()


                # -------------------------------------------
                # TELEGRAM COMMANDS
                # -------------------------------------------

                self.telegram.handle_commands(
                    self
                )


                current_time = time.time()


                # -------------------------------------------
                # UPDATE OPEN POSITIONS
                # -------------------------------------------

                if (

                    current_time
                    -
                    last_price_update

                    >=

                    CONFIG[
                        "price_update_seconds"
                    ]

                ):

                    self.update_positions()

                    last_price_update = (
                        current_time
                    )


                # -------------------------------------------
                # MARKET SCAN
                # -------------------------------------------

                if (

                    current_time
                    -
                    self.last_scan

                    >=

                    CONFIG[
                        "scan_every_seconds"
                    ]

                ):

                    self.try_new_entry()

                    self.last_scan = (
                        current_time
                    )


                # -------------------------------------------
                # AUTO STATUS
                # -------------------------------------------

                if (

                    current_time
                    -
                    self.last_status

                    >=

                    CONFIG[
                        "status_every_seconds"
                    ]

                ):

                    self.telegram.send(
                        self.status_message()
                    )

                    self.last_status = (
                        current_time
                    )


                time.sleep(
                    2
                )


            except KeyboardInterrupt:

                self.save_state()

                self.telegram.send(
                    "🔴 JARVIS STOPPED"
                )

                break


            except Exception as error:

                print(
                    "BOT ERROR:",
                    error
                )

                self.telegram.send(

                    "⚠️ JARVIS ERROR\n\n"

                    f"{error}"
                )

                time.sleep(
                    5
                )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    bot = JarvisBot()

    bot.run()
