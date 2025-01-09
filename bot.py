#!/usr/bin/env python3
import os
import yaml
import re
import time
import subprocess
import asyncio
from threading import Thread
from aiomcrcon import Client
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
import logging

with open("config.yaml", "r") as config_file:
    config = yaml.safe_load(config_file)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
RCON_PASSWORD = os.getenv("RCON_PASSWORD")

if not TELEGRAM_BOT_TOKEN or not RCON_PASSWORD:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or RCON_PASSWORD environment variables.")

RCON_HOST = config["rcon"]["host"]
RCON_PORT = config["rcon"]["port"]

BACKUP_PATH = config["paths"]["backup_path"]
WORLD_PATH = config["paths"]["world_path"]
LOG_PATH = config["paths"]["log_path"]

TELEGRAM_CHAT_ID = config["telegram"]["chat_id"]
AUTHORIZED_USER_IDS = config["telegram"]["authorized_user_ids"]

if not TELEGRAM_BOT_TOKEN or not RCON_PASSWORD:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN or RCON_PASSWORD in config.yaml.")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

RESTORE_BACKUP_SELECT = 1


async def send_rcon_command(command: str) -> tuple[str, int] | str:
    try:
        async with Client(RCON_HOST, RCON_PORT, RCON_PASSWORD) as mcr:
            resp = await mcr.send_cmd(command)
            return resp
    except Exception as e:
        return f"Error sending RCON command: {e}"


def is_authorized(user_id: int) -> bool:
    return user_id in AUTHORIZED_USER_IDS


def list_backup_files() -> list:
    try:
        files = [
            f for f in os.listdir(BACKUP_PATH)
            if os.path.isfile(os.path.join(BACKUP_PATH, f)) and f.endswith(('.zip', '.tar.gz', '.tar.bz2'))
        ]
        files.sort(key=lambda x: os.path.getmtime(os.path.join(BACKUP_PATH, x)), reverse=True)
        return files
    except Exception as e:
        logger.error(f"Error listing backup files: {e}")
        return []


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я подключен к серверу Minecraft. Используй /help."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    commands = [
        "/help - Посмотреть это сообщение",
        "/status - Узнать состояние сервера",
        "/stop - Остановить сервер",
        "/run - Запустить сервер",
        "/restart - Перезапустить сервер",
        "/restorebackup - Восстановить сервер из бэкапа",
    ]
    await update.message.reply_text("\n".join(commands))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = subprocess.run(
        ["systemctl", "is-active", "minecraft.service"],
        capture_output=True,
        text=True,
    )
    if status.stdout.strip() == "active":
        await update.message.reply_text("✅ Сервер сейчас работает.")
    else:
        await update.message.reply_text("❌ Сервер в отключке.")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("🚫У тебя тут нет власти, уходи.")
        return
    subprocess.run(["sudo", "systemctl", "stop", "minecraft.service"])
    await update.message.reply_text("⚙️ Сервер пытается остановиться...")


async def start_minecraft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("🚫У тебя тут нет власти, уходи.")
        return
    subprocess.run(["sudo", "systemctl", "start", "minecraft.service"])
    await update.message.reply_text("⚙️ Сервер запускается...")


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("🚫У тебя тут нет власти, уходи.")
        return
    subprocess.run(["sudo", "systemctl", "restart", "minecraft.service"])
    await update.message.reply_text("🔄 Сервер перезапускается!")


async def restore_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("🚫У тебя тут нет власти, уходи.")
        return ConversationHandler.END

    backups = list_backup_files()
    if not backups:
        await update.message.reply_text("❌ Резервные копии где-то потерялись.")
        return ConversationHandler.END

    message = "📦 Смотри, что нашлось:\n"
    for idx, backup in enumerate(backups, start=1):
        message += f"{idx}. <code>{backup}</code>\n"

    message += "\nРаньше было лучше... но когда? Ответь цифрой."

    await update.message.reply_text(message, parse_mode="HTML")
    context.user_data['backups'] = backups

    return RESTORE_BACKUP_SELECT


async def backup_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("🚫У тебя тут нет власти, уходи.")
        return ConversationHandler.END

    backups = context.user_data.get('backups', [])
    if not backups:
        await update.message.reply_text("❌ Резервные копии снова куда-то пропали.")
        return ConversationHandler.END

    try:
        selection = int(update.message.text)
        if 1 <= selection <= len(backups):
            selected_backup = backups[selection - 1]
            await update.message.reply_text(f"✅ Ты выбрал: {selected_backup}")
        else:
            await update.message.reply_text("❌ Неправильный выбор. Попробуй снова через /restorebackup.")
            return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Введи нормальную цифру, пожалуйста.")
        return ConversationHandler.END

    backup_path = os.path.join(BACKUP_PATH, selected_backup)
    await update.message.reply_text("⚙️ Сейчас всё будет!...")
    stop_result = subprocess.run(
        ["sudo", "systemctl", "stop", "minecraft.service"],
        capture_output=True,
        text=True,
    )
    if stop_result.returncode == 0:
        await update.message.reply_text("🔒 Сервер отключен.")
    else:
        await update.message.reply_text("❌ Не удалось остановить сервер. Восстановление отменяется.")
        return ConversationHandler.END

    await asyncio.sleep(2)

    try:
        subprocess.run(["rm", "-rf", WORLD_PATH], check=True)
        await update.message.reply_text("🗑️ Мир уничтожен, как и планировалось.")
    except subprocess.CalledProcessError as e:
        await update.message.reply_text(f"❌ Удалить мир не получилось: {e}")
        return ConversationHandler.END

    await asyncio.sleep(2)

    try:
        unzip_result = subprocess.run(
            ["unzip", backup_path, "-d", os.path.dirname(WORLD_PATH)],
            capture_output=True,
            text=True,
        )
        if unzip_result.returncode == 0:
            await update.message.reply_text("📦 Бэкап успешно восстановлен.")
        else:
            await update.message.reply_text(f"❌ Не удалось восстановить бэкап: {unzip_result.stderr}")
            return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка восстановления: {e}")
        return ConversationHandler.END

    await asyncio.sleep(2)

    start_result = subprocess.run(
        ["sudo", "systemctl", "start", "minecraft.service"],
        capture_output=True,
        text=True,
    )
    if start_result.returncode == 0:
        await update.message.reply_text("✅ Сервер снова в строю хе-хе.")
    else:
        await update.message.reply_text("❌ Сервер не запустился. Посмотри логи!")

    return ConversationHandler.END


