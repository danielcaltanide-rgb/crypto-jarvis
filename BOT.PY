import os
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# JARVIS V5 BALANCED
# LIVE SOLANA DATA + PAPER TRADING ONLY
# ============================================================

SYDNEY = ZoneInfo("Australia/Sydney")

DEX_BASE = "https://api.dexscreener.com"
STATE_FILE = "jarvis_v5_state.json"


CONFIG = {

    # PAPER ACCOUNT
    "starting_balance": 1000.00,
    "daily_profit_target": 500.00,

    # RISK
    "risk_per_trade_pct": 0.50,
    "max_position_pct": 7.50,
    "max_open_positions": 2,

    # EXITS
    "stop_loss_pct": 6.0,
    "take_profit_pct": 14.0,
    "trailing_activation_pct": 6.0,
    "trailing_stop_pct": 4.0,

    # DAILY SAFETY
    "max_daily_loss_pct": 3.0,
    "max_daily_entries": 12,

    # COOLDOWNS
    "stop_loss_cooldown_seconds": 15 * 60,
    "token_reentry_cooldown_seconds": 60 * 60,

    # CONFIRMATION
    "required_confirmations": 2,
    "confirmation_timeout_seconds": 90,

    # BALANCED TOKEN FILTERS
    "min_liquidity": 50000,
    "min_volume_24h": 100000,
    "min_market_cap": 50000,
    "max_market_cap": 4000000,

    "min_5m_change": 1.5,
    "max_5m_change": 20.0,

    "min_1h_change": -2.0,
    "max_1h_change": 70.0,

    "min_5m_buys": 8,
    "min_buy_pressure_pct": 55.0,

    "min_score": 68,

    "min_pair_age_minutes": 5,
    "max_pair_age_days": 21,

    # TIMING
    "scan_every_seconds": 30,
    "price_update_seconds": 10,
    "status_every_seconds": 300,
    "max_tokens_per_scan": 35,

    # ALERTS
    "candidate_alert_cooldown": 1800,

    # MODE
    "paper_mode": True
}


def now_sydney():
    return datetime.now(SYDNEY)


def today_key():
    return now_sydney().strftime("%Y-%m-%d")


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def http_json(url, method="GET", data=None, timeout=15):
    try:

        headers = {
            "User-Agent": "JarvisPaperBot/5.1",
            "Accept": "application/json"
        }

        if data is not None:

            body = urllib.parse.urlencode(
                data
            ).encode("utf-8")

            request = urllib.request.Request(
                url,
                data=body,
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

            return json.loads(
                response.read().decode("utf-8")
            )

    except Exception as error:

        print(
            "HTTP ERROR:",
            url,
            error
        )

        return None


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

        if self.token:

            print("Telegram token loaded.")

            self.delete_webhook()
            self.set_commands()

        else:

            print(
                "WARNING: TELEGRAM_BOT_TOKEN missing"
            )


    def api(self, method, data=None):

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

        self.api(
            "deleteWebhook",
            {
                "drop_pending_updates": "false"
            }
        )


    def set_commands(self):

        commands = json.dumps([

            {
                "command": "status",
                "description": "Jarvis dashboard"
            },
            {
                "command": "today",
                "description": "Today's performance"
            },
            {
                "command": "live",
                "description": "Live open P/L"
            },
            {
                "command": "watch",
                "description": "Confirmation watchlist"
            },
            {
                "command": "positions",
                "description": "Open trades"
            },
            {
                "command": "trades",
                "description": "Recent trades"
            },
            {
                "command": "stats",
                "description": "Trading statistics"
            },
            {
                "command": "pause",
                "description": "Pause entries"
            },
            {
                "command": "resume",
                "description": "Resume entries"
            },
            {
                "command": "paper",
                "description": "Trading mode"
            },
            {
                "command": "help",
                "description": "Commands"
            }
        ])

        self.api(
            "setMyCommands",
            {
                "commands": commands
            }
        )


    def send(self, message):

        if not self.token or not self.chat_id:
            return

        self.api(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": message
            }
        )


    def get_updates(self):

        if not self.token:
            return []

        data = {
            "timeout": 1
        }

        if self.offset is not None:
            data["offset"] = self.offset

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


    def handle_commands(self, bot):

        for update in self.get_updates():

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
                self.chat_id = incoming_chat_id

            if incoming_chat_id != self.chat_id:
                continue

            command = (
                text.lower().split()[0]
                if text
                else ""
            )

            if command in ["/start", "start"]:

                self.send(
                    "🤖 JARVIS V5 BALANCED ONLINE\n\n"
                    "Market data: LIVE\n"
                    "Execution: PAPER ONLY\n\n"
                    "Balanced filters active.\n\n"
                    "/status\n"
                    "/today\n"
                    "/live\n"
                    "/watch\n"
                    "/positions\n"
                    "/trades\n"
                    "/stats\n"
                    "/pause\n"
                    "/resume\n"
                    "/paper\n"
                    "/help"
                )

            elif command == "/status":
                self.send(bot.status_message())

            elif command == "/today":
                self.send(bot.today_message())

            elif command == "/live":
                bot.update_positions()
                self.send(bot.live_message())

            elif command == "/watch":
                self.send(bot.watch_message())

            elif command == "/positions":
                bot.update_positions()
                self.send(bot.positions_message())

            elif command == "/trades":
                self.send(bot.trades_message())

            elif command == "/stats":
                self.send(bot.stats_message())

            elif command == "/pause":

                bot.entries_paused = True
                bot.save_state()

                self.send(
                    "⏸ JARVIS PAUSED\n\n"
                    "No new paper entries."
                )

            elif command == "/resume":

                bot.entries_paused = False
                bot.save_state()

                self.send(
                    "▶️ JARVIS RESUMED"
                )

            elif command == "/paper":

                self.send(
                    "📝 MODE\n\n"
                    "LIVE market data\n"
                    "PAPER TRADING ONLY\n\n"
                    "No wallet connected."
                )

            elif command == "/help":

                self.send(
                    "🤖 COMMANDS\n\n"
                    "/status\n"
                    "/today\n"
                    "/live\n"
                    "/watch\n"
                    "/positions\n"
                    "/trades\n"
                    "/stats\n"
                    "/pause\n"
                    "/resume\n"
                    "/paper"
                )


