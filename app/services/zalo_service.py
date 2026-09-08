"""
Dịch vụ tương tác với Zalo Bot API (https://bot-api.zaloplatforms.com).
"""

import httpx
from app.config import ZALO_BOT_TOKEN


def send_zalo_message(user_zalo_id: str, text: str) -> bool:
    """
    Gửi tin nhắn phản hồi tới khách hàng qua Zalo Bot Platform.
    Tự động chia nhỏ tin nhắn nếu vượt quá 1800 ký tự để không bao giờ bị giới hạn 2000 ký tự của Zalo.
    """
    if not ZALO_BOT_TOKEN or not text:
        return False

    url = f"https://bot-api.zaloplatforms.com/bot{ZALO_BOT_TOKEN}/sendMessage"
    headers = {
        "Content-Type": "application/json"
    }

    # Giới hạn an toàn của Zalo là 1800 ký tự
    MAX_LEN = 1800

    chunks = []
    if len(text) <= MAX_LEN:
        chunks = [text]
    else:
        # Tách thông minh theo dòng để không bị cắt đôi câu
        lines = text.split("\n")
        current_chunk = []
        current_len = 0
        for line in lines:
            if current_len + len(line) + 1 > MAX_LEN:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                current_chunk.append(line)
                current_len += len(line) + 1
        if current_chunk:
            chunks.append("\n".join(current_chunk))

    success = True
    try:
        with httpx.Client(timeout=10.0) as client:
            for chunk in chunks:
                payload = {
                    "chat_id": str(user_zalo_id),
                    "text": chunk
                }
                response = client.post(url, json=payload, headers=headers)
                data = response.json() if response.status_code == 200 else {}
                if response.status_code != 200 or not data.get("ok"):
                    print(f"[Lỗi gửi Zalo] Status: {response.status_code}, Response: {response.text}")
                    success = False
    except Exception as e:
        print(f"Lỗi kết nối Zalo API: {e}")
        return False

    return success



def send_zalo_sticker(user_zalo_id: str, sticker_id: str) -> bool:
    """
    Gửi sticker tới khách hàng qua Zalo Bot Platform.
    Tài liệu: https://developers.zalo.me/docs/bot
    Endpoint: POST https://bot-api.zaloplatforms.com/bot{token}/sendSticker
    Body: {"chatId": user_zalo_id, "sticker": sticker_id}
    """
    if not ZALO_BOT_TOKEN:
        print(f"Bot tạm thời đang bảo trì. Chưa cấu hình ZALO_BOT_TOKEN để gửi sticker.")
        return False

    url = f"https://bot-api.zaloplatforms.com/bot{ZALO_BOT_TOKEN}/sendSticker"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "chat_id": str(user_zalo_id),
        "chatId": str(user_zalo_id),
        "sticker": str(sticker_id),
        "sticker_id": str(sticker_id),
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                return True
            else:
                print(f"[Lỗi gửi Sticker Zalo] Status: {response.status_code}, Response: {response.text}")
                return False
    except Exception as e:
        print(f"Lỗi kết nối Zalo API gửi sticker: {e}")
        return False


def send_chat_action(user_zalo_id: str, action: str = "typing") -> bool:
    """
    Gửi trạng thái chat action (ví dụ: 'typing' - đang soạn tin nhắn) tới người dùng.
    Endpoint: POST https://bot-api.zaloplatforms.com/bot{token}/sendChatAction
    """
    if not ZALO_BOT_TOKEN:
        return False

    url = f"https://bot-api.zaloplatforms.com/bot{ZALO_BOT_TOKEN}/sendChatAction"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "chat_id": str(user_zalo_id),
        "action": action
    }

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.post(url, json=payload, headers=headers)
            return response.status_code == 200
    except Exception as e:
        print(f"Lỗi gửi ChatAction: {e}")
        return False


def send_zalo_photo(user_zalo_id: str, photo_url: str, caption: str = "") -> bool:
    """
    Gửi hình ảnh trực tiếp (như mã QR thanh toán) tới người dùng qua Zalo Bot Platform.
    Tài liệu: https://developers.zalo.me/docs/bot
    Endpoint: POST https://bot-api.zaloplatforms.com/bot{token}/sendPhoto
    Body: {"chat_id": user_zalo_id, "photo": photo_url, "caption": caption}
    """
    if not ZALO_BOT_TOKEN:
        return False

    url = f"https://bot-api.zaloplatforms.com/bot{ZALO_BOT_TOKEN}/sendPhoto"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "chat_id": str(user_zalo_id),
        "photo": photo_url,
    }
    if caption:
        payload["caption"] = caption

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                return True
            else:
                print(f"[Lỗi gửi Photo Zalo] Status: {response.status_code}, Response: {response.text}")
                return False
    except Exception as e:
        print(f"Lỗi kết nối Zalo API gửi photo: {e}")
        return False