async def cancel_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Восстановление бэкапа отменено. Уф!")
    return ConversationHandler.END


async def forward_to_minecraft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if update.message.from_user.is_bot:
        return
    if update.effective_chat.id != TELEGRAM_CHAT_ID:
        return

    if update.message.text:
        msg = update.message.text
    elif update.message.caption:
        msg = update.message.caption
    else:
        msg_type = update.message.effective_attachment.__class__.__name__ if update.message.effective_attachment else "Non-text"
        msg = f"[{msg_type} message]"

    msg = msg.replace('"', '\\"').replace('\n', '\\n')

    response = await send_rcon_command(
        f'tellraw @a ["<{update.message.from_user.full_name}> ",{{"text":"{msg}"}}]'
    )

    if "Error" in response:
        logger.error(response)
        await update.message.reply_text("Failed to send message to Minecraft server")


async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("🚫У тебя тут нет власти, уходи.")
        return

    command = update.message.text[1:]
    response = await send_rcon_command(command)
    if "Error" in response:
        logger.error(response)
        await update.message.reply_text("Failed to send command to Minecraft server")
    else:
        await update.message.reply_text(f"Будет исполнено!")


async def handle_non_command_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await forward_to_minecraft(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ An error occurred. Please check the logs."
            )
        except Exception as e:
            logger.error(f"Failed to send error message to chat: {e}")


def monitor_minecraft_log(application, chat_id: int, loop):
    chat_regex = re.compile(r'^\[\d{2}:\d{2}:\d{2}\] \[Server thread\/INFO\]: <([^>]+)> (.+)$')

    try:
        with open(LOG_PATH, "r") as f:
            f.seek(0, 2)
            current_inode = os.fstat(f.fileno()).st_ino

            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.5)
                    if os.stat(LOG_PATH).st_ino != current_inode:
                        logger.info("Log file rotated, reopening...")
                        break
                    continue

                match = chat_regex.match(line.strip())
                if match:
                    player, msg = match.groups()
                    if not msg.startswith("<Telegram>"):
                        try:
                            asyncio.run_coroutine_threadsafe(
                                application.bot.send_message(chat_id=chat_id, text=f"<{player}> {msg}"),
                                loop
                            )
                        except Exception as e:
                            logger.error(f"Error forwarding message to Telegram: {e}")
    except Exception as e:
        logger.error(f"Error monitoring Minecraft log: {e}")


def run_bot():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    restore_backup_conv = ConversationHandler(
        entry_points=[
            CommandHandler('restorebackup', restore_backup_command, filters=filters.Chat(chat_id=TELEGRAM_CHAT_ID))],
        states={
            RESTORE_BACKUP_SELECT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & filters.Chat(chat_id=TELEGRAM_CHAT_ID),
                    backup_selection
                )
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel_restore, filters=filters.Chat(chat_id=TELEGRAM_CHAT_ID))],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start, filters=filters.Chat(chat_id=TELEGRAM_CHAT_ID), block=False))
    application.add_handler(
        CommandHandler("help", help_command, filters=filters.Chat(chat_id=TELEGRAM_CHAT_ID), block=False))
    application.add_handler(
        CommandHandler("status", status_command, filters=filters.Chat(chat_id=TELEGRAM_CHAT_ID), block=False))
    application.add_handler(
        CommandHandler("stop", stop_command, filters=filters.Chat(chat_id=TELEGRAM_CHAT_ID), block=False))
    application.add_handler(
        CommandHandler("run", start_minecraft, filters=filters.Chat(chat_id=TELEGRAM_CHAT_ID), block=False))
    application.add_handler(
        CommandHandler("restart", restart_command, filters=filters.Chat(chat_id=TELEGRAM_CHAT_ID), block=False))
    application.add_handler(restore_backup_conv)
    application.add_handler(MessageHandler(
        filters.COMMAND & filters.Chat(chat_id=TELEGRAM_CHAT_ID),
        handle_unknown_command, block=False))
    application.add_handler(MessageHandler(
        filters.ALL & filters.Chat(chat_id=TELEGRAM_CHAT_ID) & ~filters.COMMAND,
        handle_non_command_message, block=False))
    application.add_error_handler(error_handler)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    monitor_thread = Thread(target=monitor_minecraft_log, args=(application, TELEGRAM_CHAT_ID, loop), daemon=True)
    monitor_thread.start()
    logger.info("Started Minecraft log monitoring thread.")
    application.run_polling()


if __name__ == "__main__":
    run_bot()
