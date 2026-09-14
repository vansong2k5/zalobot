"""
Module Log Notifier Service - Chuyên phụ trách bắn log tiến trình & cảnh báo lỗi
qua Bot Zalo Phụ (Bot Tạp Hóa Benjaburg - Token: 1829149369477089735:SvyQYPFJWGVhqOJkcAHUFTljTeuhbTJgeBtuZgBHHmptqAjxOdxqLXeyLtrxxrng)
để Sếp Song theo dõi và xử lý trực tiếp trên Zalo.
"""

import os
import httpx
from datetime import datetime
from typing import Optional
from app.config import LOG_BOT_TOKEN

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)
LOG_CHAT_ID_FILE = os.path.join(DATA_DIR, "log_chat_id.txt")


def get_log_chat_id() -> Optional[str]:
    """Lấy chat_id của kênh nhận log hiện tại."""
    if os.path.exists(LOG_CHAT_ID_FILE):
        try:
            with open(LOG_CHAT_ID_FILE, "r", encoding="utf-8") as f:
                cid = f.read().strip()
                if cid:
                    return cid
        except Exception:
            pass
    return None


def set_log_chat_id(chat_id: str):
    """Ghi nhận chat_id nhận log mới (từ cá nhân sếp Song hoặc nhóm Zalo)."""
    try:
        with open(LOG_CHAT_ID_FILE, "w", encoding="utf-8") as f:
            f.write(str(chat_id).strip())
    except Exception as e:
        print(f"[LogNotifier] Lỗi lưu chat_id: {e}")


def send_log_message(text: str) -> bool:
    """
    Gửi một tin nhắn log trực tiếp qua Bot Zalo Ghi Log.
    """
    if not LOG_BOT_TOKEN or not text:
        return False

    target_chat_id = get_log_chat_id()
    if not target_chat_id:
        return False

    url = f"https://bot-api.zaloplatforms.com/bot{LOG_BOT_TOKEN}/sendMessage"
    headers = {"Content-Type": "application/json"}

    # Chia nhỏ nếu vượt 1800 ký tự
    MAX_LEN = 1800
    chunks = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)]

    success = True
    try:
        with httpx.Client(timeout=8.0) as client:
            for chunk in chunks:
                payload = {
                    "chat_id": str(target_chat_id),
                    "text": chunk
                }
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code != 200 or not resp.json().get("ok"):
                    print(f"[LogNotifier Error]: {resp.status_code} {resp.text}")
                    success = False
    except Exception as e:
        print(f"[LogNotifier Exception]: {e}")
        return False

    return success


def send_log_alert(title: str, content: str, level: str = "INFO") -> bool:
    """
    Gửi thông báo có định dạng đẹp mắt theo cấp độ (INFO, WARNING, ERROR, SUCCESS).
    """
    icons = {
        "INFO": "ℹ️",
        "WARNING": "⚠️",
        "ERROR": "🚨",
        "SUCCESS": "✅",
        "CAPTCHA": "🧩",
        "REG": "🚀"
    }
    icon = icons.get(level.upper(), "📌")
    now_str = datetime.now().strftime("%H:%M:%S %d/%m/%Y")

    formatted = (
        f"{icon} ［{title.upper()}］\n"
        f"⏰ Thời gian: {now_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{content.strip()}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    return send_log_message(formatted)
