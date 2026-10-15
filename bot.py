import os
import re
import asyncio  # add this to the imports at the top
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from data import ANSWERS, get_answer, group_size

load_dotenv()

# 96-1-12 | 96 1 12 | 1996-1-12 | 96.1.12
CODE_RE = re.compile(r"(?<!\d)(\d{2}|\d{4})\s*[-–—./\s]\s*(\d{1,2})\s*[-–—./\s]\s*(\d{1,3})(?!\d)")


def norm_year(y: str) -> str:
    if len(y) == 4:
        return y
    return ("19" if int(y) >= 90 else "20") + y


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    years = ", ".join(sorted(ANSWERS))
    await update.message.reply_text(
        "Send a question code like 96-1-12 (year-group-question) and I'll reply with the answer.\n"
        f"Available years: {years}\n"
        "You can send several codes in one message."
    )


async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    matches = CODE_RE.findall(update.message.text or "")
    if not matches:
        await update.message.reply_text("Send the code like this: 96-1-12")
        return

    lines = []
    for y, g, q in matches[:30]:
        year, group, num = norm_year(y), str(int(g)), int(q)
        ans = get_answer(year, group, num)
        if ans:
            lines.append(f"{y}-{g}-{q}  →  {ans}")
        elif year not in ANSWERS:
            lines.append(f"{y}-{g}-{q}  →  year {year} is not in the database")
        elif group not in ANSWERS[year]:
            lines.append(f"{y}-{g}-{q}  →  group {group} not found in {year}")
        else:
            lines.append(f"{y}-{g}-{q}  →  no such question (group has {group_size(year, group)})")
    await update.message.reply_text("\n".join(lines))


def main():
    asyncio.set_event_loop(asyncio.new_event_loop())  # fixes the Python 3.14 error
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()


if __name__ == "__main__":
    main()
