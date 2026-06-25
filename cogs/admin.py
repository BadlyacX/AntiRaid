import discord
from discord import app_commands
from discord.ext import commands
import json
import logging

from utils.permissions import (
    is_bot_admin, load_config, save_config,
    add_admin_role, remove_admin_role,
    add_whitelist_role, remove_whitelist_role,
    add_whitelist_user, remove_whitelist_user
)

logger = logging.getLogger("AntiRaid.Admin")


def admin_check():
    """斜線指令用的權限檢查"""
    async def predicate(interaction: discord.Interaction) -> bool:
        if not is_bot_admin(interaction.user):
            await interaction.response.send_message(
                "❌ 你沒有管理機器人的權限。", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /status ────────────────────────────────────────────

    @app_commands.command(name="status", description="顯示防炸群機器人目前的設定狀態")
    @admin_check()
    async def status(self, interaction: discord.Interaction):
        cfg = load_config()
        ar = cfg.get("antiraid", {})
        perms = cfg.get("permissions", {})

        def toggle(key: str) -> str:
            return "✅ 啟用" if ar.get(key, {}).get("enabled") else "❌ 停用"

        embed = discord.Embed(
            title="🛡️ AntiRaid 狀態",
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="偵測模組",
            value=(
                f"訊息速率限制：{toggle('message_rate')}\n"
                f"跨頻道重複訊息：{toggle('cross_channel_spam')}\n"
                f"頻道建立速率：{toggle('channel_creation_rate')}\n"
                f"角色建立速率：{toggle('role_creation_rate')}\n"
                f"頻道刪除速率：{toggle('channel_deletion_rate')}\n"
                f"角色刪除速率：{toggle('role_deletion_rate')}\n"
                f"大量踢出偵測：{toggle('mass_kick')}\n"
                f"大量封禁偵測：{toggle('mass_ban')}\n"
                f"角色提權防護：{toggle('permission_guard')}\n"
                f"大量提及偵測：{toggle('mass_mention')}\n"
                f"關鍵字黑名單：{toggle('suspicious_keywords')}"
            ),
            inline=False
        )

        # 白名單角色
        wl_roles = []
        for rid in perms.get("whitelist_roles", []):
            role = interaction.guild.get_role(rid)
            wl_roles.append(role.mention if role else f"<失效角色 {rid}>")

        # 白名單用戶
        wl_users = [f"<@{uid}>" for uid in perms.get("whitelist_users", [])]

        embed.add_field(
            name="白名單角色",
            value=", ".join(wl_roles) if wl_roles else "（無）",
            inline=False
        )
        embed.add_field(
            name="白名單用戶",
            value=", ".join(wl_users) if wl_users else "（無）",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /whitelist ──────────────────────────────────────────

    whitelist_group = app_commands.Group(name="whitelist", description="白名單管理")

    @whitelist_group.command(name="add_role", description="將角色加入白名單")
    @admin_check()
    async def wl_add_role(self, interaction: discord.Interaction, role: discord.Role):
        add_whitelist_role(role.id)
        await interaction.response.send_message(
            f"✅ 已將 {role.mention} 加入白名單。", ephemeral=True
        )

    @whitelist_group.command(name="remove_role", description="從白名單移除角色")
    @admin_check()
    async def wl_remove_role(self, interaction: discord.Interaction, role: discord.Role):
        remove_whitelist_role(role.id)
        await interaction.response.send_message(
            f"✅ 已從白名單移除 {role.mention}。", ephemeral=True
        )

    @whitelist_group.command(name="add_user", description="將用戶加入白名單")
    @admin_check()
    async def wl_add_user(self, interaction: discord.Interaction, user: discord.Member):
        add_whitelist_user(user.id)
        await interaction.response.send_message(
            f"✅ 已將 {user.mention} 加入白名單。", ephemeral=True
        )

    @whitelist_group.command(name="remove_user", description="從白名單移除用戶")
    @admin_check()
    async def wl_remove_user(self, interaction: discord.Interaction, user: discord.Member):
        remove_whitelist_user(user.id)
        await interaction.response.send_message(
            f"✅ 已從白名單移除 {user.mention}。", ephemeral=True
        )

    # ── /admin ──────────────────────────────────────────────

    admin_group = app_commands.Group(name="admin", description="機器人管理員角色設定")

    @admin_group.command(name="add_role", description="授予角色管理機器人的權限")
    @admin_check()
    async def admin_add_role(self, interaction: discord.Interaction, role: discord.Role):
        add_admin_role(role.id)
        await interaction.response.send_message(
            f"✅ 已授予 {role.mention} 管理機器人的權限。", ephemeral=True
        )

    @admin_group.command(name="remove_role", description="撤銷角色管理機器人的權限")
    @admin_check()
    async def admin_remove_role(self, interaction: discord.Interaction, role: discord.Role):
        remove_admin_role(role.id)
        await interaction.response.send_message(
            f"✅ 已撤銷 {role.mention} 管理機器人的權限。", ephemeral=True
        )

    # ── /set ────────────────────────────────────────────────

    set_group = app_commands.Group(name="set", description="調整防炸群參數")

    @set_group.command(name="msg_rate", description="設定訊息速率閾值")
    @admin_check()
    async def set_msg_rate(
        self,
        interaction: discord.Interaction,
        max_messages: int,
        interval_seconds: int
    ):
        cfg = load_config()
        cfg["antiraid"]["message_rate"]["max_messages"] = max_messages
        cfg["antiraid"]["message_rate"]["interval_seconds"] = interval_seconds
        save_config(cfg)
        await interaction.response.send_message(
            f"✅ 訊息速率設為：{interval_seconds} 秒內最多 {max_messages} 則。",
            ephemeral=True
        )

    @set_group.command(name="keyword_add", description="新增可疑關鍵字")
    @admin_check()
    async def set_keyword_add(self, interaction: discord.Interaction, keyword: str):
        cfg = load_config()
        kws = cfg["antiraid"]["suspicious_keywords"]["keywords"]
        if keyword not in kws:
            kws.append(keyword)
            save_config(cfg)
            await interaction.response.send_message(
                f"✅ 已新增關鍵字：`{keyword}`", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⚠️ 關鍵字 `{keyword}` 已存在。", ephemeral=True
            )

    @set_group.command(name="keyword_remove", description="移除可疑關鍵字")
    @admin_check()
    async def set_keyword_remove(self, interaction: discord.Interaction, keyword: str):
        cfg = load_config()
        kws = cfg["antiraid"]["suspicious_keywords"]["keywords"]
        if keyword in kws:
            kws.remove(keyword)
            save_config(cfg)
            await interaction.response.send_message(
                f"✅ 已移除關鍵字：`{keyword}`", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ 找不到關鍵字：`{keyword}`", ephemeral=True
            )

    # ── /toggle ─────────────────────────────────────────────

    @app_commands.command(name="toggle", description="開啟/關閉特定偵測模組")
    @admin_check()
    @app_commands.choices(module=[
        app_commands.Choice(name="訊息速率限制", value="message_rate"),
        app_commands.Choice(name="跨頻道重複訊息", value="cross_channel_spam"),
        app_commands.Choice(name="頻道建立速率", value="channel_creation_rate"),
        app_commands.Choice(name="角色建立速率", value="role_creation_rate"),
        app_commands.Choice(name="頻道刪除速率", value="channel_deletion_rate"),
        app_commands.Choice(name="角色刪除速率", value="role_deletion_rate"),
        app_commands.Choice(name="大量踢出偵測", value="mass_kick"),
        app_commands.Choice(name="大量封禁偵測", value="mass_ban"),
        app_commands.Choice(name="角色提權防護", value="permission_guard"),
        app_commands.Choice(name="大量提及偵測", value="mass_mention"),
        app_commands.Choice(name="關鍵字黑名單", value="suspicious_keywords"),
    ])
    async def toggle(self, interaction: discord.Interaction, module: app_commands.Choice[str]):
        cfg = load_config()
        current = cfg["antiraid"][module.value]["enabled"]
        cfg["antiraid"][module.value]["enabled"] = not current
        save_config(cfg)
        state = "✅ 啟用" if not current else "❌ 停用"
        await interaction.response.send_message(
            f"{state} 模組：**{module.name}**", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
