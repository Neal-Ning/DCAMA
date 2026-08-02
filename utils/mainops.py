from dotenv import load_dotenv
import ast
import time
import os
import requests
from requests.auth import HTTPBasicAuth
import yfinance as yf
from utils.yfops import price_above_ma
from utils.mappings import *
from utils.botops import *
import numpy as np
import sqlite3
from datetime import datetime, timezone
import logging

# ===== Globals ===== #

# Environment variables
load_dotenv()
t212_api = os.getenv('T212P_API')
t212_sck = os.getenv('T212P_SCK')


logging.basicConfig(
    filename="database/app.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Trading212 URLs
base_url = "https://demo.trading212.com/api/v0/equity"
pos_url = f"{base_url}/positions"
account_summary_url = f"{base_url}/account/summary"
order_url = f"{base_url}/orders/market"
pending_order_url = f"{base_url}/orders"
order_history_url = f"{base_url}/history/orders?cursor=0&limit=10"

# Request session
session = requests.Session()
session.auth= HTTPBasicAuth(t212_api, t212_sck)

# ===== T212 API Helper ===== #

def do_t212_req(url, method="GET", max_retries=10, backoff=1, **kwargs): 
    for attempt in range(max_retries): 
        try: 
            r = session.request(method, url, timeout=10, **kwargs)

            # Too many requests, wait longer each time before resending
            if r.status_code == 429: 
                wait = backoff * (attempt + 1)
                logger.warning(
                    "Rate limited (429) on %s, attempt %d/%d, sleeping %ds",
                    url, attempt + 1, max_retries, wait,
                )
                time.sleep(wait)
                continue

            # Handle other forms of HTTP Error
            if not r.ok:
                try:
                    body = r.json()
                except ValueError:
                    body = r.text
                logger.error("HTTP %d for %s: %s", r.status_code, url, body)
                raise RuntimeError(
                    f"HTTP {r.status_code} for {url}: {body}"
                )

            return r.json()

        # Handle connection errors
        except(requests.Timeout, requests.ConnectionError) as e: 
            wait = backoff * (attempt + 1)
            logger.warning(
                "%s on %s, attempt %d/%d, retrying in %ds",
                type(e).__name__, url, attempt + 1, max_retries, wait,
            )
            time.sleep(wait)
            continue

    # When max retries have been exhausted
    logger.error("Failed after %d retries, url: %s", max_retries, url)
    raise RuntimeError(f"Failed after {max_retries} retries, url: {url}")


def check_pos(ticker=None): 
    t212_pos_obj = do_t212_req(pos_url, method="GET")
    logger.info("Fetched pos for %s", ticker)
    if not ticker: 
        return t212_pos_obj
    elif isinstance(ticker, str): 
        return [
            entry for entry in t212_pos_obj if entry.get("instrument").get("ticker") == ticker
        ]
    elif isinstance(ticker, list): 
        tickers = set(ticker)
        return [
            entry for entry in t212_pos_obj if entry.get("instrument").get("ticker") in tickers
        ]

# ===== Case when deposite ===== #
# (Call from user after deposite)

# Given a desposite, calculate the split and write out a message given the current state of each stock's ma
def deposite_calculation(deposite): 

    # Create a pie to hold the split values
    split_pie = {k:0 for k,_ in empty_pie.items()}
    update_pie = {k:0 for k,_ in accum.items()}

    # Handle the special case of QQQ3
    _, pama = price_above_ma("QQQ3.MI")
    amount = proportion["QQQ3.MI"] * deposite
    update_pie["QQQ3.MI"] = amount
    split_pie["QQQ3.MI" if pama else "SXRV.DE"] = amount

    # Handle rest of the splits
    for ticker in ["IUFS.L", "IUHC.L", "IGLD.DE", "ASWC.DE"]: 
        amount = proportion[ticker] * deposite
        update_pie[ticker] += amount
        pama = price_above_ma(ticker)[1]
        split_pie[ticker if pama else "CASH"] += amount

    message = "\n".join(f"Put €{v:.2f} in {k}" for k,v in split_pie.items() if v != 0)

    return {"msg": message, "to_db": update_pie}

# Increments each ticker's standing by the given amount - used for deposits,
# which add on top of whatever's already invested.
# Given a pie (must contain only the 5 items in db), add corresponding amount to db
def add_to_db(add_pie):
    conn = sqlite3.connect("database/accum.db")
    conn.executemany(
        "UPDATE standings SET amount = amount + ? WHERE ticker = ?", ((v,k) for k,v in add_pie.items())
    )
    conn.commit()
    conn.close()
    logger.info("Added amounts to entries in database")

# Overwrites (not adds to) each ticker's standing - used for reconciling
# against an actual sell/buy execution price, not for accumulating deposits.
def set_in_db(set_pie):
    conn = sqlite3.connect("database/accum.db")
    conn.executemany(
        "UPDATE standings SET amount = ? WHERE ticker = ?", ((v,k) for k,v in set_pie.items())
    )
    conn.commit()
    conn.close()
    logger.info("Changed amounts of entries in database")

# Given a deposite, calculate split, add to db, then message the user the amount to execute.
def deposite_allocation(deposite):
    result = deposite_calculation(deposite)
    add_to_db(result["to_db"])
    message = result["msg"]
    send_message(message)
    logger.info("Deposite allocation calculation done: %s", message)

# ===== Case when MA ===== #
# (Call from 10 am scheduled pama inspector)

def get_pama_n_close(empty_pie):
    # CASH and SXRV.DE aren't independently-tracked fields - CASH is just
    # where money sits, and SXRV.DE is only where QQQ3.MI's money sits while
    # QQQ3.MI itself is below its ma. pama_pie must have exactly the 5 fields
    # in `accum`/`standings`, or check_last's lookups against the `last`
    # table will miss keys.
    pama_pie = {k: False for k, _ in empty_pie.items() if k != "CASH" and k!= "SXRV.DE"}
    close_pie = {k: 0 for k, _ in empty_pie.items() if k != "CASH"}

    closeqqq, _ = price_above_ma("SXRV.DE")
    close_pie["SXRV.DE"] = closeqqq

    for ticker in ["QQQ3.MI", "IUFS.L", "IUHC.L", "IGLD.DE", "ASWC.DE"]: 
        close, pama = price_above_ma(ticker)
        pama_pie[ticker] = True if pama else False
        close_pie[ticker] = close

    return pama_pie, close_pie

# No confirmation button here: per spec, once the user reinvests after
# seeing this, there's nothing left for the app to update - the reserved
# amount was already tracked while the ticker was below its ma.
def event_pama(ticker):
    conn = sqlite3.connect("database/accum.db")
    amount = conn.execute(
        "SELECT amount FROM standings WHERE ticker = ?", (ticker, )
    ).fetchone()[0]
    conn.close()
    message =  f"Event pama on {ticker}, add €{amount:.2f}"
    send_message(message)

def event_pbma(ticker):
    # Stamped into the callback data so update_ticker can ignore any FILLED
    # sell order placed before this prompt - otherwise a premature "Done"
    # tap could match a stale, unrelated sell from an earlier pbma cycle.
    trigger_time = datetime.now(timezone.utc).timestamp()
    message = f"Event pbma, on {ticker}, move into low risk or cash, notify when done"
    send_buttons(message, buttons=[("Done", f"update_ticker:{ticker}:{trigger_time}")])

def check_last(truth_pie):
    # Get the last pama status
    conn = sqlite3.connect("database/accum.db")
    last = dict(conn.execute(
        "SELECT ticker, pama FROM last"
    ).fetchall())
    last = {k: bool(v) for k,v in last.items()}

    # Alert user of changes, persisting each ticker right after its own alert
    # goes out so a failure partway through doesn't re-notify tickers that
    # already succeeded on the next run.
    for k,v in truth_pie.items():
        if v and not last.get(k):
            event_pama(k)
        elif not v and last.get(k):
            event_pbma(k)
        else:
            continue

        conn.execute(
            "INSERT OR REPLACE INTO last (ticker, pama) VALUES (?, ?)",
            (k, v)
        )
        conn.commit()

    conn.close()


# ===== Case when set up ===== #
# (Call from user when first setting up)

def set_up(to_invest: int | None = None):
    # Destructive: wipes and reseeds standings/last, then reinvests
    # everything from scratch. Meant for first-time setup or an intentional
    # rebalance, not a routine command - there is no confirmation gate here.
    conn = sqlite3.connect("database/accum.db")
    conn.execute("DROP TABLE IF EXISTS standings")
    conn.execute("DROP TABLE IF EXISTS last")
    conn.commit()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS standings (
            ticker TEXT PRIMARY KEY NOT NULL, 
            amount REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS last (
            ticker TEXT PRIMARY KEY NOT NULL, 
            pama BOOL
        )
    """)
    conn.executemany(
        "INSERT OR REPLACE INTO standings (ticker, amount) VALUES (?, ?)", accum.items()
    )
    conn.executemany(
        "INSERT OR REPLACE INTO last (ticker, pama) VALUES (?, ?)", ((k, False) for k,_ in accum.items())
    )
    conn.commit()
    conn.close()
    
    pocket = to_invest if to_invest else do_t212_req(account_summary_url).get("totalValue")
    deposite_allocation(pocket)

# ===== Case checking positions ===== #

def update_ticker(yticker, after):

    history = do_t212_req(order_history_url)

    # Only consider sell orders filled at/after `after` (the pbma prompt's
    # trigger time) - see event_pbma for why that matters.
    matches = [
        entry for entry in history.get("items")
        if entry.get("order").get("status") == "FILLED"
        if entry.get("order").get("side") == "SELL"
        and entry.get("order").get("ticker") == yahoo_t212[yticker]
        and datetime.fromisoformat(entry.get("fill").get("filledAt")).timestamp() >= after
    ]

    # Get the newest one
    newest = max(
        matches, 
        key=lambda e: datetime.fromisoformat(e.get("fill").get("filledAt")), 
        default=None,
    )

    if newest == None: 
        send_message("Order not found, check and try again")
        logger.warning("Tried to find order to update %s, but order not found", yticker)
        return 1

    #Calculate from the fields in the variable newest
    amount = newest.get("fill").get("walletImpact").get("netValue")

    set_in_db({yticker: amount})

    send_message("Completed")
    logger.info("Found order and updated %s", yticker)

    return 0

    
# Text commands the user can send the bot: "/setup" or "/setup {amount}" to
# (re)initialize and invest, "/depo {amount}" to allocate a deposit, and
# "/setup {"TICKER": amount, ...}" as a fallback to directly overwrite one or
# more fields' tracked standings (e.g. to correct drift by hand, without
# touching the others or re-running the full invest-from-scratch flow).
# Anything else, or bad input, is reported back rather than raised - an
# uncaught exception here would take down the whole bot-listener thread.
@on_message
def handle_message(text, chat_id):
    command, _, arg = text.strip().partition(" ")
    arg = arg.strip()

    if command == "/setup":
        if arg.startswith("{"):
            # ast.literal_eval, not eval - this text comes straight from a
            # Telegram message, so only literals (dict/number/str) are safe.
            try:
                overrides = ast.literal_eval(arg)
            except (ValueError, SyntaxError):
                send_message('Usage: /setup {"TICKER": amount, ...}')
                return

            if not isinstance(overrides, dict) or not all(k in accum for k in overrides):
                send_message(f"Tickers must be one of: {', '.join(accum)}")
                return

            try:
                overrides = {k: float(v) for k, v in overrides.items()}
            except (TypeError, ValueError):
                send_message("All amounts must be numeric")
                return

            set_in_db(overrides)
            logger.info("Received /setup dict override: %s", overrides)
            send_message(
                "Standings set:\n" + "\n".join(f"{k} = €{v:.2f}" for k, v in overrides.items())
            )
            return

        try:
            amount = float(arg) if arg else None
        except ValueError:
            send_message("Usage: /setup or /setup {amount}")
            return
        if amount is not None and amount <= 0:
            send_message("Amount must be positive")
            return
        logger.info("Received /setup command, amount=%s", amount)
        set_up(amount)

    elif command == "/depo":
        try:
            amount = float(arg)
        except ValueError:
            send_message("Usage: /depo {amount}")
            return
        if amount <= 0:
            send_message("Amount must be positive")
            return
        logger.info("Received /depo command, amount=%s", amount)
        deposite_allocation(amount)


# callback_data is "update_ticker:{ticker}:{trigger_time}" - partitioned
# twice since the ticker itself never contains a colon.
@on_callback
def handle_callback(data, chat_id, message_id):
    action, _, arg = data.partition(":")
    if action == "update_ticker":
        ticker, _, trigger_time = arg.partition(":")
        if update_ticker(ticker, float(trigger_time)) == 0:
            remove_buttons(chat_id, message_id)