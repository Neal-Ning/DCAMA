from dotenv import load_dotenv
import os
import time
import requests

# ===== Globals ===== #

load_dotenv()
bot_api = os.getenv("BOT_API")

base_url = f"https://api.telegram.org/bot{bot_api}"
chat_id_file = "database/chat_id.txt"
offset_file = "database/update_offset.txt"

session = requests.Session()

# Handlers registered by mainops.py, called when the user sends something.
# Trading logic never touches Telegram directly - it only ever reacts to
# on_message/on_callback, keeping this module ignorant of tickers/amounts.
_message_handlers = []
_callback_handlers = []


# ===== Telegram API helper ===== #

# Thin wrapper around Telegram's HTTP API. Retries on rate limiting and
# transient network errors with linear backoff; anything else (bad request,
# invalid token, etc.) is raised immediately so callers don't silently swallow
# a real failure.
def do_bot_req(method, payload=None, timeout=15, max_retries=5, backoff=1):
    for attempt in range(max_retries):
        try:
            r = session.post(f"{base_url}/{method}", json=payload, timeout=timeout)

            # Telegram's own rate limit - back off and retry rather than fail.
            if r.status_code == 429:
                time.sleep(backoff * (attempt + 1))
                continue

            if not r.ok:
                try:
                    body = r.json()
                except ValueError:
                    body = r.text
                raise RuntimeError(f"HTTP {r.status_code} for {method}: {body}")

            return r.json()

        except (requests.Timeout, requests.ConnectionError):
            time.sleep(backoff * (attempt + 1))
            continue

    raise RuntimeError(f"Failed after {max_retries} retries, method: {method}")


# ===== Chat id persistence ===== #
# The bot has no chat to message until the user has sent it something at
# least once. Once learned, the chat id is cached on disk so restarts don't
# need a fresh message from the user.

def _load_chat_id():
    if os.path.exists(chat_id_file):
        with open(chat_id_file) as f:
            content = f.read().strip()
            return int(content) if content else None
    return None


def _save_chat_id(chat_id):
    with open(chat_id_file, "w") as f:
        f.write(str(chat_id))


def get_chat_id():
    return _load_chat_id()


# ===== Update offset persistence ===== #
# Telegram only stops re-sending an update once we call getUpdates with an
# offset past it, so this needs to survive a crash/restart - otherwise every
# update since the last confirmed offset gets redelivered and reprocessed.

def _load_offset():
    if os.path.exists(offset_file):
        content = open(offset_file).read().strip()
        return int(content) if content else None
    return None


def _save_offset(offset):
    with open(offset_file, "w") as f:
        f.write(str(offset))


# ===== Sending messages ===== #

def send_message(text, chat_id=None):
    chat_id = chat_id or get_chat_id()
    if chat_id is None:
        raise RuntimeError(
            "No chat_id known yet - the user needs to message the bot at least once."
        )
    return do_bot_req("sendMessage", {"chat_id": chat_id, "text": text})

# Edits an existing message's text in place, rather than sending a new one.
def edit_message(chat_id, message_id, text):
    return do_bot_req("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    })

def send_buttons(text, buttons, chat_id=None):
    """
    Send a message with inline callback buttons.

    buttons: either a flat list of (label, callback_data) pairs (one button
    per row), or a list of rows, where each row is a list of (label,
    callback_data) pairs.
    """
    chat_id = chat_id or get_chat_id()
    if chat_id is None:
        raise RuntimeError(
            "No chat_id known yet - the user needs to message the bot at least once."
        )

    if buttons and isinstance(buttons[0], tuple):
        rows = [[button] for button in buttons]
    else:
        rows = buttons

    inline_keyboard = [
        [{"text": label, "callback_data": data} for label, data in row]
        for row in rows
    ]

    return do_bot_req("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {"inline_keyboard": inline_keyboard},
    })

# Strips the inline keyboard once its action has been handled, so the user
# can't submit the same button twice.
def remove_buttons(chat_id, message_id):
    try:
        return do_bot_req("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        })
    except RuntimeError as e:
        # Already-stripped keyboard (e.g. a double-tapped button) - Telegram
        # rejects this as a no-op edit, which isn't a real failure.
        if "message is not modified" in str(e).lower():
            return None
        raise

# ===== Receiving messages ===== #
# mainops.py registers handlers here to get notified of whatever the user sends.

def on_message(func):
    """Decorator/registration: func(text, chat_id) called on plain text messages."""
    _message_handlers.append(func)
    return func


def on_callback(func):
    """Decorator/registration: func(data, chat_id) called on button presses."""
    _callback_handlers.append(func)
    return func


def _handle_update(update):
    if "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        if get_chat_id() != chat_id:
            _save_chat_id(chat_id)

        text = message.get("text", "")
        for handler in _message_handlers:
            handler(text, chat_id)

    elif "callback_query" in update:
        callback = update["callback_query"]
        chat_id = callback["message"]["chat"]["id"]
        message_id = callback["message"]["message_id"]
        if get_chat_id() != chat_id:
            _save_chat_id(chat_id)

        # Required by Telegram to clear the button's loading spinner on the
        # client, independent of whatever our own handlers do below.
        do_bot_req("answerCallbackQuery", {"callback_query_id": callback["id"]})

        data = callback.get("data")
        for handler in _callback_handlers:
            handler(data, chat_id, message_id)


def listen(poll_timeout=30):
    """
    Block forever, long-polling Telegram for updates and dispatching them to
    whatever handlers mainops.py registered via on_message / on_callback.
    """
    offset = _load_offset()
    announced = False

    # Send exactly one startup announcement: immediately if we already know
    # the chat id from a previous run, otherwise as soon as the first batch
    # below teaches us the chat id (e.g. a message sent while we were down).
    if get_chat_id() is not None:
        send_message("Bot Started")
        announced = True

    while True:
        params = {"timeout": poll_timeout}
        if offset is not None:
            params["offset"] = offset

        result = do_bot_req("getUpdates", params, timeout=poll_timeout + 10)

        for update in result.get("result", []):
            offset = update["update_id"] + 1
            _handle_update(update)
            # Persist right after each update is handled (not once per batch)
            # so a crash mid-batch only risks replaying the one update being
            # processed, not every update already handled before it.
            _save_offset(offset)

        if not announced and get_chat_id() is not None:
            send_message("Bot Started")
            announced = True


if __name__ == "__main__":
    listen()
