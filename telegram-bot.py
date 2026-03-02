#!/usr/bin/env python3
"""
Telegram bot that connects to Claude Code CLI.

Setup:
1. pip install -r requirements.txt
2. Get bot token from @BotFather on Telegram
3. Get your user ID from @userinfobot on Telegram
4. Copy .env.example to .env and fill in values
5. Run: python telegram-bot.py
"""

import subprocess
import json
import os
import html
import logging
from pathlib import Path
import mistune

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Load .env file if it exists
ENV_FILE = Path(__file__).parent / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

# Configuration
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_USERS = [int(x) for x in os.environ.get("ALLOWED_USERS", "").split(",") if x]
WORKSPACE = os.environ.get("CLAUDE_WORKSPACE", str(Path.home()))
CLAUDE_PATH = os.environ.get("CLAUDE_PATH", "claude")
SESSION_FILE = Path.home() / ".telegram-claude-sessions.json"


def load_sessions() -> dict:
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text())
    return {}


def save_sessions(sessions: dict):
    SESSION_FILE.write_text(json.dumps(sessions, indent=2))


class TelegramRenderer(mistune.HTMLRenderer):
    """Custom renderer for Telegram-compatible HTML."""

    def heading(self, text, level, **attrs):
        return f"<b>{text}</b>\n\n"

    def paragraph(self, text):
        return f"{text}\n\n"

    def list(self, text, ordered, **attrs):
        return text + "\n"

    def list_item(self, text, **attrs):
        return f"• {text}\n"

    def block_code(self, code, info=None):
        escaped = html.escape(code.strip())
        return f"<pre>{escaped}</pre>\n\n"

    def codespan(self, text):
        return f"<code>{html.escape(text)}</code>"

    def emphasis(self, text):
        return f"<i>{text}</i>"

    def strong(self, text):
        return f"<b>{text}</b>"

    def strikethrough(self, text):
        return f"<s>{text}</s>"

    def link(self, text, url, title=None):
        return f'<a href="{html.escape(url)}">{text}</a>'

    def image(self, text, url, title=None):
        return f"[Image: {text}]"

    def block_quote(self, text):
        return f"<blockquote>{text}</blockquote>\n"

    def thematic_break(self):
        return "\n---\n\n"

    def linebreak(self):
        return "\n"

    def table(self, text):
        return f"<pre>{text}</pre>\n\n"

    def table_head(self, text):
        return text + "─" * 20 + "\n"

    def table_body(self, text):
        return text

    def table_row(self, text):
        return text + "\n"

    def table_cell(self, text, align=None, head=False):
        if head:
            return f"<b>{text}</b> │ "
        return f"{text} │ "


# Create markdown parser with GFM support
md = mistune.create_markdown(
    renderer=TelegramRenderer(escape=False),
    plugins=["strikethrough", "table", "task_lists", "url"],
)


def markdown_to_telegram_html(text: str) -> str:
    """Convert GitHub-flavored markdown to Telegram-compatible HTML."""
    result = md(text)
    return result.strip()


CONTEXT_PROMPT = """First, silently read CLAUDE.md for context.
Then respond to: """


def run_claude(message: str, session_id: str = None) -> tuple[str, str]:
    """Run Claude and return (response, new_session_id). Handles expired sessions."""
    cmd = [
        CLAUDE_PATH,
        "-p",
        message,
        "--output-format",
        "json",
        "--allowedTools",
        "Read,Write,Edit,Bash,Glob,Grep,WebFetch,WebSearch,Task,Skill",
    ]

    if session_id:
        cmd.extend(["--resume", session_id])

    log.info(f"Running Claude (session={session_id or 'new'})")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=WORKSPACE)

    try:
        data = json.loads(result.stdout)
        response = data.get("result", "No response")
        new_session_id = data.get("session_id")
        log.info(f"Claude responded (session={new_session_id}, chars={len(response)})")
        return response, new_session_id
    except json.JSONDecodeError:
        error_text = result.stdout or result.stderr or ""
        if "No conversation found" in error_text or "session" in error_text.lower():
            log.warning(f"Session {session_id} expired or not found, retrying without session")
            return None, None  # Signal to retry without session
        log.error(f"Claude error: {error_text[:200]}")
        return error_text or "Error running Claude", None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.full_name
    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        log.warning(f"Unauthorized access attempt from user_id={user_id} ({username})")
        await update.message.reply_text("Not authorized.")
        return

    message = update.message.text
    log.info(f"Message from {username} (id={user_id}): {message[:80]!r}{'...' if len(message) > 80 else ''}")
    sessions = load_sessions()
    session_id = sessions.get(str(user_id))

    await update.message.chat.send_action("typing")

    if session_id:
        response, new_session_id = run_claude(message, session_id)
        if response is None:
            log.info(f"Cleared expired session for user_id={user_id}")
            del sessions[str(user_id)]
            save_sessions(sessions)
            session_id = None

    if not session_id:
        full_message = CONTEXT_PROMPT + message
        response, new_session_id = run_claude(full_message)

    if new_session_id:
        sessions[str(user_id)] = new_session_id
        save_sessions(sessions)

    response = markdown_to_telegram_html(response)

    if not response or not response.strip():
        response = "(No response from Claude)"

    # Telegram has 4096 char limit
    if len(response) > 4000:
        response = response[:4000] + "\n\n... (truncated)"

    await update.message.reply_text(response, parse_mode="HTML")


async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sessions = load_sessions()
    if str(user_id) in sessions:
        del sessions[str(user_id)]
        save_sessions(sessions)
    await update.message.reply_text("Session cleared. Next message starts fresh.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sessions = load_sessions()
    has_session = str(user_id) in sessions
    await update.message.reply_text(
        f"User ID: {user_id}\nActive session: {'Yes' if has_session else 'No'}"
    )


def main():
    if not BOT_TOKEN:
        print("Set TELEGRAM_BOT_TOKEN in .env file")
        return

    if not ALLOWED_USERS:
        print("Set ALLOWED_USERS in .env file")
        return

    log.info("Starting bot...")
    log.info(f"Workspace: {WORKSPACE}")
    log.info(f"Allowed users: {ALLOWED_USERS or 'Everyone (set ALLOWED_USERS to restrict)'}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("new", new_session))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot running. Send messages on Telegram.")
    app.run_polling()


if __name__ == "__main__":
    main()
