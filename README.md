# 🛡️ AntiRaid Bot — Discord 防炸群機器人

## 快速開始

### 1. 安裝依賴
```bash
pip install -r requirements.txt
```

### 2. 設定 config.json
填入你的機器人 Token：
```json
{
  "token": "YOUR_BOT_TOKEN_HERE",
  ...
}
```

### 3. 在 Discord Developer Portal 開啟 Privileged Intents
- `SERVER MEMBERS INTENT` 
- `MESSAGE CONTENT INTENT` 

### 4. 機器人所需權限
- `Kick Members`
- `Ban Members`（若使用 ban 動作）
- `Manage Channels`（刪除惡意頻道）
- `Manage Roles`（刪除惡意角色）
- `View Audit Log`（追蹤是誰建立頻道/角色）
- `Read/Send Messages`
- `Manage Messages`（刪除惡意訊息）

### 5. 啟動機器人
```bash
python main.py
```

### 6. 建立日誌頻道
在伺服器內建立一個名為 `bot-log` 的文字頻道，機器人會在此記錄所有事件。

---

## 偵測模組說明

| 模組 | 說明 |
|------|------|
| 訊息速率限制 | N 秒內發送超過 X 則訊息 → 踢出 |
| 頻道建立速率 | N 秒內建立超過 X 個頻道 → 踢出並刪除頻道 |
| 角色建立速率 | N 秒內建立超過 X 個角色 → 踢出並刪除角色 |
| 大量提及偵測 | 單則訊息提及超過 X 個用戶/角色 |
| 關鍵字黑名單 | 訊息含有可疑詐騙關鍵字 |
| 新帳號限制 | 帳號建立未滿 N 天即封鎖 |

---

## 斜線指令

| 指令 | 說明 |
|------|------|
| `/status` | 顯示目前所有模組狀態與白名單 |
| `/toggle <模組>` | 開啟/關閉特定偵測模組 |
| `/whitelist add_role <角色>` | 角色加入白名單 |
| `/whitelist remove_role <角色>` | 角色從白名單移除 |
| `/whitelist add_user <用戶>` | 用戶加入白名單 |
| `/whitelist remove_user <用戶>` | 用戶從白名單移除 |
| `/admin add_role <角色>` | 授予角色管理機器人的權限 |
| `/admin remove_role <角色>` | 撤銷角色管理機器人的權限 |
| `/set msg_rate <數量> <秒>` | 調整訊息速率閾值 |
| `/set keyword_add <關鍵字>` | 新增黑名單關鍵字 |
| `/set keyword_remove <關鍵字>` | 移除黑名單關鍵字 |
| `/set new_account_days <天數>` | 設定帳號最低天數 |

---

## 白名單邏輯

以下用戶**不受**防炸群限制：
1. 伺服器擁有者
2. 擁有「伺服器管理員」Discord 權限者
3. 被加入 `admin_roles` / `admin_users` 的角色/用戶
4. 被加入 `whitelist_roles` / `whitelist_users` 的角色/用戶

---

## 目錄結構
```
antiraid-bot/
├── main.py              # 程式入口
├── config.json          # 設定檔
├── requirements.txt
├── cogs/
│   ├── antiraid.py      # 核心偵測邏輯
│   ├── admin.py         # 管理員斜線指令
│   └── logger.py        # 伺服器事件日誌
└── utils/
    └── permissions.py   # 權限與白名單工具
```
