import asyncio
import logging
import os
import re
import sqlite3
import time

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from data import ANSWERS, get_answer, group_size

logging.basicConfig(level=logging.INFO)
load_dotenv()

ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)  # your Telegram user id (get it from @userinfobot)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports.db")

# 96-1-12 | 96 1 12 | 1996-1-12 | 96.1.12
CODE_RE = re.compile(r"(?<!\d)(\d{2}|\d{4})\s*[-–—./\s]\s*(\d{1,2})\s*[-–—./\s]\s*(\d{1,3})(?!\d)")


# ---------- database ----------
def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created INTEGER,
                user_id INTEGER,
                username TEXT,
                year TEXT, grp TEXT, q INTEGER,
                bot_said TEXT,      -- what the bot really answered
                user_says_bot TEXT, -- what the user says the bot said
                correct TEXT,       -- what the user says is correct
                status TEXT DEFAULT 'open'
            )"""
        )


def save_report(user, year, grp, q, shown, said, correct) -> int:
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT INTO reports (created,user_id,username,year,grp,q,bot_said,user_says_bot,correct)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (int(time.time()), user.id, user.username or user.full_name, year, grp, q, shown, said, correct),
        )
        return cur.lastrowid


# ---------- helpers ----------
def norm_year(y: str) -> str:
    if len(y) == 4:
        return y
    return ("19" if int(y) >= 90 else "20") + y


def letters_kb(prefix: str) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(l, callback_data=f"{prefix}:{l}") for l in "ABCDE"]
    return InlineKeyboardMarkup([row])


def short_code(year: str, grp: str, q) -> str:
    return f"{year[2:]}-{grp}-{q}"


# ---------- commands ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    years = ", ".join(sorted(ANSWERS))
    await update.message.reply_text(
        "Send a question code like 96-1-12 (year-group-question) and I'll reply with the answer.\n"
        f"Available years: {years}\n"
        "You can send several codes in one message.\n"
        "Think an answer is wrong? Press the ⚠️ Report button under it."
    )


async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    matches = CODE_RE.findall(update.message.text or "")
    if not matches:
        await update.message.reply_text("Send the code like this: 96-1-12")
        return

    lines, found = [], []
    for y, g, q in matches[:30]:
        year, group, num = norm_year(y), str(int(g)), int(q)
        ans = get_answer(year, group, num)
        if ans:
            lines.append(f"{y}-{g}-{q}  →  {ans}")
            found.append((year, group, num, ans))
        elif year not in ANSWERS:
            lines.append(f"{y}-{g}-{q}  →  year {year} is not in the database")
        elif group not in ANSWERS[year]:
            lines.append(f"{y}-{g}-{q}  →  group {group} not found in {year}")
        else:
            lines.append(f"{y}-{g}-{q}  →  no such question (group has {group_size(year, group)})")

    markup = None
    if found:
        rows = []
        for year, group, num, ans in found[:8]:
            label = "⚠️ Report a mistake" if len(found) == 1 else f"⚠️ Report {short_code(year, group, num)}"
            rows.append([InlineKeyboardButton(label, callback_data=f"rp:{year}:{group}:{num}:{ans}")])
        markup = InlineKeyboardMarkup(rows)

    await update.message.reply_text("\n".join(lines), reply_markup=markup)


# ---------- report flow (stateless: everything is kept in callback_data) ----------
async def on_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Pressed '⚠️ Report'  ->  ask: what did the bot say?"""
    q = update.callback_query
    await q.answer()
    _, year, grp, num, shown = q.data.split(":")
    await q.message.reply_text(
        f"Reporting {short_code(year, grp, num)}.\n\n1/2 — What did the bot say?",
        reply_markup=letters_kb(f"rs:{year}:{grp}:{num}:{shown}"),
    )


async def on_said(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Picked what the bot said  ->  ask: what is the correct answer?"""
    q = update.callback_query
    await q.answer()
    _, year, grp, num, shown, said = q.data.split(":")
    await q.edit_message_text(
        f"Reporting {short_code(year, grp, num)}.\nThe bot said: {said}\n\n2/2 — What is the correct answer?",
        reply_markup=letters_kb(f"rc:{year}:{grp}:{num}:{shown}:{said}"),
    )


async def on_correct(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Picked the correct answer  ->  save + notify admin."""
    q = update.callback_query
    await q.answer()
    _, year, grp, num, shown, said, correct = q.data.split(":")
    code = short_code(year, grp, num)

    if correct == shown:
        await q.edit_message_text(
            f"{code}: you picked {correct}, which is the same as the bot's answer ({shown}), so nothing to fix. "
            "If you think it is still wrong, press Report again."
        )
        return

    rid = save_report(q.from_user, year, grp, int(num), shown, said, correct)
    await q.edit_message_text(
        f"✅ Thanks! Report #{rid} saved.\n{code}: bot said {said}, you say the correct answer is {correct}."
    )

    if ADMIN_ID:
        who = f"@{q.from_user.username}" if q.from_user.username else q.from_user.full_name
        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"⚠️ Report #{rid} from {who}\n"
                f"Question: {code}\n"
                f"Bot answered: {shown}\n"
                f"User says the bot said: {said}\n"
                f"User says correct: {correct}",
            )
        except Exception as e:
            logging.warning("Could not notify admin: %s", e)


# ---------- admin ----------
async def reports_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            "SELECT id,year,grp,q,bot_said,correct,username FROM reports WHERE status='open' ORDER BY id DESC LIMIT 20"
        ).fetchall()
    if not rows:
        await update.message.reply_text("No open reports 🎉")
        return
    text = "\n".join(
        f"#{i}  {short_code(y, g, q)}  bot:{b} → user:{c}  ({u})" for i, y, g, q, b, c, u in rows
    )
    await update.message.reply_text(text + "\n\nMark fixed: /done <id>")


async def done_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not ctx.args or not ctx.args[0].isdigit():
        return
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE reports SET status='done' WHERE id=?", (int(ctx.args[0]),))
    await update.message.reply_text(f"Report #{ctx.args[0]} marked as done.")


def main():
    asyncio.set_event_loop(asyncio.new_event_loop())  # needed on Python 3.14
    init_db()
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("reports", reports_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(on_report, pattern=r"^rp:"))
    app.add_handler(CallbackQueryHandler(on_said, pattern=r"^rs:"))
    app.add_handler(CallbackQueryHandler(on_correct, pattern=r"^rc:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()


if __name__ == "__main__":
    main()