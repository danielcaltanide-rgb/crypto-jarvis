import time
import random
import json
from datetime import datetime

# ============================================================
# JARVIS MEME COIN PAPER TRADING BOT
# Educational / testing version
# ============================================================

CONFIG = {
    # Starting simulated balance
    "starting_balance": 1000.00,

    # Risk controls
    "risk_per_trade_pct": 1.0,      # risk 1% of balance
    "max_position_pct": 10.0,       # never put >10% balance in one trade
    "max_open_trades": 3,

    # Exit rules
    "stop_loss_pct": 8.0,
    "take_profit_pct": 18.0,
    "trailing_stop_pct": 7.0,

    # Scanner filters
    "min_liquidity": 20_000,
    "min_volume_24h": 30_000,
    "min_market_cap": 30_000,
    "max_market_cap": 5_000_000,

    # Momentum requirements
    "min_price_change_5m": 2.0,
    "min_score": 65,

    # Safety
    "max_daily_loss_pct": 5.0,
    "cooldown_seconds": 10,

    # Paper mode stays TRUE
    "paper_mode": True
}


class JarvisBot:

    def __init__(self):
        self.balance = CONFIG["starting_balance"]
        self.starting_day_balance = self.balance

        self.positions = {}
        self.trade_history = []

        self.total_profit = 0
        self.total_wins = 0
        self.total_losses = 0

        print("=" * 55)
        print("        JARVIS MEME COIN TRADING BOT")
        print("=" * 55)
        print("MODE: PAPER TRADING")
        print(f"STARTING BALANCE: ${self.balance:,.2f}")
        print("=" * 55)

    # --------------------------------------------------------
    # DEMO MARKET SCANNER
    # --------------------------------------------------------

    def scan_market(self):

        # These are simulated tokens.
        # Later this function can be replaced by real market data.

        symbols = [
            "BONK",
            "WIF",
            "POPCAT",
            "MEW",
            "MOON",
            "PEPE2",
            "DOGGO",
            "CAT",
            "MEME",
            "ROCKET"
        ]

        opportunities = []

        for symbol in symbols:

            price = random.uniform(0.00001, 0.05)
            liquidity = random.uniform(5_000, 500_000)
            volume = random.uniform(5_000, 1_000_000)
            market_cap = random.uniform(10_000, 8_000_000)

            change_5m = random.uniform(-10, 20)
            change_1h = random.uniform(-20, 50)

            buys = random.randint(10, 500)
            sells = random.randint(5, 400)

            token = {
                "symbol": symbol,
                "price": price,
                "liquidity": liquidity,
                "volume": volume,
                "market_cap": market_cap,
                "change_5m": change_5m,
                "change_1h": change_1h,
                "buys": buys,
                "sells": sells
            }

            token["score"] = self.calculate_score(token)

            if self.valid_token(token):
                opportunities.append(token)

        opportunities.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        return opportunities

    # --------------------------------------------------------
    # TOKEN QUALITY SCORE
    # --------------------------------------------------------

    def calculate_score(self, token):

        score = 0

        # Liquidity
        if token["liquidity"] > 100_000:
            score += 20
        elif token["liquidity"] > 50_000:
            score += 15
        elif token["liquidity"] > 20_000:
            score += 10

        # Volume
        if token["volume"] > 500_000:
            score += 20
        elif token["volume"] > 100_000:
            score += 15
        elif token["volume"] > 30_000:
            score += 10

        # 5 minute momentum
        if token["change_5m"] > 10:
            score += 20
        elif token["change_5m"] > 5:
            score += 15
        elif token["change_5m"] > 2:
            score += 10

        # 1 hour trend
        if token["change_1h"] > 20:
            score += 15
        elif token["change_1h"] > 5:
            score += 10

        # Buy/sell pressure
        if token["buys"] > token["sells"] * 1.5:
            score += 20
        elif token["buys"] > token["sells"]:
            score += 10

        # Market cap preference
        if 50_000 <= token["market_cap"] <= 1_000_000:
            score += 10
        elif token["market_cap"] <= 5_000_000:
            score += 5

        return min(score, 100)

    # --------------------------------------------------------
    # FILTER BAD SETUPS
    # --------------------------------------------------------

    def valid_token(self, token):

        if token["liquidity"] < CONFIG["min_liquidity"]:
            return False

        if token["volume"] < CONFIG["min_volume_24h"]:
            return False

        if token["market_cap"] < CONFIG["min_market_cap"]:
            return False

        if token["market_cap"] > CONFIG["max_market_cap"]:
            return False

        if token["change_5m"] < CONFIG["min_price_change_5m"]:
            return False

        if token["score"] < CONFIG["min_score"]:
            return False

        return True

    # --------------------------------------------------------
    # POSITION SIZE
    # --------------------------------------------------------

    def calculate_position_size(self):

        risk_amount = (
            self.balance *
            CONFIG["risk_per_trade_pct"] / 100
        )

        stop_fraction = CONFIG["stop_loss_pct"] / 100

        position_size = risk_amount / stop_fraction

        maximum_position = (
            self.balance *
            CONFIG["max_position_pct"] / 100
        )

        position_size = min(
            position_size,
            maximum_position
        )

        return max(position_size, 0)

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    def buy(self, token):

        symbol = token["symbol"]

        if symbol in self.positions:
            return

        if len(self.positions) >= CONFIG["max_open_trades"]:
            return

        amount = self.calculate_position_size()

        if amount <= 0:
            return

        if amount > self.balance:
            return

        price = token["price"]

        quantity = amount / price

        position = {
            "symbol": symbol,
            "entry_price": price,
            "current_price": price,
            "quantity": quantity,
            "amount": amount,
            "highest_price": price,
            "score": token["score"],
            "opened": datetime.now().isoformat()
        }

        self.balance -= amount
        self.positions[symbol] = position

        print()
        print("🚀 PAPER BUY")
        print(f"Token: {symbol}")
        print(f"Score: {token['score']}/100")
        print(f"Entry: ${price:.8f}")
        print(f"Position: ${amount:.2f}")
        print(f"Balance: ${self.balance:.2f}")

    # --------------------------------------------------------
    # SIMULATE PRICE MOVEMENT
    # --------------------------------------------------------

    def update_positions(self):

        for symbol in list(self.positions.keys()):

            position = self.positions[symbol]

            movement = random.uniform(-0.08, 0.10)

            new_price = (
                position["current_price"] *
                (1 + movement)
            )

            position["current_price"] = new_price

            if new_price > position["highest_price"]:
                position["highest_price"] = new_price

            entry = position["entry_price"]

            pnl_pct = (
                (new_price - entry) /
                entry *
                100
            )

            trailing_drop = (
                (
                    position["highest_price"] -
                    new_price
                )
                /
                position["highest_price"]
                *
                100
            )

            print(
                f"{symbol} | "
                f"PnL {pnl_pct:+.2f}% | "
                f"${new_price:.8f}"
            )

            # Take profit

            if pnl_pct >= CONFIG["take_profit_pct"]:
                self.sell(symbol, "TAKE PROFIT")
                continue

            # Stop loss

            if pnl_pct <= -CONFIG["stop_loss_pct"]:
                self.sell(symbol, "STOP LOSS")
                continue

            # Trailing stop only after trade is profitable

            if (
                pnl_pct > 5
                and
                trailing_drop >= CONFIG["trailing_stop_pct"]
            ):
                self.sell(symbol, "TRAILING STOP")

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    def sell(self, symbol, reason):

        position = self.positions[symbol]

        exit_price = position["current_price"]

        value = (
            position["quantity"] *
            exit_price
        )

        profit = value - position["amount"]

        pnl_pct = (
            profit /
            position["amount"] *
            100
        )

        self.balance += value
        self.total_profit += profit

        if profit >= 0:
            self.total_wins += 1
        else:
            self.total_losses += 1

        trade = {
            "symbol": symbol,
            "entry": position["entry_price"],
            "exit": exit_price,
            "profit": profit,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "closed": datetime.now().isoformat()
        }

        self.trade_history.append(trade)

        del self.positions[symbol]

        print()
        print("💰 PAPER SELL")
        print(f"Token: {symbol}")
        print(f"Reason: {reason}")
        print(f"PnL: {pnl_pct:+.2f}%")
        print(f"Profit: ${profit:+.2f}")
        print(f"Balance: ${self.balance:.2f}")
        print()

        self.save_results()

    # --------------------------------------------------------
    # DAILY LOSS PROTECTION
    # --------------------------------------------------------

    def daily_loss_limit_hit(self):

        equity = self.get_equity()

        loss_pct = (
            (
                self.starting_day_balance -
                equity
            )
            /
            self.starting_day_balance
            *
            100
        )

        return loss_pct >= CONFIG["max_daily_loss_pct"]

    # --------------------------------------------------------
    # ACCOUNT EQUITY
    # --------------------------------------------------------

    def get_equity(self):

        position_value = 0

        for position in self.positions.values():

            position_value += (
                position["quantity"] *
                position["current_price"]
            )

        return self.balance + position_value

    # --------------------------------------------------------
    # DASHBOARD
    # --------------------------------------------------------

    def dashboard(self):

        equity = self.get_equity()

        trades = self.total_wins + self.total_losses

        if trades > 0:
            win_rate = (
                self.total_wins /
                trades *
                100
            )
        else:
            win_rate = 0

        print()
        print("=" * 55)
        print("JARVIS DASHBOARD")
        print("=" * 55)

        print(f"Cash:          ${self.balance:,.2f}")
        print(f"Equity:        ${equity:,.2f}")
        print(f"Total P/L:     ${self.total_profit:+,.2f}")

        print(
            f"Wins/Losses:   "
            f"{self.total_wins}/"
            f"{self.total_losses}"
        )

        print(f"Win Rate:      {win_rate:.1f}%")
        print(f"Open Trades:   {len(self.positions)}")

        print("=" * 55)

    # --------------------------------------------------------
    # SAVE TRADES
    # --------------------------------------------------------

    def save_results(self):

        data = {
            "balance": self.balance,
            "equity": self.get_equity(),
            "total_profit": self.total_profit,
            "wins": self.total_wins,
            "losses": self.total_losses,
            "trades": self.trade_history
        }

        with open(
            "jarvis_results.json",
            "w"
        ) as file:

            json.dump(
                data,
                file,
                indent=4
            )

    # --------------------------------------------------------
    # MAIN BOT LOOP
    # --------------------------------------------------------

    def run(self):

        print()
        print("🤖 Jarvis started.")
        print("Scanning for meme coin opportunities...")
        print()

        while True:

            try:

                if self.daily_loss_limit_hit():

                    print()
                    print(
                        "🛑 DAILY LOSS LIMIT HIT."
                    )

                    print(
                        "Jarvis has stopped opening new trades."
                    )

                    self.update_positions()
                    self.dashboard()

                    time.sleep(60)

                    continue

                # Manage current positions

                if self.positions:
                    self.update_positions()

                # Find new opportunities

                opportunities = self.scan_market()

                if opportunities:

                    print()
                    print(
                        f"🔎 Found "
                        f"{len(opportunities)} "
                        f"potential setups"
                    )

                    for token in opportunities[:3]:

                        print(
                            f"{token['symbol']} | "
                            f"Score {token['score']} | "
                            f"5m {token['change_5m']:+.1f}% | "
                            f"Liq ${token['liquidity']:,.0f}"
                        )

                    # Buy strongest candidate

                    best = opportunities[0]

                    self.buy(best)

                else:

                    print(
                        "No setup currently meets "
                        "the strategy rules."
                    )

                self.dashboard()

                time.sleep(
                    CONFIG["cooldown_seconds"]
                )

            except KeyboardInterrupt:

                print()
                print("Jarvis stopped.")

                self.save_results()

                break

            except Exception as error:

                print(
                    "ERROR:",
                    error
                )

                time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    bot = JarvisBot()

    bot.run()
