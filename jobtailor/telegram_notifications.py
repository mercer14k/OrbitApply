"""Optional Telegram alerts. Credentials stay in memory, outside job exports."""

import json
import re
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock


class TelegramError(RuntimeError):
    pass


class TelegramAPI:
    def __init__(self, token):
        token = str(token).strip()
        if not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,100}", token):
            raise TelegramError("Paste the complete bot token from BotFather.")
        self._token = token

    def call(self, method, payload=None):
        if method not in {"getMe", "getWebhookInfo", "getUpdates", "sendMessage"}:
            raise TelegramError("Unsupported Telegram request.")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.loads(response.read(512_000).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Raw HTTP errors contain the token in their URL. Never display them.
            messages = {
                401: "Telegram rejected the bot token. Check it with BotFather.",
                403: "Telegram blocked delivery. Open your bot and press Start or unblock it.",
                409: "This bot is used by another app. Create a separate bot for OrbitApply.",
                429: "Telegram is rate limiting messages. This alert was not confirmed.",
            }
            raise TelegramError(
                messages.get(exc.code, "Telegram could not complete the request.")
            ) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise TelegramError(
                "Telegram could not be reached or delivery could not be confirmed. Check your internet connection."
            ) from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise TelegramError(
                "Telegram did not confirm the request. Check your bot connection."
            )
        return result.get("result")


@dataclass(frozen=True)
class TelegramTarget:
    token: str = field(repr=False)
    chat_id: int
    name: str
    bot_username: str


@dataclass
class TelegramPairing:
    token: str = field(repr=False)
    bot_username: str
    code: str = field(default_factory=lambda: secrets.token_urlsafe(24), repr=False)
    expires: float = field(default_factory=lambda: time.monotonic() + 600)
    offset: int = 0

    @property
    def url(self):
        return f"https://t.me/{self.bot_username}?start={self.code}"


def begin_pairing(token):
    api = TelegramAPI(token)
    bot = api.call("getMe")
    if (
        not isinstance(bot, dict)
        or not bot.get("is_bot")
        or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", str(bot.get("username", "")))
    ):
        raise TelegramError("Telegram did not return a valid bot. Check your token.")
    webhook = api.call("getWebhookInfo")
    if not isinstance(webhook, dict) or webhook.get("url"):
        raise TelegramError(
            "This bot is connected to another service. Create a separate bot for OrbitApply."
        )
    return TelegramPairing(token=str(token).strip(), bot_username=bot["username"])


def finish_pairing(pairing):
    if time.monotonic() > pairing.expires:
        raise TelegramError(
            "The connection link expired. Click Connect Telegram to get a new one."
        )
    api = TelegramAPI(pairing.token)
    # Bounded pagination handles old messages without selecting a random chat.
    for _ in range(2):
        updates = api.call(
            "getUpdates",
            {
                "offset": pairing.offset,
                "limit": 100,
                "timeout": 0,
                "allowed_updates": ["message"],
            },
        )
        if not isinstance(updates, list):
            raise TelegramError("Telegram returned an invalid connection response.")
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                pairing.offset = max(pairing.offset, update_id + 1)
            message = update.get("message") or {}
            chat, sender = message.get("chat") or {}, message.get("from") or {}
            valid_commands = {
                f"/start {pairing.code}",
                f"/start@{pairing.bot_username} {pairing.code}",
            }
            if (
                str(message.get("text", "")).strip() in valid_commands
                and chat.get("type") == "private"
                and type(chat.get("id")) is int
                and chat["id"] > 0
                and sender.get("id") == chat["id"]
                and not sender.get("is_bot")
            ):
                name = chat.get("first_name") or chat.get("username") or "Your Telegram"
                return TelegramTarget(
                    pairing.token, chat["id"], str(name), pairing.bot_username
                )
        if len(updates) < 100:
            break
    return None


def folder_message(row, completed):
    folder = str(row.get("folder") or "")
    exists = bool(folder) and Path(folder).is_dir()
    heading = (
        "Folder created"
        if completed and exists
        else "Folder created; review needed"
        if exists
        else "Folder could not be completed"
    )

    def short(value, limit):
        value = str(value or "").strip()
        return value if len(value) <= limit else value[: limit - 1] + "…"

    lines = [
        heading,
        f"Role: {short(row.get('role'), 250)}",
        f"Company: {short(row.get('company'), 200)}",
        f"Status: {short(row.get('status'), 180)}",
    ]
    if exists:
        lines += [
            f"Folder on your computer: {short(folder, 700)}",
            "Review the resume and STATUS.txt before applying.",
        ]
    elif completed:
        lines.append("The saved folder is no longer available. Check the app.")
    # Do not forward raw exception/model/JD text to Telegram.
    url = str(row.get("url") or "")
    if url.startswith(("https://", "http://")):
        lines.append(f"Apply: {short(url, 700)}")
    return "\n".join(lines)


class TelegramNotifications:
    """Session-owned connection shared safely with notification workers."""

    def __init__(self):
        self._lock = Lock()
        self._target = None
        self._enabled = False
        self._generation = 0

    def connect(self, target):
        with self._lock:
            self._target = target
            self._enabled = True
            self._generation += 1

    def disconnect(self):
        with self._lock:
            self._target = None
            self._enabled = False
            self._generation += 1

    def set_enabled(self, enabled):
        with self._lock:
            enabled = bool(enabled and self._target)
            if enabled != self._enabled:
                self._generation += 1
            self._enabled = enabled

    def snapshot(self):
        with self._lock:
            return {
                "connected": self._target is not None,
                "enabled": self._enabled,
                "name": self._target.name if self._target else "",
                "bot_username": self._target.bot_username if self._target else "",
            }

    def capture(self):
        with self._lock:
            return self._generation if self._enabled and self._target else None

    def send(self, generation, row, completed):
        with self._lock:
            if generation != self._generation or not self._enabled or not self._target:
                return "Skipped: Telegram alerts were turned off or disconnected."
            target = self._target
        # One bounded request, with no automatic retry after ambiguous delivery.
        TelegramAPI(target.token).call(
            "sendMessage",
            {
                "chat_id": target.chat_id,
                "text": folder_message(row, completed),
                "link_preview_options": {"is_disabled": True},
            },
        )
        return "Telegram alert sent"
