import os
import time
import json
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo


# ============================================================
# JARVIS LIVE-MARKET PAPER TRADING BOT
# $500 DAILY TARGET VERSION
# ============================================================

SYDNEY = ZoneInfo("Australia/Sydney")

DEX_BASE = "https://api.dexscreener.com"
STATE_FILE = "jarvis_state.json"


CONFIG = {

    # --------------------------------------------------------
    # PAPER ACCOUNT
    # --------------------------------------------------------

    "starting_balance": 1000.00,

    # Daily goal - PAPER ONLY
    "daily_profit_target": 500.00,

    # --------------------------------------------------------
    # RISK
    # --------------------------------------------------------

    "risk_per_trade_pct": 1.0,

    "max_position_pct": 10.0,

    "max_open_positions": 3,

    # --------------------------------------------------------
    # EXITS
    # --------------------------------------------------------

    "stop_loss_pct": 8.0,

    "take_profit_pct": 18.0,

    "trailing_activation_pct": 8.0,

    "trailing_stop_pct": 6.0,

    # --------------------------------------------------------
    # DAILY SAFETY
    # --------------------------------------------------------

    "max_daily_loss_pct": 5.0,

    "max_daily_entries": 30,

    # --------------------------------------------------------
    # TOKEN FILTERS
    # --------------------------------------------------------

    "min_liquidity": 25000,

    "min_volume_24h": 50000,

    "min_market_cap": 40000,

    "max_market_cap": 5000000,

    "min_5m_change": 2.0,

    "max_5m_change": 35.0,

    "min_1h_change": -5.0,

    "max_1h_change": 120.0,

    "min_5m_buys": 5,

    "min_score": 65,

    "min_pair_age_minutes": 3,

    "max_pair_age_days": 30,

    # --------------------------------------------------------
    # TIMING
    # --------------------------------------------------------

    "scan_every_seconds": 30,

    "price_update_seconds": 10,

    "status_every_seconds": 300,

    "max_tokens_per_scan": 25,

    # --------------------------------------------------------
    # MODE
    # --------------------------------------------------------

    "paper_mode": True
}


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


def http_json(
    url,
    method="GET",
    data=None,
    timeout=15
):

    try:

        headers = {
            "User-Agent": "JarvisPaperBot/2.0",
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

            text = (
                response
                .read()
                .decode("utf-8")
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

        self.api(
            "deleteWebhook",
            {
                "drop_pending_updates":
                    "false"
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
                "description": "Today's target progress"
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
                "description": "Commands"
            }
        ])

        self.api(
            "setMyCommands",
            {
                "commands":
                    commands
            }
        )


    def send(
        self,
        message
    ):

        if not self.token:
            return

        if not self.chat_id:
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
                updates[-1][
                    "update_id"
                ]
                + 1
            )

        return updates


    def handle_commands(
        self,
        bot
    ):

        for update in (
            self.get_updates()
        ):

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
                    "🤖 JARVIS ONLINE\n\n"
                    "Market data: LIVE\n"
                    "Execution: PAPER ONLY\n\n"
                    "Daily target: $500\n\n"
                    "/status\n"
                    "/today\n"
                    "/positions\n"
                    "/trades\n"
                    "/pause\n"
                    "/resume\n"
                    "/paper\n"
                    "/help"
                )


            elif command == "/status":

                self.send(
                    bot.status_message()
                )


            elif command == "/today":

                self.send(
                    bot.today_message()
                )


            elif command == "/positions":

                self.send(
                    bot.positions_message()
                )


            elif command == "/trades":

                self.send(
                    bot.trades_message()
                )


            elif command == "/pause":

                bot.entries_paused = True

                bot.save_state()

                self.send(
                    "⏸ JARVIS PAUSED\n\n"
                    "No new positions will open.\n"
                    "Existing positions will still be managed."
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
                    "REAL MARKET DATA\n"
                    "PAPER TRADING ONLY\n\n"
                    "No wallet is connected."
                )


            elif command == "/help":

                self.send(
                    "🤖 COMMANDS\n\n"
                    "/status\n"
                    "/today\n"
                    "/positions\n"
                    "/trades\n"
                    "/pause\n"
                    "/resume\n"
                    "/paper\n"
                    "/help"
                )


