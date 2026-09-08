"""
Gói Dịch Vụ Nghiệp Vụ (Business Logic & Services Package).

Phân chia thành các module chức năng:
- zalo_service: Gửi tin nhắn qua Zalo Bot API.
- user_service: Quản lý người dùng, phân quyền Admin.
- order_service: Quản lý đơn hàng, tạo mã đơn, quét đơn quá hạn.
- sepay_service: Tạo QR thanh toán SePay, xử lý webhook nạp tiền tự động.
- admin_bot_service: Xử lý các lệnh quản trị đặc quyền dành riêng cho Admin (ADSP, ADSTOCK, STATS...).
- customer_bot_service: Xử lý các lệnh tin nhắn từ khách hàng (MENU, BUY, SODU, RUT, REF...).
"""

from .zalo_service import send_zalo_message, send_zalo_sticker, send_chat_action, send_zalo_photo
from .user_service import get_or_create_user, is_admin_user
from .order_service import generate_unique_order_code, expire_pending_orders
from .sepay_service import (
    generate_sepay_qr_url,
    format_payment_instructions,
    process_sepay_payment,
)
from .admin_bot_service import handle_admin_message
from .customer_bot_service import handle_zalo_user_message
from .group_service import (
    register_or_update_group,
    get_all_active_groups,
    notify_groups_order_completed,
)

__all__ = [
    "send_zalo_message",
    "send_zalo_sticker",
    "send_chat_action",
    "send_zalo_photo",
    "get_or_create_user",
    "is_admin_user",
    "generate_unique_order_code",
    "expire_pending_orders",
    "generate_sepay_qr_url",
    "format_payment_instructions",
    "process_sepay_payment",
    "handle_admin_message",
    "handle_zalo_user_message",
    "register_or_update_group",
    "get_all_active_groups",
    "notify_groups_order_completed",
]
