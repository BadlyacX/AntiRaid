import discord
from discord.ext import commands
from datetime import datetime, timezone
import logging

logger = logging.getLogger("AntiRaid.Logger")


class Logger(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        from utils.permissions import load_config
        cfg = load_config()
        ch_name = cfg.get("log_channel_name", "bot-log")
        return discord.utils.get(guild.text_channels, name=ch_name)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        ch = await self._get_log_channel(member.guild)
        if not ch:
            return
        embed = discord.Embed(
            title="📥 成員加入",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="用戶", value=f"{member} (`{member.id}`)", inline=False)
        embed.add_field(name="帳號建立時間", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        embed.add_field(name="目前成員數", value=str(member.guild.member_count), inline=True)
        await ch.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        ch = await self._get_log_channel(member.guild)
        if not ch:
            return
        embed = discord.Embed(
            title="📤 成員離開/被踢",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="用戶", value=f"{member} (`{member.id}`)", inline=False)
        await ch.send(embed=embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        ch = await self._get_log_channel(channel.guild)
        if not ch:
            return
        embed = discord.Embed(
            title="🗑️ 頻道被刪除",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="頻道名稱", value=channel.name, inline=True)
        embed.add_field(name="頻道類型", value=str(channel.type), inline=True)
        await ch.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Logger(bot))
