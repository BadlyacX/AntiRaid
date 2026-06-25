import discord
import json
import logging

logger = logging.getLogger("AntiRaid.Permissions")

def load_config() -> dict:
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: dict):
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

def is_bot_admin(member: discord.Member) -> bool:
    """
    檢查成員是否為機器人管理員。
    只認伺服器擁有者 + 明確設定的管理員清單，
    不依賴 Discord Administrator 權限，避免攻擊者操作指令。
    """
    # 伺服器擁有者永遠是管理員
    if member.guild.owner_id == member.id:
        return True

    config = load_config()
    perms = config.get("permissions", {})

    # 被指定的管理員使用者
    if member.id in perms.get("admin_users", []):
        return True

    # 擁有被指定的管理員角色
    admin_role_ids = set(perms.get("admin_roles", []))
    member_role_ids = {role.id for role in member.roles}
    if admin_role_ids & member_role_ids:
        return True

    return False

def is_whitelisted(member: discord.Member) -> bool:
    """
    檢查成員是否在白名單中（不受防炸群限制）。
    只認伺服器擁有者 + 明確設定的白名單/管理員清單，
    不依賴 Discord Administrator 權限，避免攻擊者拿到管理員就繞過偵測。
    """
    # 伺服器擁有者永遠白名單
    if member.guild.owner_id == member.id:
        return True

    config = load_config()
    perms = config.get("permissions", {})

    # 明確設定的管理員使用者
    if member.id in perms.get("admin_users", []):
        return True

    # 明確設定的白名單使用者
    if member.id in perms.get("whitelist_users", []):
        return True

    member_role_ids = {role.id for role in member.roles}

    # 明確設定的管理員角色
    if set(perms.get("admin_roles", [])) & member_role_ids:
        return True

    # 明確設定的白名單角色
    if set(perms.get("whitelist_roles", [])) & member_role_ids:
        return True

    return False

def add_admin_role(role_id: int):
    config = load_config()
    if role_id not in config["permissions"]["admin_roles"]:
        config["permissions"]["admin_roles"].append(role_id)
        save_config(config)

def remove_admin_role(role_id: int):
    config = load_config()
    config["permissions"]["admin_roles"] = [
        r for r in config["permissions"]["admin_roles"] if r != role_id
    ]
    save_config(config)

def add_whitelist_role(role_id: int):
    config = load_config()
    if role_id not in config["permissions"]["whitelist_roles"]:
        config["permissions"]["whitelist_roles"].append(role_id)
        save_config(config)

def remove_whitelist_role(role_id: int):
    config = load_config()
    config["permissions"]["whitelist_roles"] = [
        r for r in config["permissions"]["whitelist_roles"] if r != role_id
    ]
    save_config(config)

def add_whitelist_user(user_id: int):
    config = load_config()
    if user_id not in config["permissions"]["whitelist_users"]:
        config["permissions"]["whitelist_users"].append(user_id)
        save_config(config)

def remove_whitelist_user(user_id: int):
    config = load_config()
    config["permissions"]["whitelist_users"] = [
        u for u in config["permissions"]["whitelist_users"] if u != user_id
    ]
    save_config(config)
