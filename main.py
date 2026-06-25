import discord
from discord.ext import commands
import json
import os
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("AntiRaid")

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=config.get("prefix", "!"),
    intents=intents,
    help_command=None
)

# 載入所有 Cog
COGS = [
    "cogs.antiraid",
    "cogs.admin",
    "cogs.logger",
]

@bot.event
async def on_ready():
    logger.info(f"機器人已上線：{bot.user} (ID: {bot.user.id})")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="🛡️保護伺服器中"
        )
    )
    await bot.tree.sync()
    logger.info("斜線指令已同步")

async def main():
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                logger.info(f"已載入模組：{cog}")
            except Exception as e:
                logger.error(f"載入模組失敗 {cog}：{e}")
        try:
            await bot.start(config["token"])
        finally:
            with open("bot.log", "a", encoding="utf-8") as f:
                f.write("-" * 60 + "\n")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
