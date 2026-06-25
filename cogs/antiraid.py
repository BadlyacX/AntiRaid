import discord
from discord.ext import commands
from collections import defaultdict, deque
from datetime import datetime, timezone
import asyncio
import logging
from utils.permissions import is_whitelisted, load_config

logger = logging.getLogger("AntiRaid.Core")

# 危險權限：一旦角色新增這些權限，視為提權攻擊
DANGEROUS_PERMS = (
    "administrator", "ban_members", "kick_members",
    "manage_guild", "manage_roles", "manage_channels",
    "manage_webhooks", "manage_messages", "mention_everyone",
)

# ── 速率追蹤器 ──────────────────────────────────────────────
class RateTracker:
    """追蹤某個 key 在時間窗口內的事件次數"""
    def __init__(self):
        self._events: dict[str, deque] = defaultdict(deque)

    def add(self, key: str, now: datetime) -> int:
        self._events[key].append(now.timestamp())
        return len(self._events[key])

    def prune(self, key: str, window: float, now: datetime):
        cutoff = now.timestamp() - window
        q = self._events[key]
        while q and q[0] < cutoff:
            q.popleft()

    def count(self, key: str, window: float, now: datetime) -> int:
        self.prune(key, window, now)
        return len(self._events[key])

    def reset(self, key: str):
        self._events.pop(key, None)


# ── 跨頻道重複訊息追蹤器 ────────────────────────────────────
class CrossChannelSpamTracker:
    """
    追蹤同一用戶在時間窗口內，
    是否在不同頻道發送了相同（或高度相似）的訊息。

    結構：
      _records[guild_id][user_id][content_hash]
          = deque of (timestamp, channel_id, message)
    """
    def __init__(self):
        # guild_id -> user_id -> content_key -> deque[(ts, ch_id, msg)]
        self._records: dict[int, dict[int, dict[str, deque]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(deque))
        )

    @staticmethod
    def _normalize(content: str) -> str:
        """統一化內容：小寫、去除多餘空白，方便比對"""
        return " ".join(content.lower().split())

    def add(self, message: discord.Message, now: datetime) -> tuple[int, list[discord.Message]]:
        """
        記錄一則訊息，回傳 (不同頻道數, 該內容的所有訊息列表)。
        """
        guild_id  = message.guild.id
        user_id   = message.author.id
        key       = self._normalize(message.content)
        ch_id     = message.channel.id

        if not key:          # 空訊息（附件等）略過
            return 0, []

        q = self._records[guild_id][user_id][key]
        q.append((now.timestamp(), ch_id, message))
        return self._stats(guild_id, user_id, key, now)

    def _stats(self, guild_id, user_id, key, now) -> tuple[int, list[discord.Message]]:
        window = 60.0        # 固定 1 分鐘窗口
        cutoff = now.timestamp() - window
        q = self._records[guild_id][user_id][key]

        # 修剪過期記錄
        while q and q[0][0] < cutoff:
            q.popleft()

        unique_channels = {entry[1] for entry in q}
        messages        = [entry[2] for entry in q]
        return len(unique_channels), messages

    def reset(self, guild_id: int, user_id: int, key: str):
        try:
            del self._records[guild_id][user_id][key]
        except KeyError:
            pass


