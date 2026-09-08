"""
Router xử lý Webhook SePay & Webhook Zalo Bot.
Đường dẫn:
- POST /webhooks/sepay: Nhận thông báo giao dịch chuyển khoản từ SePay (https://my.sepay.vn/webhooks).
- POST /webhooks/zalo: Nhận tin nhắn của người dùng gửi tới Zalo Bot.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import SEPAY_API_KEY
from app.db import get_db, SessionLocal
from app.schemas import SepayWebhookPayload
from app.services import (
    handle_zalo_user_message,
    process_sepay_payment,
    send_zalo_message,
    register_or_update_group,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# ==============================================================================
# 1. WEBHOOK SEPAY (CỔNG THANH TOÁN TỰ ĐỘNG - https://my.sepay.vn/webhooks)
# ==============================================================================

@router.post("/sepay")
async def sepay_webhook(
    request: Request,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Endpoint tiếp nhận Webhook từ SePay khi tài khoản ngân hàng nhận được tiền.
    Tài liệu: https://developer.sepay.vn/webhook
    Yêu cầu: Phản hồi HTTP 200 và JSON: {"success": true}
    """
    # 1. Xác thực API Key từ SePay nếu đã cấu hình SEPAY_API_KEY trong .env
    if SEPAY_API_KEY and SEPAY_API_KEY != "your_sepay_api_key":
        is_valid = False
        if authorization:
            clean_auth = authorization.strip()
            # Hỗ trợ mọi định dạng: "Apikey {KEY}", "Bearer {KEY}", hoặc trực tiếp "{KEY}"
            if (
                clean_auth == f"Apikey {SEPAY_API_KEY}"
                or clean_auth == f"Bearer {SEPAY_API_KEY}"
                or clean_auth == SEPAY_API_KEY
                or clean_auth.replace("Apikey ", "").replace("Bearer ", "").strip() == SEPAY_API_KEY
            ):
                is_valid = True
            else:
                print(f"[Cảnh báo SePay Auth] Header Authorization: '{authorization}' không khớp với cấu hình SEPAY_API_KEY")

        if not is_valid:
            print(f"[Lỗi SePay Auth] Xác thực thất bại! Header: '{authorization}' | Config: '{SEPAY_API_KEY}'")
            raise HTTPException(status_code=401, detail="Xác thực SePay API Key không hợp lệ")

    # 2. Đọc payload JSON an toàn, hỗ trợ cả camelCase và snake_case
    try:
        data = await request.json()
    except Exception as e:
        print(f"[Lỗi SePay Webhook] Body không phải JSON hợp lệ: {e}")
        return {"success": False, "message": "Invalid JSON body"}

    print(f"[SePay Webhook Nhận Được] Data: {data}")

    content = str(data.get("content") or data.get("description") or "")
    transfer_type = str(data.get("transferType") or data.get("transfer_type") or "in")
    raw_amount = data.get("transferAmount") or data.get("transfer_amount") or data.get("amount") or 0
    try:
        from decimal import Decimal
        transfer_amount = Decimal(str(raw_amount))
    except Exception:
        from decimal import Decimal
        transfer_amount = Decimal("0")

    # 3. Xử lý giao dịch nhận tiền
    result = process_sepay_payment(
        db=db,
        content=content,
        transfer_amount=transfer_amount,
        transfer_type=transfer_type
    )

    print(f"[SePay Kết Quả Xử Lý] {result}")

    # 4. SePay bắt buộc response JSON phải có "success": true
    return {
        "success": True,
        "message": result.get("message", "Đã xử lý"),
        "result": result
    }



# ==============================================================================
# 2. WEBHOOK ZALO BOT (NHẬN TIN NHẮN TỪ KHÁCH HÀNG)
# ==============================================================================

def register_group_background(group_id: str, group_name: str):
    """
    Ghi nhận nhóm Zalo vào CSDL trong Background Task.
    """
    db = SessionLocal()
    try:
        register_or_update_group(db, group_id, group_name)
    except Exception as e:
        print(f"[Register Group Error]: {e}")
    finally:
        db.close()