class DexScreener:

    def recent_profiles(self):

        result = http_json(
            DEX_BASE
            +
            "/token-profiles/latest/v1"
        )

        return result if isinstance(
            result,
            list
        ) else []


    def token_pairs(
        self,
        token_address
    ):

        result = http_json(
            DEX_BASE
            +
            "/token-pairs/v1/solana/"
            +
            token_address
        )

        return result if isinstance(
            result,
            list
        ) else []


    def best_pair(
        self,
        token_address
    ):

        pairs = self.token_pairs(
            token_address
        )

        valid = []

        for pair in pairs:

            if pair.get("chainId") != "solana":
                continue

            liquidity = safe_float(
                pair.get(
                    "liquidity",
                    {}
                ).get("usd")
            )

            if liquidity <= 0:
                continue

            valid.append(pair)

        if not valid:
            return None

        valid.sort(
            key=lambda p:
                safe_float(
                    p.get(
                        "liquidity",
                        {}
                    ).get("usd")
                ),
            reverse=True
        )

        return valid[0]


class JarvisBot:

    def __init__(self):

        self.dex = DexScreener()
        self.telegram = TelegramManager()

        self.balance = CONFIG[
            "starting_balance"
        ]

        self.positions = {}
        self.trade_history = []

        self.daily_profit = 0.0
        self.daily_entries = 0

        self.total_wins = 0
        self.total_losses = 0
        self.total_profit = 0.0

        self.day_key = today_key()

        self.day_start_equity = CONFIG[
            "starting_balance"
        ]

        self.entries_paused = False
        self.target_alert_sent = False

        self.last_scan = 0
        self.last_status = 0

        self.confirmations = {}
        self.candidate_alerts = {}
        self.token_last_exit = {}

        self.last_stop_loss_time = 0

        self.load_state()
        self.rebuild_stats()
        self.reset_day_if_needed()


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

                data = json.load(file)

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

            self.trade_history = data.get(
                "trade_history",
                []
            )

            self.daily_profit = safe_float(
                data.get(
                    "daily_profit",
                    0
                )
            )

            self.daily_entries = safe_int(
                data.get(
                    "daily_entries",
                    0
                )
            )

            self.day_key = data.get(
                "day_key",
                today_key()
            )

            self.day_start_equity = safe_float(
                data.get(
                    "day_start_equity",
                    CONFIG["starting_balance"]
                )
            )

            self.entries_paused = bool(
                data.get(
                    "entries_paused",
                    False
                )
            )

            self.confirmations = data.get(
                "confirmations",
                {}
            )

            self.candidate_alerts = data.get(
                "candidate_alerts",
                {}
            )

            self.token_last_exit = data.get(
                "token_last_exit",
                {}
            )

            self.last_stop_loss_time = safe_float(
                data.get(
                    "last_stop_loss_time",
                    0
                )
            )

            print(
                "Balanced V5 state loaded."
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

                "daily_profit":
                    self.daily_profit,

                "daily_entries":
                    self.daily_entries,

                "day_key":
                    self.day_key,

                "day_start_equity":
                    self.day_start_equity,

                "entries_paused":
                    self.entries_paused,

                "confirmations":
                    self.confirmations,

                "candidate_alerts":
                    self.candidate_alerts,

                "token_last_exit":
                    self.token_last_exit,

                "last_stop_loss_time":
                    self.last_stop_loss_time
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
                "Save error:",
                error
            )


    def rebuild_stats(self):

        self.total_profit = 0.0
        self.total_wins = 0
        self.total_losses = 0

        for trade in self.trade_history:

            profit = safe_float(
                trade.get(
                    "profit"
                )
            )

            self.total_profit += profit

            if profit >= 0:
                self.total_wins += 1
            else:
                self.total_losses += 1


    def get_open_value(self):

        value = 0.0

        for position in self.positions.values():

            value += (
                safe_float(
                    position.get(
                        "quantity"
                    )
                )
                *
                safe_float(
                    position.get(
                        "current_price"
                    )
                )
            )

        return value


    def get_equity(self):

        return (
            self.balance
            +
            self.get_open_value()
        )


    def account_profit(self):

        return (
            self.get_equity()
            -
            CONFIG["starting_balance"]
        )


    def unrealized_profit(self):

        result = 0.0

        for position in self.positions.values():

            current_value = (
                safe_float(
                    position.get(
                        "quantity"
                    )
                )
                *
                safe_float(
                    position.get(
                        "current_price"
                    )
                )
            )

            result += (
                current_value
                -
                safe_float(
                    position.get(
                        "amount"
                    )
                )
            )

        return result


    def reset_day_if_needed(self):

        current = today_key()

        if current == self.day_key:
            return

        self.day_key = current
        self.daily_profit = 0.0
        self.daily_entries = 0

        self.day_start_equity = (
            self.get_equity()
        )

        self.confirmations = {}
        self.candidate_alerts = {}

        self.save_state()


    def daily_target_hit(self):

        return (
            self.daily_profit
            >=
            CONFIG["daily_profit_target"]
        )


    def target_progress(self):

        target = CONFIG[
            "daily_profit_target"
        ]

        if target <= 0:
            return 0.0

        return max(
            0,
            min(
                self.daily_profit
                /
                target
                *
                100,
                100
            )
        )


    def daily_loss_pct(self):

        if self.day_start_equity <= 0:
            return 0.0

        change = (
            self.get_equity()
            -
            self.day_start_equity
        )

        if change >= 0:
            return 0.0

        return (
            abs(change)
            /
            self.day_start_equity
            *
            100
        )


    def daily_loss_limit_hit(self):

        return (
            self.daily_loss_pct()
            >=
            CONFIG["max_daily_loss_pct"]
        )


    def stop_loss_cooldown_remaining(self):

        if self.last_stop_loss_time <= 0:
            return 0

        remaining = (
            CONFIG[
                "stop_loss_cooldown_seconds"
            ]
            -
            (
                time.time()
                -
                self.last_stop_loss_time
            )
        )

        return max(
            0,
            int(remaining)
        )


    def stop_loss_cooldown_active(self):

        return (
            self.stop_loss_cooldown_remaining()
            >
            0
        )


    def pair_age_minutes(
        self,
        pair
    ):

        created = safe_int(
            pair.get(
                "pairCreatedAt"
            )
        )

        if created <= 0:
            return None

        return (
            time.time()
            -
            created / 1000
        ) / 60


    def pair_to_token(
        self,
        pair
    ):

        base = pair.get(
            "baseToken",
            {}
        )

        changes = pair.get(
            "priceChange",
            {}
        )

        txns = pair.get(
            "txns",
            {}
        )

        m5 = txns.get(
            "m5",
            {}
        )

        buys = safe_int(
            m5.get("buys")
        )

        sells = safe_int(
            m5.get("sells")
        )

        total = buys + sells

        buy_pressure = (
            buys / total * 100
            if total > 0
            else 0
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

        return {

            "symbol":
                base.get(
                    "symbol",
                    "UNKNOWN"
                ),

            "name":
                base.get(
                    "name",
                    "Unknown"
                ),

            "token_address":
                base.get(
                    "address",
                    ""
                ),

            "price":
                safe_float(
                    pair.get(
                        "priceUsd"
                    )
                ),

            "liquidity":
                safe_float(
                    pair.get(
                        "liquidity",
                        {}
                    ).get(
                        "usd"
                    )
                ),

            "volume24":
                safe_float(
                    pair.get(
                        "volume",
                        {}
                    ).get(
                        "h24"
                    )
                ),

            "market_cap":
                market_cap,

            "change5":
                safe_float(
                    changes.get(
                        "m5"
                    )
                ),

            "change1h":
                safe_float(
                    changes.get(
                        "h1"
                    )
                ),

            "buys5":
                buys,

            "sells5":
                sells,

            "buy_pressure":
                buy_pressure,

            "age_minutes":
                self.pair_age_minutes(
                    pair
                )
        }


    def score_token(
        self,
        token
    ):

        score = 0

        if token["liquidity"] >= 250000:
            score += 20

        elif token["liquidity"] >= 100000:
            score += 17

        elif token["liquidity"] >= 50000:
            score += 12


        if token["volume24"] >= 1000000:
            score += 20

        elif token["volume24"] >= 500000:
            score += 18

        elif token["volume24"] >= 250000:
            score += 15

        elif token["volume24"] >= 100000:
            score += 11


        if 4 <= token["change5"] <= 10:
            score += 20

        elif 1.5 <= token["change5"] < 4:
            score += 14

        elif 10 < token["change5"] <= 15:
            score += 13

        elif 15 < token["change5"] <= 20:
            score += 7


        if 5 <= token["change1h"] <= 35:
            score += 15

        elif -2 <= token["change1h"] < 5:
            score += 8

        elif 35 < token["change1h"] <= 70:
            score += 6


        if token["buy_pressure"] >= 70:
            score += 20

        elif token["buy_pressure"] >= 62:
            score += 16

        elif token["buy_pressure"] >= 58:
            score += 12

        elif token["buy_pressure"] >= 55:
            score += 8


        if (
            75000
            <=
            token["market_cap"]
            <=
            1000000
        ):
            score += 10

        elif (
            1000000
            <
            token["market_cap"]
            <=
            2500000
        ):
            score += 7

        elif (
            2500000
            <
            token["market_cap"]
            <=
            4000000
        ):
            score += 4


        return min(
            score,
            100
        )


    def valid_token(
        self,
        token
    ):

        if token["price"] <= 0:
            return False

        if token["liquidity"] < CONFIG["min_liquidity"]:
            return False

        if token["volume24"] < CONFIG["min_volume_24h"]:
            return False

        if token["market_cap"] < CONFIG["min_market_cap"]:
            return False

        if token["market_cap"] > CONFIG["max_market_cap"]:
            return False

        if token["change5"] < CONFIG["min_5m_change"]:
            return False

        if token["change5"] > CONFIG["max_5m_change"]:
            return False

        if token["change1h"] < CONFIG["min_1h_change"]:
            return False

        if token["change1h"] > CONFIG["max_1h_change"]:
            return False

        if token["buys5"] < CONFIG["min_5m_buys"]:
            return False

        if (
            token["buy_pressure"]
            <
            CONFIG["min_buy_pressure_pct"]
        ):
            return False

        age = token["age_minutes"]

        if age is not None:

            if age < CONFIG["min_pair_age_minutes"]:
                return False

            max_age = (
                CONFIG["max_pair_age_days"]
                *
                24
                *
                60
            )

            if age > max_age:
                return False

        token["score"] = (
            self.score_token(
                token
            )
        )

        return (
            token["score"]
            >=
            CONFIG["min_score"]
        )


    def token_reentry_allowed(
        self,
        address
    ):

        last = safe_float(
            self.token_last_exit.get(
                address,
                0
            )
        )

        if last <= 0:
            return True

        return (
            time.time()
            -
            last
            >=
            CONFIG[
                "token_reentry_cooldown_seconds"
            ]
        )


    def scan_market(self):

        profiles = (
            self.dex
            .recent_profiles()
        )

        candidates = []

        seen = set()

        checked = 0

        for profile in profiles:

            if checked >= CONFIG["max_tokens_per_scan"]:
                break

            if profile.get("chainId") != "solana":
                continue

            address = profile.get(
                "tokenAddress"
            )

            if not address:
                continue

            if address in seen:
                continue

            seen.add(address)

            checked += 1

            pair = self.dex.best_pair(
                address
            )

            if not pair:
                continue

            token = self.pair_to_token(
                pair
            )

            if self.valid_token(token):
                candidates.append(token)

            time.sleep(0.15)

        candidates.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        return candidates


    def update_confirmations(
        self,
        candidates
    ):

        current = time.time()

        for token in candidates:

            address = token[
                "token_address"
            ]

            existing = self.confirmations.get(
                address
            )

            if not existing:

                self.confirmations[address] = {
                    "count": 1,
                    "last_seen": current,
                    "symbol": token["symbol"],
                    "name": token["name"],
                    "score": token["score"],
                    "price": token["price"]
                }

            else:

                elapsed = (
                    current
                    -
                    safe_float(
                        existing.get(
                            "last_seen"
                        )
                    )
                )

                if elapsed > CONFIG[
                    "confirmation_timeout_seconds"
                ]:
                    existing["count"] = 1

                else:
                    existing["count"] = (
                        safe_int(
                            existing.get(
                                "count"
                            )
                        )
                        +
                        1
                    )

                existing["last_seen"] = current
                existing["score"] = token["score"]
                existing["price"] = token["price"]

        for address in list(
            self.confirmations.keys()
        ):

            record = self.confirmations[
                address
            ]

            elapsed = (
                current
                -
                safe_float(
                    record.get(
                        "last_seen"
                    )
                )
            )

            if elapsed > CONFIG[
                "confirmation_timeout_seconds"
            ]:

                del self.confirmations[
                    address
                ]

        self.save_state()


    def confirmation_count(
        self,
        address
    ):

        return safe_int(
            self.confirmations.get(
                address,
                {}
            ).get(
                "count",
                0
            )
        )


    def watch_message(self):

        if not self.confirmations:

            return (
                "👀 JARVIS WATCHLIST\n\n"
                "No tokens currently waiting "
                "for confirmation."
            )

        message = (
            "👀 JARVIS WATCHLIST\n\n"
        )

        records = sorted(
            self.confirmations.items(),
            key=lambda item:
                safe_int(
                    item[1].get(
                        "count"
                    )
                ),
            reverse=True
        )

        for address, record in records[:10]:

            message += (
                f"{record.get('symbol', 'UNKNOWN')}\n"
                f"Confirmations: "
                f"{safe_int(record.get('count'))}/"
                f"{CONFIG['required_confirmations']}\n"
                f"Score: "
                f"{safe_int(record.get('score'))}/100\n"
                f"Price: "
                f"${safe_float(record.get('price')):.10f}\n\n"
            )

        return message


    def position_size(self):

        equity = self.get_equity()

        risk_dollars = (
            equity
            *
            CONFIG["risk_per_trade_pct"]
            /
            100
        )

        stop_fraction = (
            CONFIG["stop_loss_pct"]
            /
            100
        )

        if stop_fraction <= 0:
            return 0.0

        risk_size = (
            risk_dollars
            /
            stop_fraction
        )

        max_size = (
            equity
            *
            CONFIG["max_position_pct"]
            /
            100
        )

        return max(
            0,
            min(
                risk_size,
                max_size,
                self.balance
            )
        )


    def send_candidate_alert(
        self,
        token
    ):

        address = token[
            "token_address"
        ]

        last = safe_float(
            self.candidate_alerts.get(
                address,
                0
            )
        )

        if (
            time.time()
            -
            last
            <
            CONFIG[
                "candidate_alert_cooldown"
            ]
        ):
            return

        self.candidate_alerts[
            address
        ] = time.time()

        count = (
            self.confirmation_count(
                address
            )
        )

        self.telegram.send(
            "👀 JARVIS WATCHING\n\n"
            f"{token['name']} "
            f"({token['symbol']})\n\n"
            f"Score: "
            f"{token['score']}/100\n"
            f"Confirmation: "
            f"{count}/"
            f"{CONFIG['required_confirmations']}\n\n"
            f"5m: "
            f"{token['change5']:+.2f}%\n"
            f"1h: "
            f"{token['change1h']:+.2f}%\n"
            f"Buy pressure: "
            f"{token['buy_pressure']:.1f}%\n\n"
            f"Liquidity: "
            f"${token['liquidity']:,.0f}\n"
            f"24h volume: "
            f"${token['volume24']:,.0f}\n"
            f"Market cap: "
            f"${token['market_cap']:,.0f}"
        )


    def buy(
        self,
        token
    ):

        if self.entries_paused:
            return

        if self.daily_target_hit():
            return

        if self.daily_loss_limit_hit():
            return

        if self.stop_loss_cooldown_active():
            return

        if self.daily_entries >= CONFIG["max_daily_entries"]:
            return

        if len(self.positions) >= CONFIG["max_open_positions"]:
            return

        address = token[
            "token_address"
        ]

        if address in self.positions:
            return

        if not self.token_reentry_allowed(
            address
        ):
            return

        if (
            self.confirmation_count(address)
            <
            CONFIG[
                "required_confirmations"
            ]
        ):
            return

        amount = self.position_size()

        if amount <= 0:
            return

        price = token["price"]

        if price <= 0:
            return

        quantity = (
            amount
            /
            price
        )

        self.positions[address] = {

            "symbol":
                token["symbol"],

            "name":
                token["name"],

            "token_address":
                address,

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
                token["score"],

            "opened_at":
                now_sydney().isoformat()
        }

        self.balance -= amount

        self.daily_entries += 1

        self.confirmations.pop(
            address,
            None
        )

        self.save_state()

        self.telegram.send(
            "🚀 JARVIS BALANCED ENTRY\n\n"
            f"{token['name']} "
            f"({token['symbol']})\n\n"
            "✅ 2-SCAN CONFIRMATION PASSED\n\n"
            f"Entry: "
            f"${price:.10f}\n"
            f"Position: "
            f"${amount:.2f}\n"
            f"Score: "
            f"{token['score']}/100\n"
            f"Buy pressure: "
            f"{token['buy_pressure']:.1f}%\n\n"
            f"Stop: "
            f"-{CONFIG['stop_loss_pct']}%\n"
            f"Target: "
            f"+{CONFIG['take_profit_pct']}%\n\n"
            "PAPER ONLY"
        )


    def update_positions(self):

        for address in list(
            self.positions.keys()
        ):

            if address not in self.positions:
                continue

            position = self.positions[
                address
            ]

            pair = self.dex.best_pair(
                address
            )

            if not pair:
                continue

            price = safe_float(
                pair.get(
                    "priceUsd"
                )
            )

            if price <= 0:
                continue

            position[
                "current_price"
            ] = price

            if (
                price
                >
                safe_float(
                    position.get(
                        "highest_price"
                    )
                )
            ):

                position[
                    "highest_price"
                ] = price

            entry = safe_float(
                position[
                    "entry_price"
                ]
            )

            if entry <= 0:
                continue

            pnl_pct = (
                (
                    price
                    -
                    entry
                )
                /
                entry
                *
                100
            )

            highest = safe_float(
                position[
                    "highest_price"
                ]
            )

            high_profit_pct = (
                (
                    highest
                    -
                    entry
                )
                /
                entry
                *
                100
            )

            drop_from_high = (
                (
                    highest
                    -
                    price
                )
                /
                highest
                *
                100
                if highest > 0
                else 0
            )

            print(
                position["symbol"],
                "| P/L",
                f"{pnl_pct:+.2f}%"
            )

            if (
                pnl_pct
                <=
                -CONFIG[
                    "stop_loss_pct"
                ]
            ):

                self.sell(
                    address,
                    "STOP LOSS"
                )

                continue

            if (
                pnl_pct
                >=
                CONFIG[
                    "take_profit_pct"
                ]
            ):

                self.sell(
                    address,
                    "TAKE PROFIT"
                )

                continue

            if (
                high_profit_pct
                >=
                CONFIG[
                    "trailing_activation_pct"
                ]
                and
                drop_from_high
                >=
                CONFIG[
                    "trailing_stop_pct"
                ]
            ):

                self.sell(
                    address,
                    "TRAILING STOP"
                )

                continue

            time.sleep(0.15)

        self.save_state()


    def sell(
        self,
        address,
        reason
    ):

        if address not in self.positions:
            return

        position = self.positions[
            address
        ]

        exit_price = safe_float(
            position[
                "current_price"
            ]
        )

        amount = safe_float(
            position[
                "amount"
            ]
        )

        quantity = safe_float(
            position[
                "quantity"
            ]
        )

        value = (
            quantity
            *
            exit_price
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
            if amount > 0
            else 0
        )

        self.balance += value
        self.daily_profit += profit

        self.trade_history.append({

            "symbol":
                position[
                    "symbol"
                ],

            "name":
                position[
                    "name"
                ],

            "token_address":
                address,

            "profit":
                profit,

            "pnl_pct":
                pnl_pct,

            "reason":
                reason,

            "closed_at":
                now_sydney()
                .isoformat()
        })

        self.token_last_exit[
            address
        ] = time.time()

        if reason == "STOP LOSS":

            self.last_stop_loss_time = (
                time.time()
            )

        del self.positions[
            address
        ]

        self.rebuild_stats()
        self.save_state()

        icon = (
            "🟢"
            if profit >= 0
            else "🔴"
        )

        self.telegram.send(
            f"{icon} PAPER TRADE CLOSED\n\n"
            f"{position['name']} "
            f"({position['symbol']})\n\n"
            f"Reason: "
            f"{reason}\n"
            f"Return: "
            f"{pnl_pct:+.2f}%\n"
            f"P/L: "
            f"${profit:+.2f}\n\n"
            f"Today's P/L: "
            f"${self.daily_profit:+.2f}\n"
            f"Equity: "
            f"${self.get_equity():,.2f}"
        )


    def try_new_entry(self):

        if self.daily_target_hit():
            return

        if self.daily_loss_limit_hit():
            return

        if self.entries_paused:
            return

        if self.stop_loss_cooldown_active():
            return

        if self.daily_entries >= CONFIG["max_daily_entries"]:
            return

        if len(self.positions) >= CONFIG["max_open_positions"]:
            return

        candidates = self.scan_market()

        if not candidates:

            self.confirmations = {}
            print(
                "No qualifying balanced tokens."
            )
            return

        self.update_confirmations(
            candidates
        )

        best = candidates[0]

        self.send_candidate_alert(
            best
        )

        for token in candidates:

            address = token[
                "token_address"
            ]

            if address in self.positions:
                continue

            if not self.token_reentry_allowed(
                address
            ):
                continue

            if (
                self.confirmation_count(
                    address
                )
                >=
                CONFIG[
                    "required_confirmations"
                ]
            ):

                self.buy(token)

                break


    def status_message(self):

        closed = (
            self.total_wins
            +
            self.total_losses
        )

        win_rate = (
            self.total_wins
            /
            closed
            *
            100
            if closed > 0
            else 0
        )

        if self.daily_loss_limit_hit():
            status = "🛑 DAILY LOSS STOP"

        elif self.entries_paused:
            status = "⏸ PAUSED"

        elif self.stop_loss_cooldown_active():

            status = (
                "⏳ COOLDOWN "
                f"{self.stop_loss_cooldown_remaining() / 60:.1f} MIN"
            )

        else:
            status = "🟢 ACTIVE"

        return (
            "🤖 JARVIS V5 BALANCED\n\n"
            f"Starting balance: "
            f"${CONFIG['starting_balance']:,.2f}\n"
            f"Cash: "
            f"${self.balance:,.2f}\n"
            f"Equity: "
            f"${self.get_equity():,.2f}\n\n"
            f"💰 Account P/L: "
            f"${self.account_profit():+,.2f}\n"
            f"Closed P/L: "
            f"${self.total_profit:+,.2f}\n"
            f"Open P/L: "
            f"${self.unrealized_profit():+,.2f}\n"
            f"Today's P/L: "
            f"${self.daily_profit:+,.2f}\n\n"
            f"🎯 Daily target: "
            f"${CONFIG['daily_profit_target']:,.2f}\n"
            f"Target progress: "
            f"{self.target_progress():.1f}%\n\n"
            f"Wins: "
            f"{self.total_wins}\n"
            f"Losses: "
            f"{self.total_losses}\n"
            f"Win rate: "
            f"{win_rate:.1f}%\n"
            f"Open trades: "
            f"{len(self.positions)}/"
            f"{CONFIG['max_open_positions']}\n"
            f"Today's entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n"
            f"Watching: "
            f"{len(self.confirmations)}\n\n"
            f"Daily loss used: "
            f"{self.daily_loss_pct():.2f}%/"
            f"{CONFIG['max_daily_loss_pct']:.2f}%\n\n"
            f"Status: "
            f"{status}\n"
            "Market data: LIVE\n"
            "Mode: PAPER TRADING"
        )


    def today_message(self):

        return (
            "📅 TODAY\n\n"
            f"P/L: "
            f"${self.daily_profit:+.2f}\n"
            f"Entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n"
            f"Watching: "
            f"{len(self.confirmations)}\n"
            f"Open trades: "
            f"{len(self.positions)}\n\n"
            f"Equity: "
            f"${self.get_equity():,.2f}"
        )


    def live_message(self):

        if not self.positions:

            return (
                "⚡ LIVE POSITIONS\n\n"
                "No open paper trades.\n\n"
                f"Equity: "
                f"${self.get_equity():,.2f}"
            )

        message = (
            "⚡ LIVE POSITIONS\n\n"
        )

        for position in self.positions.values():

            entry = safe_float(
                position[
                    "entry_price"
                ]
            )

            current = safe_float(
                position[
                    "current_price"
                ]
            )

            pnl = (
                (
                    current
                    -
                    entry
                )
                /
                entry
                *
                100
                if entry > 0
                else 0
            )

            value = (
                safe_float(
                    position[
                        "quantity"
                    ]
                )
                *
                current
            )

            pnl_dollars = (
                value
                -
                safe_float(
                    position[
                        "amount"
                    ]
                )
            )

            message += (
                f"{position['symbol']}\n"
                f"P/L: "
                f"{pnl:+.2f}%\n"
                f"P/L $: "
                f"${pnl_dollars:+.2f}\n\n"
            )

        message += (
            f"Open P/L: "
            f"${self.unrealized_profit():+,.2f}\n"
            f"Equity: "
            f"${self.get_equity():,.2f}"
        )

        return message


    def positions_message(self):

        if not self.positions:

            return (
                "📊 OPEN POSITIONS\n\n"
                "No open paper trades."
            )

        message = (
            "📊 OPEN POSITIONS\n\n"
        )

        for position in self.positions.values():

            message += (
                f"{position['symbol']}\n"
                f"Entry: "
                f"${safe_float(position['entry_price']):.10f}\n"
                f"Current: "
                f"${safe_float(position['current_price']):.10f}\n"
                f"Size: "
                f"${safe_float(position['amount']):.2f}\n"
                f"Score: "
                f"{safe_int(position.get('score'))}/100\n\n"
            )

        return message


    def trades_message(self):

        if not self.trade_history:

            return (
                "📜 RECENT TRADES\n\n"
                "No closed trades."
            )

        message = (
            "📜 RECENT TRADES\n\n"
        )

        for trade in reversed(
            self.trade_history[-10:]
        ):

            profit = safe_float(
                trade.get(
                    "profit"
                )
            )

            message += (
                f"{trade.get('symbol', 'UNKNOWN')}\n"
                f"{trade.get('reason', '')}\n"
                f"{safe_float(trade.get('pnl_pct')):+.2f}% | "
                f"${profit:+.2f}\n\n"
            )

        return message


    def stats_message(self):

        if not self.trade_history:

            return (
                "📈 STATISTICS\n\n"
                "No closed trades yet."
            )

        profits = [
            safe_float(
                trade.get(
                    "profit"
                )
            )
            for trade in self.trade_history
        ]

        wins = [
            p
            for p in profits
            if p >= 0
        ]

        losses = [
            p
            for p in profits
            if p < 0
        ]

        total = len(profits)

        win_rate = (
            len(wins)
            /
            total
            *
            100
            if total > 0
            else 0
        )

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))

        if gross_loss > 0:

            profit_factor = (
                gross_profit
                /
                gross_loss
            )

            pf_text = (
                f"{profit_factor:.2f}"
            )

        elif gross_profit > 0:

            pf_text = "∞"

        else:

            pf_text = "0.00"

        return (
            "📈 JARVIS STATISTICS\n\n"
            f"Trades: "
            f"{total}\n"
            f"Wins: "
            f"{len(wins)}\n"
            f"Losses: "
            f"{len(losses)}\n"
            f"Win rate: "
            f"{win_rate:.1f}%\n\n"
            f"Total P/L: "
            f"${sum(profits):+,.2f}\n"
            f"Best trade: "
            f"${max(profits):+,.2f}\n"
            f"Worst trade: "
            f"${min(profits):+,.2f}\n"
            f"Profit factor: "
            f"{pf_text}\n\n"
            f"Equity: "
            f"${self.get_equity():,.2f}"
        )


    def run(self):

        self.telegram.send(
            "🟢 JARVIS V5 BALANCED ONLINE\n\n"
            "Real Solana market data\n"
            "Execution: PAPER ONLY\n\n"
            f"Liquidity minimum: "
            f"${CONFIG['min_liquidity']:,.0f}\n"
            f"Volume minimum: "
            f"${CONFIG['min_volume_24h']:,.0f}\n"
            f"Buy pressure minimum: "
            f"{CONFIG['min_buy_pressure_pct']:.0f}%\n"
            f"Minimum score: "
            f"{CONFIG['min_score']}\n\n"
            "2-scan confirmation remains active."
        )

        last_price_update = 0

        while True:

            try:

                self.reset_day_if_needed()

                self.telegram.handle_commands(
                    self
                )

                current = time.time()

                if (
                    current
                    -
                    last_price_update
                    >=
                    CONFIG[
                        "price_update_seconds"
                    ]
                ):

                    self.update_positions()

                    last_price_update = (
                        current
                    )

                if (
                    current
                    -
                    self.last_scan
                    >=
                    CONFIG[
                        "scan_every_seconds"
                    ]
                ):

                    self.try_new_entry()

                    self.last_scan = (
                        current
                    )

                if (
                    current
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
                        current
                    )

                time.sleep(2)

            except KeyboardInterrupt:

                self.save_state()

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

                time.sleep(5)


if __name__ == "__main__":

    bot = JarvisBot()

    bot.run()