# ============================================================
# DEXSCREENER
# ============================================================

class DexScreener:

    def recent_profiles(self):

        url = (
            DEX_BASE
            +
            "/token-profiles/latest/v1"
        )

        result = http_json(url)

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
            DEX_BASE
            +
            "/token-pairs/v1/"
            +
            "solana/"
            +
            token_address
        )

        result = http_json(url)

        if isinstance(
            result,
            list
        ):

            return result

        return []


    def best_pair(
        self,
        token_address
    ):

        pairs = self.token_pairs(
            token_address
        )

        valid_pairs = []

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

            valid_pairs.append(
                pair
            )

        if not valid_pairs:

            return None

        valid_pairs.sort(
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

        return valid_pairs[0]


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

        self.target_alert_sent = False

        self.last_scan = 0

        self.last_status = 0

        self.load_state()

        self.reset_day_if_needed()


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

            self.target_alert_sent = bool(
                data.get(
                    "target_alert_sent",
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
                    self.trade_history[
                        -500:
                    ],

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
                    self.entries_paused,

                "target_alert_sent":
                    self.target_alert_sent
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


    # ========================================================
    # DAY RESET
    # ========================================================

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

        self.target_alert_sent = False

        self.save_state()

        self.telegram.send(
            "🌅 NEW PAPER TRADING DAY\n\n"
            f"Starting equity: "
            f"${self.day_start_equity:,.2f}\n"
            f"Daily target: "
            f"${CONFIG['daily_profit_target']:,.2f}"
        )


    # ========================================================
    # TARGET
    # ========================================================

    def daily_target_hit(self):

        return (
            self.daily_profit
            >=
            CONFIG[
                "daily_profit_target"
            ]
        )


    def target_progress(self):

        target = (
            CONFIG[
                "daily_profit_target"
            ]
        )

        if target <= 0:

            return 0.0

        progress = (
            self.daily_profit
            /
            target
            *
            100
        )

        return max(
            0,
            min(
                progress,
                100
            )
        )


    def check_target(self):

        if not self.daily_target_hit():
            return

        if self.target_alert_sent:
            return

        self.target_alert_sent = True

        self.save_state()

        self.telegram.send(
            "🎯 DAILY TARGET REACHED\n\n"
            f"Today's P/L: "
            f"${self.daily_profit:+,.2f}\n"
            f"Target: "
            f"${CONFIG['daily_profit_target']:,.2f}\n\n"
            "Jarvis will stop opening new positions "
            "for the rest of the day.\n\n"
            "Existing positions will still be managed."
        )


    # ========================================================
    # EQUITY
    # ========================================================

    def get_equity(self):

        open_value = 0.0

        for position in (
            self.positions.values()
        ):

            open_value += (
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

        return (
            self.balance
            +
            open_value
        )


    # ========================================================
    # DAILY LOSS
    # ========================================================

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
            (
                created
                /
                1000
            )
        ) / 60


    # ========================================================
    # NORMALIZE TOKEN
    # ========================================================

    def pair_to_token(
        self,
        pair
    ):

        base = pair.get(
            "baseToken",
            {}
        )

        price_change = pair.get(
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
                    price_change.get(
                        "m5"
                    )
                ),

            "change1h":
                safe_float(
                    price_change.get(
                        "h1"
                    )
                ),

            "buys5":
                safe_int(
                    m5.get(
                        "buys"
                    )
                ),

            "sells5":
                safe_int(
                    m5.get(
                        "sells"
                    )
                ),

            "age_minutes":
                self.pair_age_minutes(
                    pair
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


        if token["liquidity"] >= 250000:

            score += 20

        elif token["liquidity"] >= 100000:

            score += 18

        elif token["liquidity"] >= 50000:

            score += 14

        elif token["liquidity"] >= 25000:

            score += 10


        if token["volume24"] >= 1000000:

            score += 20

        elif token["volume24"] >= 500000:

            score += 18

        elif token["volume24"] >= 150000:

            score += 14

        elif token["volume24"] >= 50000:

            score += 10


        if 5 <= token["change5"] <= 15:

            score += 20

        elif 2 <= token["change5"] < 5:

            score += 14

        elif 15 < token["change5"] <= 30:

            score += 10


        if 5 <= token["change1h"] <= 40:

            score += 15

        elif 0 <= token["change1h"] < 5:

            score += 8


        total = (
            token["buys5"]
            +
            token["sells5"]
        )

        if total > 0:

            ratio = (
                token["buys5"]
                /
                total
            )

            if ratio >= 0.70:

                score += 20

            elif ratio >= 0.60:

                score += 15

            elif ratio >= 0.52:

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
            5000000
        ):

            score += 6


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

        if token["price"] <= 0:
            return False

        if (
            token["liquidity"]
            <
            CONFIG[
                "min_liquidity"
            ]
        ):
            return False

        if (
            token["volume24"]
            <
            CONFIG[
                "min_volume_24h"
            ]
        ):
            return False

        if (
            token["market_cap"]
            <
            CONFIG[
                "min_market_cap"
            ]
        ):
            return False

        if (
            token["market_cap"]
            >
            CONFIG[
                "max_market_cap"
            ]
        ):
            return False

        if (
            token["change5"]
            <
            CONFIG[
                "min_5m_change"
            ]
        ):
            return False

        if (
            token["change5"]
            >
            CONFIG[
                "max_5m_change"
            ]
        ):
            return False

        if (
            token["change1h"]
            <
            CONFIG[
                "min_1h_change"
            ]
        ):
            return False

        if (
            token["change1h"]
            >
            CONFIG[
                "max_1h_change"
            ]
        ):
            return False

        if (
            token["buys5"]
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
            CONFIG[
                "min_score"
            ]
        )


    # ========================================================
    # SCANNER
    # ========================================================

    def scan_market(self):

        profiles = (
            self.dex
            .recent_profiles()
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


            if (
                profile.get(
                    "chainId"
                )
                !=
                "solana"
            ):
                continue


            address = profile.get(
                "tokenAddress"
            )


            if not address:

                continue


            if address in seen:

                continue


            seen.add(
                address
            )

            checked += 1


            pair = (
                self.dex
                .best_pair(
                    address
                )
            )


            if not pair:

                continue


            token = (
                self.pair_to_token(
                    pair
                )
            )


            if self.valid_token(
                token
            ):

                candidates.append(
                    token
                )


            time.sleep(
                0.15
            )


        candidates.sort(
            key=lambda x:
                x[
                    "score"
                ],
            reverse=True
        )

        return candidates


    # ========================================================
    # POSITION SIZE
    # ========================================================

    def position_size(self):

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

        maximum = (
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
                maximum,
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

        if self.entries_paused:
            return

        if self.daily_target_hit():
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


        address = (
            token[
                "token_address"
            ]
        )


        if address in self.positions:

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
            address
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
                token[
                    "score"
                ],

            "opened_at":
                now_sydney()
                .isoformat()
        }


        self.balance -= amount

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

            f"Daily goal: "
            f"${CONFIG['daily_profit_target']:,.0f}"
        )


    # ========================================================
    # UPDATE POSITIONS
    # ========================================================

    def update_positions(self):

        for address in list(
            self.positions.keys()
        ):

            position = (
                self.positions[
                    address
                ]
            )


            pair = (
                self.dex
                .best_pair(
                    address
                )
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
                position[
                    "highest_price"
                ]
            ):

                position[
                    "highest_price"
                ] = price


            entry = (
                position[
                    "entry_price"
                ]
            )


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


            highest = (
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
            )


            print(
                position[
                    "symbol"
                ],
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


            time.sleep(
                0.15
            )


        self.save_state()


    # ========================================================
    # SELL
    # ========================================================

    def sell(
        self,
        address,
        reason
    ):

        if address not in self.positions:
            return


        position = (
            self.positions[
                address
            ]
        )


        exit_price = (
            position[
                "current_price"
            ]
        )


        value = (
            position[
                "quantity"
            ]
            *
            exit_price
        )


        profit = (
            value
            -
            position[
                "amount"
            ]
        )


        pnl_pct = (
            profit
            /
            position[
                "amount"
            ]
            *
            100
        )


        self.balance += value

        self.total_profit += profit

        self.daily_profit += profit


        if profit >= 0:

            self.total_wins += 1

            icon = "🟢"

        else:

            self.total_losses += 1

            icon = "🔴"


        self.trade_history.append({

            "symbol":
                position[
                    "symbol"
                ],

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


        del self.positions[
            address
        ]


        self.check_target()

        self.save_state()


        self.telegram.send(

            f"{icon} PAPER SNIPE CLOSED\n\n"

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

            f"Daily target: "
            f"${CONFIG['daily_profit_target']:,.2f}\n"

            f"Progress: "
            f"{self.target_progress():.1f}%\n\n"

            f"Equity: "
            f"${self.get_equity():,.2f}"
        )


    # ========================================================
    # STATUS
    # ========================================================

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


        if self.daily_target_hit():

            status = (
                "🎯 TARGET REACHED"
            )

        elif self.daily_loss_limit_hit():

            status = (
                "🛑 DAILY LOSS STOP"
            )

        elif self.entries_paused:

            status = (
                "⏸ PAUSED"
            )

        elif (
            self.daily_entries
            >=
            CONFIG[
                "max_daily_entries"
            ]
        ):

            status = (
                "🟡 ENTRY LIMIT"
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

    def today_message(self):

        remaining = max(
            0,
            CONFIG[
                "daily_profit_target"
            ]
            -
            self.daily_profit
        )


        return (

            "📅 TODAY\n\n"

            f"P/L: "
            f"${self.daily_profit:+.2f}\n"

            f"Target: "
            f"${CONFIG['daily_profit_target']:,.2f}\n"

            f"Progress: "
            f"{self.target_progress():.1f}%\n"

            f"Remaining: "
            f"${remaining:,.2f}\n\n"

            f"Entries: "
            f"{self.daily_entries}/"
            f"{CONFIG['max_daily_entries']}\n"

            f"Equity: "
            f"${self.get_equity():,.2f}\n\n"

            "Execution: PAPER ONLY"
        )


    # ========================================================
    # POSITIONS
    # ========================================================

    def positions_message(self):

        if not self.positions:

            return (
                "📊 OPEN POSITIONS\n\n"
                "No open paper trades."
            )


        message = (
            "📊 OPEN POSITIONS\n\n"
        )


        for position in (
            self.positions.values()
        ):

            entry = (
                position[
                    "entry_price"
                ]
            )

            current = (
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
            )


            message += (

                f"{position['symbol']}\n"

                f"Entry: "
                f"${entry:.10f}\n"

                f"Current: "
                f"${current:.10f}\n"

                f"P/L: "
                f"{pnl:+.2f}%\n"

                f"Size: "
                f"${position['amount']:.2f}\n\n"
            )


        return message


    # ========================================================
    # TRADES
    # ========================================================

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
    # ENTRY
    # ========================================================

    def try_new_entry(self):

        if self.daily_target_hit():
            return

        if self.daily_loss_limit_hit():
            return

        if self.entries_paused:
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

            if (
                token[
                    "token_address"
                ]
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

    def run(self):

        self.telegram.send(

            "🟢 JARVIS ONLINE\n\n"

            "Real Solana market data\n"

            "Execution: PAPER ONLY\n\n"

            f"Starting equity: "
            f"${self.get_equity():,.2f}\n"

            f"Daily paper target: "
            f"${CONFIG['daily_profit_target']:,.2f}\n\n"

            "Send /status anytime."
        )


        last_price_update = 0


        while True:

            try:

                self.reset_day_if_needed()

                self.telegram.handle_commands(
                    self
                )

                self.check_target()

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


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    bot = JarvisBot()

    bot.run()