def process_zalo_message_background(
    chat_id: str,
    sender_id: str,
    display_name: str,
    text: str,
    is_group: bool = False
):
    """
    Xử lý tin nhắn Zalo trong Background Worker để không làm nghẽn Webhook và tránh timeout Zalo.
    """
    db = SessionLocal()
    try:
        handle_zalo_user_message(
            db=db,
            zalo_user_id=chat_id,
            display_name=display_name,
            message_text=text,
            sender_id=sender_id,
            is_group=is_group
        )
    except Exception as e:
        print(f"[Zalo Background Worker Error]: {e}")
    finally:
        db.close()


@router.post("/zalo")
async def zalo_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Endpoint tiếp nhận sự kiện và tin nhắn từ Zalo Bot Platform.
    Tài liệu Zalo Bot: https://developers.zalo.me/docs/bot
    Trả về ngay HTTP 200 {"status": "ok"} trong vài mili-giây, xử lý logic trong BackgroundTasks.
    """
    try:
        body = await request.json()
    except Exception:
        # Nếu gửi body không phải JSON hợp lệ thì vẫn trả về 200 để tránh retry liên tục
        return {"status": "ok"}

    event_name = body.get("event_name", "")
    message_obj = body.get("message", {}) if isinstance(body.get("message"), dict) else {}
    from_user = (
        message_obj.get("from")
        or body.get("from")
        or body.get("sender")
        or {}
    )
    chat_obj = (
        message_obj.get("chat")
        or body.get("chat")
        or {}
    )

    chat_type = chat_obj.get("type", "")
    chat_title = chat_obj.get("title") or chat_obj.get("name")
    raw_chat_id = chat_obj.get("id") or body.get("chat_id")
    raw_user_id = from_user.get("id") or body.get("user_id_by_app")

    # Nhận diện nếu sự kiện/tin nhắn xuất phát từ một nhóm (Group)
    # Zalo Bot định danh Group luôn bắt đầu bằng tiền tố 'zgr-' hoặc type='group'/'supergroup'
    is_group = (
        chat_type in ("group", "supergroup")
        or str(raw_chat_id).startswith("zgr-")
    )

    if is_group and raw_chat_id:
        # Tự động ghi nhớ / cập nhật nhóm vào bảng bot_groups (tuyệt đối không ghi vào bảng users)
        background_tasks.add_task(
            register_group_background,
            str(raw_chat_id),
            str(chat_title or "Nhóm Zalo")
        )

    # Ưu tiên chat_id là nơi gửi tin nhắn đến (group hoặc cá nhân)
    chat_id = (
        raw_chat_id
        or raw_user_id
        or ""
    )

    sender_id = str(raw_user_id or raw_chat_id or "")

    text = (
        body.get("text")
        or message_obj.get("text")
        or ""
    )

    display_name = (
        from_user.get("display_name")
        or from_user.get("name")
        or from_user.get("first_name")
        or chat_obj.get("display_name")
        or chat_obj.get("name")
        or chat_obj.get("first_name")
        or body.get("display_name")
        or ""
    )

    # Trường hợp người dùng gửi dạng tin nhắn Zalo chưa hỗ trợ (voice, file...)
    if event_name == "message.unsupported.received" and chat_id:
        guide_text = (
            "💡 Chào bạn! Hiện tại hệ thống nhận diện tốt nhất qua tin nhắn chữ.\n"
            "👉 Bạn vui lòng soạn 'MENU' để xem danh sách dịch vụ hoặc nhắn số [Mã SP] để mua tài khoản nhé! ❤️"
        )
        background_tasks.add_task(send_zalo_message, str(chat_id), guide_text)
        return {"status": "ok"}

    if chat_id and text:
        source_label = f"Nhóm '{chat_title}'" if is_group else f"Khách '{display_name}'"
        print(f"[Zalo Webhook] Nhận tin nhắn từ {source_label} (chat_id: {chat_id}, sender: {sender_id}): '{text}'")
        # Đẩy vào BackgroundTasks để phản hồi Zalo ngay lập tức < 10ms tránh timeout
        background_tasks.add_task(
            process_zalo_message_background,
            str(chat_id),
            str(sender_id),
            str(display_name).strip(),
            str(text),
            bool(is_group)
        )

    return {"status": "ok"}


