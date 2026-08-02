import logging
import signal
import sys
import threading
from datetime import datetime, timedelta

import utils.mainops as mainops
from utils.botops import listen
from utils.mappings import empty_pie

logger = logging.getLogger(__name__)

# Time of day (server local time) to run the pama check. yfinance's daily
# close is only reliable once markets have shut, so keep this late enough
# to have that day's bar available.
CHECK_HOUR = 12
CHECK_MINUTE = 0

# stop_event tells both threads to shut down, whether from a signal or a
# crash. crashed records *why*, so main() can exit non-zero only on a real
# failure - a clean signal-triggered shutdown shouldn't trigger a restart.
stop_event = threading.Event()
crashed = threading.Event()


def run_pama_check():
    pama_pie, _ = mainops.get_pama_n_close(empty_pie)
    mainops.check_last(pama_pie)


def next_run_time(now, hour, minute):
    # If today's slot has already passed (e.g. we started up after
    # CHECK_HOUR), roll over to tomorrow instead of firing immediately.
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def pama_scheduler():
    try:
        while not stop_event.is_set():
            now = datetime.now()
            target = next_run_time(now, CHECK_HOUR, CHECK_MINUTE)

            # wait() returns True the instant stop_event is set, so a
            # shutdown interrupts the sleep instead of waiting it out.
            if stop_event.wait((target - now).total_seconds()):
                break

            if datetime.now().weekday() < 5:  # Monday-Friday only
                try:
                    run_pama_check()
                except Exception:
                    # An isolated bad check (stale data, network blip)
                    # shouldn't kill the scheduler - just try again tomorrow.
                    logger.exception("Scheduled pama check failed")
    except Exception:
        # Anything escaping the loop itself is unexpected; go down loudly
        # instead of leaving a scheduler thread that silently stopped ticking.
        logger.exception("pama-scheduler thread crashed, forcing shutdown")
        crashed.set()
        stop_event.set()


def bot_listener():
    try:
        listen()
    except Exception:
        # listen() only ever returns via an exception, so any escape here
        # means Telegram I/O is broken. Bring the whole process down rather
        # than silently lose the ability to receive user commands while the
        # scheduler thread keeps running and looks fine.
        logger.exception("bot-listener thread crashed, forcing shutdown")
        crashed.set()
        stop_event.set()


def handle_signal(signum, frame):
    logger.info("Received signal %d, shutting down", signum)
    stop_event.set()


def main():
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # daemon=True so a SIGTERM/SIGINT exit doesn't hang waiting on a thread
    # that's mid-sleep or blocked in a long-poll.
    threads = [
        threading.Thread(target=bot_listener, name="bot-listener", daemon=True),
        threading.Thread(target=pama_scheduler, name="pama-scheduler", daemon=True),
    ]
    for t in threads:
        t.start()

    # Idle until a shutdown is requested (signal) or a thread dies unexpectedly
    # (each thread sets stop_event itself from its own except block above).
    while not stop_event.is_set():
        stop_event.wait(5)

    for t in threads:
        t.join(timeout=5)

    # Non-zero only on an actual crash, so systemd's Restart=on-failure (or
    # equivalent) doesn't restart the process on a clean signal shutdown.
    if crashed.is_set():
        sys.exit(1)


if __name__ == "__main__":
    main()