# ── 主 Cog ──────────────────────────────────────────────────
class AntiRaid(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.msg_tracker        = RateTracker()            # 訊息速率
        self.ch_tracker         = RateTracker()            # 頻道建立速率
        self.role_tracker       = RateTracker()            # 角色建立速率
        self.ch_del_tracker     = RateTracker()            # 頻道刪除速率
        self.role_del_tracker   = RateTracker()            # 角色刪除速率
        self.kick_tracker       = RateTracker()            # 踢出成員速率
        self.ban_tracker        = RateTracker()            # 封禁成員速率
        self.cross_ch_tracker   = CrossChannelSpamTracker() # 跨頻道重複訊息
        # 每個目標一把鎖，避免不同攻擊者的處置互相阻塞
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ── 工具 ────────────────────────────────────────────────

    def _cfg(self) -> dict:
        return load_config().get("antiraid", {})

    async def _log(self, guild: discord.Guild, embed: discord.Embed):
        cfg = load_config()
        ch_name = cfg.get("log_channel_name", "bot-log")
        ch = discord.utils.get(guild.text_channels, name=ch_name)
        if ch:
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                pass

    # ── 全域清掃：回查 Audit Log + 遍歷頻道訊息 ──────────────

    async def _sweep_audit_channels(self, guild: discord.Guild, user_id: int,
                                     lookback: float, already: set[int]) -> list[discord.abc.GuildChannel]:
        """從 Audit Log 找出該用戶在 lookback 秒內建立的所有頻道"""
        found: list[discord.abc.GuildChannel] = []
        now_ts = datetime.now(timezone.utc).timestamp()
        try:
            async for entry in guild.audit_logs(limit=100, action=discord.AuditLogAction.channel_create):
                if now_ts - entry.created_at.timestamp() > lookback:
                    break
                if entry.user and entry.user.id == user_id:
                    ch = guild.get_channel(entry.target.id) if entry.target else None
                    if ch and ch.id not in already:
                        found.append(ch)
                        already.add(ch.id)
        except discord.Forbidden:
            pass
        return found

    async def _sweep_audit_roles(self, guild: discord.Guild, user_id: int,
                                  lookback: float, already: set[int]) -> list[discord.Role]:
        """從 Audit Log 找出該用戶在 lookback 秒內建立的所有角色"""
        found: list[discord.Role] = []
        now_ts = datetime.now(timezone.utc).timestamp()
        try:
            async for entry in guild.audit_logs(limit=100, action=discord.AuditLogAction.role_create):
                if now_ts - entry.created_at.timestamp() > lookback:
                    break
                if entry.user and entry.user.id == user_id:
                    role = guild.get_role(entry.target.id) if entry.target else None
                    if role and role.id not in already and not role.is_default():
                        found.append(role)
                        already.add(role.id)
        except discord.Forbidden:
            pass
        return found

    async def _sweep_channel_messages(self, channel: discord.TextChannel,
                                       user_id: int, after: datetime) -> list[discord.Message]:
        """掃描單一頻道，找出該用戶在 after 之後的所有訊息"""
        found: list[discord.Message] = []
        try:
            async for msg in channel.history(limit=200, after=after):
                if msg.author.id == user_id:
                    found.append(msg)
        except (discord.Forbidden, discord.HTTPException):
            pass
        return found

    async def _full_cleanup(self, guild: discord.Guild, user_id: int,
                             lookback_seconds: float = 600,
                             skip_msg_ids: set[int] = None,
                             skip_ch_ids: set[int] = None,
                             skip_role_ids: set[int] = None):
        """
        全域清掃：觸發後回溯 Audit Log 和所有頻道，
        刪除該用戶在過去 lookback_seconds 秒內留下的所有痕跡。
        """
        skip_msg_ids  = skip_msg_ids  or set()
        skip_ch_ids   = skip_ch_ids   or set()
        skip_role_ids = skip_role_ids or set()

        sweep_chs, sweep_roles = await asyncio.gather(
            self._sweep_audit_channels(guild, user_id, lookback_seconds, set(skip_ch_ids)),
            self._sweep_audit_roles(guild, user_id, lookback_seconds, set(skip_role_ids)),
        )

        after_dt = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - lookback_seconds,
            tz=timezone.utc,
        )
        text_channels = [ch for ch in guild.text_channels
                         if ch.permissions_for(guild.me).read_message_history]
        msg_tasks = [self._sweep_channel_messages(ch, user_id, after_dt) for ch in text_channels]
        msg_results = await asyncio.gather(*msg_tasks) if msg_tasks else []

        all_msgs = [m for batch in msg_results for m in batch if m.id not in skip_msg_ids]

        deleted_msgs = 0
        deleted_chs  = 0
        deleted_roles = 0

        for msg in all_msgs:
            try:
                await msg.delete()
                deleted_msgs += 1
            except (discord.NotFound, discord.Forbidden):
                pass

        for ch in sweep_chs:
            try:
                await ch.delete(reason="[AntiRaid] 全域清掃：疑似炸群建立的頻道")
                deleted_chs += 1
            except (discord.NotFound, discord.Forbidden):
                pass

        for role in sweep_roles:
            try:
                await role.delete(reason="[AntiRaid] 全域清掃：疑似炸群建立的角色")
                deleted_roles += 1
            except (discord.NotFound, discord.Forbidden):
                pass

        if deleted_msgs or deleted_chs or deleted_roles:
            embed = discord.Embed(
                title="🧹 全域清掃完成",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="用戶 ID", value=str(user_id), inline=True)
            embed.add_field(name="回溯範圍", value=f"{lookback_seconds:.0f} 秒", inline=True)
            embed.add_field(
                name="清除結果",
                value=f"訊息 {deleted_msgs} 則 ｜ 頻道 {deleted_chs} 個 ｜ 角色 {deleted_roles} 個",
                inline=False,
            )
            await self._log(guild, embed)
            logger.info(
                f"[{guild.name}] 全域清掃 user={user_id}："
                f"訊息={deleted_msgs} 頻道={deleted_chs} 角色={deleted_roles}"
            )

    # ── 角色降級 + 踢出/封禁 ──────────────────────────────────

    async def _demote_roles(self, member: discord.Member, reason: str) -> int:
        """移除目標身上所有可管理的角色，回傳成功移除的數量"""
        bot_top = member.guild.me.top_role
        removable = [
            r for r in member.roles
            if not r.is_default() and r < bot_top and r.is_assignable()
        ]
        removed = 0
        for role in removable:
            try:
                await member.remove_roles(role, reason=f"[AntiRaid] 降級：{reason}")
                removed += 1
            except (discord.Forbidden, discord.HTTPException):
                pass
        return removed

    async def _execute_kick_or_ban(self, guild: discord.Guild,
                                    member: discord.Member,
                                    action: str, reason: str) -> str:
        """嘗試踢出/封禁，權限不足時先降級角色再重試"""
        for attempt in range(2):
            try:
                if action == "ban":
                    await guild.ban(member, reason=f"[AntiRaid] {reason}", delete_message_days=1)
                    return "封禁"
                else:
                    await guild.kick(member, reason=f"[AntiRaid] {reason}")
                    return "踢出"
            except discord.Forbidden:
                if attempt == 0:
                    demoted = await self._demote_roles(member, reason)
                    if demoted > 0:
                        logger.info(f"[{guild.name}] 已移除 {member} 的 {demoted} 個角色，重試踢出")
                        continue
                logger.warning(f"無法對 {member} 執行動作：降級後仍權限不足")
                return "（動作失敗，請確認機器人角色順序）"
            except discord.HTTPException as e:
                logger.warning(f"無法對 {member} 執行動作：{e}")
                return "（動作失敗，請確認機器人權限）"
        return "（動作失敗）"

    # ── Audit Log 共用工具 ──────────────────────────────────────

    async def _find_executor(self, guild: discord.Guild, action: discord.AuditLogAction,
                             target_id: int, within: float = 10.0):
        """從 Audit Log 找出某個目標操作的執行者"""
        now = datetime.now(timezone.utc)
        try:
            async for entry in guild.audit_logs(limit=10, action=action):
                if (now - entry.created_at).total_seconds() > within:
                    break
                target = entry.target
                if target is not None and getattr(target, "id", None) == target_id:
                    return entry.user
        except discord.Forbidden:
            return None
        return None

    async def _audit_rate_check(self, guild: discord.Guild,
                                audit_action: discord.AuditLogAction, target_id: int,
                                cfg_key: str, tracker: RateTracker, key_suffix: str,
                                max_key: str, max_default: int, unit_label: str):
        """
        通用：透過 Audit Log 找出執行者，追蹤其某類操作的速率，
        超過閾值就處置。用於頻道/角色刪除、大量踢出/封禁。
        """
        cfg = self._cfg().get(cfg_key, {})
        if not cfg.get("enabled"):
            return

        executor = await self._find_executor(guild, audit_action, target_id)
        if not executor or executor.id == self.bot.user.id:
            return

        member = guild.get_member(executor.id)
        if not member or is_whitelisted(member):
            return

        now = datetime.now(timezone.utc)
        key = f"{guild.id}:{member.id}:{key_suffix}"
        interval = cfg.get("interval_seconds", 10)
        threshold = cfg.get(max_key, max_default)
        tracker.add(key, now)
        count = tracker.count(key, interval, now)

        if count >= threshold:
            tracker.reset(key)
            await self._take_action(
                guild, member,
                f"{interval} 秒內{unit_label} {count} 次（閾值 {threshold}）",
                action=cfg.get("action", "ban"),
            )

    # ── 主要處置動作 ────────────────────────────────────────────

    async def _take_action(
        self,
        guild: discord.Guild,
        member: discord.Member,
        reason: str,
        action: str = "kick",
        delete_messages: list[discord.Message] = None,
        delete_channels: list[discord.abc.GuildChannel] = None,
        delete_roles: list[discord.Role] = None,
        full_sweep: bool = True,
    ):
        """執行踢出 / ban，並刪除惡意訊息 / 頻道 / 角色，然後執行全域清掃"""
        already_deleted_msg_ids  = set()
        already_deleted_ch_ids   = set()
        already_deleted_role_ids = set()

        async with self._locks[member.id]:
            # 刪除惡意訊息
            if delete_messages:
                for msg in delete_messages:
                    try:
                        await msg.delete()
                        already_deleted_msg_ids.add(msg.id)
                    except (discord.NotFound, discord.Forbidden):
                        already_deleted_msg_ids.add(msg.id)

            # 刪除惡意頻道
            if delete_channels:
                for ch in delete_channels:
                    try:
                        await ch.delete(reason=f"[AntiRaid] {reason}")
                        already_deleted_ch_ids.add(ch.id)
                    except (discord.NotFound, discord.Forbidden):
                        already_deleted_ch_ids.add(ch.id)

            # 刪除惡意角色
            if delete_roles:
                for role in delete_roles:
                    try:
                        await role.delete(reason=f"[AntiRaid] {reason}")
                        already_deleted_role_ids.add(role.id)
                    except (discord.NotFound, discord.Forbidden):
                        already_deleted_role_ids.add(role.id)

            # 踢出或封禁成員（權限不足時嘗試降級角色後重試）
            action_text = await self._execute_kick_or_ban(guild, member, action, reason)

        # 發送日誌
        embed = discord.Embed(
            title="🚨防炸群觸發",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="目標", value=f"{member} (`{member.id}`)", inline=False)
        embed.add_field(name="原因", value=reason, inline=False)
        embed.add_field(name="動作", value=action_text, inline=True)
        embed.add_field(name="帳號建立時間", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await self._log(guild, embed)
        logger.info(f"[{guild.name}] {action_text} {member}：{reason}")

        # 全域清掃：回溯 Audit Log + 遍歷頻道訊息
        if full_sweep:
            await self._full_cleanup(
                guild, member.id,
                lookback_seconds=600,
                skip_msg_ids=already_deleted_msg_ids,
                skip_ch_ids=already_deleted_ch_ids,
                skip_role_ids=already_deleted_role_ids,
            )

    # ── 偵測：Webhook 垃圾訊息 ───────────────────────────────

    async def _handle_webhook_message(self, message: discord.Message):
        """偵測 Webhook 洗版：超過閾值就刪除 Webhook 並清除其訊息"""
        cfg = self._cfg().get("message_rate", {})
        if not cfg.get("enabled"):
            return

        now = datetime.now(timezone.utc)
        key = f"{message.guild.id}:wh:{message.webhook_id}"
        interval = cfg.get("interval_seconds", 5)
        max_msgs = cfg.get("max_messages", 15)
        self.msg_tracker.add(key, now)
        count = self.msg_tracker.count(key, interval, now)

        if count < max_msgs:
            return
        self.msg_tracker.reset(key)

        # 刪除 Webhook 本身（阻止繼續發送）
        try:
            wh = await self.bot.fetch_webhook(message.webhook_id)
            await wh.delete(reason="[AntiRaid] Webhook 洗版")
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

        # 清除該 Webhook 的近期訊息
        spam = []
        try:
            async for m in message.channel.history(limit=50):
                if m.webhook_id == message.webhook_id:
                    spam.append(m)
        except discord.Forbidden:
            spam = [message]
        deleted = 0
        for m in spam:
            try:
                await m.delete()
                deleted += 1
            except (discord.NotFound, discord.Forbidden):
                pass

        embed = discord.Embed(
            title="🚨 Webhook 洗版已攔截",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Webhook ID", value=str(message.webhook_id), inline=True)
        embed.add_field(name="頻道", value=message.channel.mention, inline=True)
        embed.add_field(name="處置", value=f"刪除 Webhook ｜ 清除 {deleted} 則訊息", inline=False)
        await self._log(message.guild, embed)
        logger.info(f"[{message.guild.name}] 攔截 Webhook 洗版 wh={message.webhook_id}，清除 {deleted} 則")

    # ── 偵測：訊息速率 ───────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.id == self.bot.user.id:
            return

        # Webhook 訊息沒有對應成員，單獨處理
        if message.webhook_id:
            await self._handle_webhook_message(message)
            return

        member = message.guild.get_member(message.author.id)
        if not member or is_whitelisted(member):
            return

        cfg = self._cfg()
        now = datetime.now(timezone.utc)

        # ── 關鍵字黑名單 ──
        kw_cfg = cfg.get("suspicious_keywords", {})
        if kw_cfg.get("enabled"):
            content_lower = message.content.lower()
            for kw in kw_cfg.get("keywords", []):
                if kw.lower() in content_lower:
                    await self._take_action(
                        message.guild, member,
                        f"訊息包含可疑關鍵字：`{kw}`",
                        action=kw_cfg.get("action", "kick"),
                        delete_messages=[message]
                    )
                    return

        # ── 大量提及 ──
        mm_cfg = cfg.get("mass_mention", {})
        if mm_cfg.get("enabled"):
            mention_count = len(message.mentions) + len(message.role_mentions)
            if mention_count >= mm_cfg.get("max_mentions", 10):
                await self._take_action(
                    message.guild, member,
                    f"單則訊息提及 {mention_count} 個用戶/角色",
                    action=mm_cfg.get("action", "kick"),
                    delete_messages=[message]
                )
                return

        # ── 跨頻道重複訊息 ──
        cc_cfg = cfg.get("cross_channel_spam", {})
        if cc_cfg.get("enabled") and message.content.strip():
            unique_chs, spam_msgs = self.cross_ch_tracker.add(message, now)
            max_chs = cc_cfg.get("max_channels", 3)
            if unique_chs >= max_chs:
                content_key = CrossChannelSpamTracker._normalize(message.content)
                self.cross_ch_tracker.reset(message.guild.id, member.id, content_key)
                await self._take_action(
                    message.guild, member,
                    f"30秒內在 {unique_chs} 個頻道發送相同訊息（閾值 {max_chs} 個頻道）",
                    action=cc_cfg.get("action", "kick"),
                    delete_messages=spam_msgs
                )
                return

        # ── 訊息發送速率 ──
        rate_cfg = cfg.get("message_rate", {})
        if rate_cfg.get("enabled"):
            key = f"{message.guild.id}:{member.id}"
            interval = rate_cfg.get("interval_seconds", 5)
            max_msgs = rate_cfg.get("max_messages", 15)
            self.msg_tracker.add(key, now)
            count = self.msg_tracker.count(key, interval, now)
            if count >= max_msgs:
                self.msg_tracker.reset(key)
                # 嘗試抓近期訊息一起刪除
                spam_msgs = []
                try:
                    async for m in message.channel.history(limit=30):
                        if m.author.id == member.id:
                            spam_msgs.append(m)
                except discord.Forbidden:
                    spam_msgs = [message]
                await self._take_action(
                    message.guild, member,
                    f"{interval} 秒內發送 {count} 則訊息（閾值 {max_msgs}）",
                    action=rate_cfg.get("action", "kick"),
                    delete_messages=spam_msgs
                )

    # ── 偵測：頻道建立速率 ────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        cfg = self._cfg().get("channel_creation_rate", {})
        if not cfg.get("enabled"):
            return

        guild = channel.guild
        now = datetime.now(timezone.utc)

        # 取得最近的稽核日誌確認是誰建立的
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_create):
                if (now - entry.created_at).total_seconds() < 5 and entry.target.id == channel.id:
                    executor = entry.user
                    break
            else:
                return
        except discord.Forbidden:
            return

        if not executor or executor.id == self.bot.user.id:
            return

        member = guild.get_member(executor.id)
        if not member or is_whitelisted(member):
            return

        key = f"{guild.id}:{member.id}:ch"
        interval = cfg.get("interval_seconds", 10)
        max_ch = cfg.get("max_channels", 3)
        self.ch_tracker.add(key, now)
        count = self.ch_tracker.count(key, interval, now)

        if count >= max_ch:
            self.ch_tracker.reset(key)
            await self._take_action(
                guild, member,
                f"{interval} 秒內建立 {count} 個頻道（閾值 {max_ch}）",
                action=cfg.get("action", "kick"),
                delete_channels=[channel]
            )

    # ── 偵測：角色建立速率 ────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        cfg = self._cfg().get("role_creation_rate", {})
        if not cfg.get("enabled"):
            return

        guild = role.guild
        now = datetime.now(timezone.utc)

        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.role_create):
                if (now - entry.created_at).total_seconds() < 5 and entry.target.id == role.id:
                    executor = entry.user
                    break
            else:
                return
        except discord.Forbidden:
            return

        if not executor or executor.id == self.bot.user.id:
            return

        member = guild.get_member(executor.id)
        if not member or is_whitelisted(member):
            return

        key = f"{guild.id}:{member.id}:role"
        interval = cfg.get("interval_seconds", 10)
        max_roles = cfg.get("max_roles", 3)
        self.role_tracker.add(key, now)
        count = self.role_tracker.count(key, interval, now)

        if count >= max_roles:
            self.role_tracker.reset(key)
            await self._take_action(
                guild, member,
                f"{interval} 秒內建立 {count} 個角色（閾值 {max_roles}）",
                action=cfg.get("action", "kick"),
                delete_roles=[role],
            )


    # ── 偵測：角色提權（危險權限 / 位置） ────────────────────────

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = after.guild
        cfg = self._cfg().get("permission_guard", {})
        if not cfg.get("enabled"):
            return

        # 1) 新增的危險權限
        newly_granted = [
            p for p in DANGEROUS_PERMS
            if getattr(after.permissions, p) and not getattr(before.permissions, p)
        ]

        # 2) 位置躍升至機器人之上
        bot_top = guild.me.top_role
        escalated = after.position >= bot_top.position and before.position < bot_top.position

        if not newly_granted and not escalated:
            return

        # 找出是誰改的
        executor = await self._find_executor(guild, discord.AuditLogAction.role_update, after.id)
        if executor and executor.id == self.bot.user.id:
            return
        member = guild.get_member(executor.id) if executor else None
        if member and is_whitelisted(member):
            return

        # 還原變更（權限優先還原，位置壓回機器人下方）
        edit_kwargs = {}
        if newly_granted:
            edit_kwargs["permissions"] = before.permissions
        if escalated:
            edit_kwargs["position"] = max(1, bot_top.position - 1)
        if edit_kwargs:
            try:
                await after.edit(reason="[AntiRaid] 還原角色提權", **edit_kwargs)
            except (discord.Forbidden, discord.HTTPException):
                pass

        reason_parts = []
        if newly_granted:
            reason_parts.append("新增危險權限：" + ", ".join(newly_granted))
        if escalated:
            reason_parts.append("角色位置躍升至機器人之上")
        reason = f"角色 `{after.name}` 異常變更（{'；'.join(reason_parts)}）"

        # 有抓到執行者就處置，否則僅記錄
        if member:
            await self._take_action(guild, member, reason, action=cfg.get("action", "ban"))
        else:
            embed = discord.Embed(
                title="⚠️ 角色提權已還原（未能識別執行者）",
                color=discord.Color.dark_red(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="角色", value=after.mention, inline=False)
            embed.add_field(name="原因", value=reason, inline=False)
            await self._log(guild, embed)
            logger.warning(f"[{guild.name}] {reason}（未識別執行者）")

    # ── 偵測：大量刪除頻道 ────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._audit_rate_check(
            channel.guild, discord.AuditLogAction.channel_delete, channel.id,
            cfg_key="channel_deletion_rate", tracker=self.ch_del_tracker,
            key_suffix="ch_del", max_key="max_channels", max_default=3,
            unit_label="刪除頻道",
        )

    # ── 偵測：大量刪除角色 ────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._audit_rate_check(
            role.guild, discord.AuditLogAction.role_delete, role.id,
            cfg_key="role_deletion_rate", tracker=self.role_del_tracker,
            key_suffix="role_del", max_key="max_roles", max_default=3,
            unit_label="刪除角色",
        )

    # ── 偵測：大量踢出成員 ────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._audit_rate_check(
            member.guild, discord.AuditLogAction.kick, member.id,
            cfg_key="mass_kick", tracker=self.kick_tracker,
            key_suffix="kick", max_key="max_kicks", max_default=3,
            unit_label="踢出成員",
        )

    # ── 偵測：大量封禁成員 ────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.abc.User):
        await self._audit_rate_check(
            guild, discord.AuditLogAction.ban, user.id,
            cfg_key="mass_ban", tracker=self.ban_tracker,
            key_suffix="ban", max_key="max_bans", max_default=3,
            unit_label="封禁成員",
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiRaid(bot))
