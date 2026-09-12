"""
Dịch vụ Quản lý Nhóm Zalo & Bắn Thông Báo Đơn Hàng Mới vào Group.
"""

from datetime import datetime
from sqlalchemy.orm import Session
from app.config import ZALO_GROUP_IDS
from app.models import BotGroup, Order
from app.services.zalo_service import send_zalo_message


def register_or_update_group(db: Session, group_id: str, group_name: str = None) -> BotGroup:
    """
    Tự động ghi nhận hoặc cập nhật nhóm Zalo vào bảng bot_groups.
    Chỉ lưu vào bảng bot_groups, tuyệt đối không lưu vào bảng users.
    """
    if not group_id:
        return None

    clean_id = str(group_id).strip()
    # Chỉ chấp nhận Group ID thực sự (bắt đầu bằng zgr-)
    if not clean_id.startswith("zgr-") and "group" not in clean_id.lower():
        return None

    clean_name = str(group_name).strip() if group_name else "Nhóm Zalo"

    group = db.query(BotGroup).filter(BotGroup.group_id == clean_id).first()
    if not group:
        group = BotGroup(
            group_id=clean_id,
            group_name=clean_name,
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(group)
        db.commit()
        db.refresh(group)
        print(f"[Group Manager] Đã lưu nhóm mới vào bot_groups: {group.group_name} ({group.group_id})")
    else:
        # Cập nhật tên nếu có thay đổi và kích hoạt trạng thái active
        updated = False
        if clean_name and group.group_name != clean_name and clean_name != "Nhóm Zalo":
            group.group_name = clean_name
            updated = True
        if group.status != "active":
            group.status = "active"
            updated = True
        if updated:
            group.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(group)
            print(f"[Group Manager] Đã cập nhật nhóm bot_groups: {group.group_name} ({group.group_id})")

    return group


def get_all_active_groups(db: Session) -> list[str]:
    """
    Lấy danh sách tất cả các chat_id nhóm hợp lệ (kết hợp cả trong DB và cấu hình ZALO_GROUP_IDS ở .env).
    Chỉ chấp nhận các ID bắt đầu bằng 'zgr-' để tuyệt đối không gửi nhầm vào bot chat cá nhân.
    """
    group_ids = set()

    # 1. Lấy từ Database (chỉ lấy ID bắt đầu bằng zgr-)
    db_groups = db.query(BotGroup).filter(BotGroup.status == "active").all()
    for g in db_groups:
        if g.group_id and (g.group_id.startswith("zgr-") or "group" in g.group_id.lower()):
            group_ids.add(str(g.group_id))

    # 2. Lấy từ cấu hình .env (nếu có)
    for gid in ZALO_GROUP_IDS:
        if gid:
            group_ids.add(str(gid))

    return list(group_ids)


def mask_customer_name(display_name: str) -> str:
    """
    Che bớt ký tự trong tên khách hàng để vừa tạo hiệu ứng mua sắm (social proof),
    vừa bảo vệ quyền riêng tư cá nhân khi đăng vào nhóm công khai.
    Ví dụ: 'Nguyễn Văn Song' -> 'Nguyễn V*** S***'
    """
    if not display_name or display_name.strip() in ("", "Khách"):
        return "Khách hàng thân thiết"

    words = display_name.strip().split()
    if len(words) == 1:
        w = words[0]
        return w[:2] + "***" if len(w) > 2 else w + "***"

    masked_words = []
    for idx, w in enumerate(words):
        if idx == 0:
            # Giữ nguyên từ đầu tiên (Họ)
            masked_words.append(w)
        else:
            # Từ thứ 2 trở đi che bớt
            if len(w) > 1:
                masked_words.append(w[0] + "***")
            else:
                masked_words.append(w + "*")

    return " ".join(masked_words)


def notify_groups_order_completed(db: Session, order: Order) -> int:
    """
    Gửi thông báo 'Chúc mừng đơn hàng thành công' tới tất cả các group mà bot tham gia.
    Trả về số lượng group đã gửi thành công.
    """
    group_ids = get_all_active_groups(db)
    if not group_ids:
        print("[Group Notice] Chưa có nhóm nào được đăng ký để gửi thông báo đơn hàng.")
        return 0

    customer = order.user
    customer_display = customer.display_name if customer else "Khách hàng"
    masked_name = mask_customer_name(customer_display)

    lines = [
        "🎉 CHÚC MỪNG KHÁCH HÀNG VỪA MUA HÀNG THÀNH CÔNG! 🛍️\n",
        f"👤 Khách hàng: {masked_name}",
        f"📦 Sản phẩm: {order.product.product_name}",
        f"🔢 Số lượng: {order.quantity}",
        f"💰 Giá trị: {int(order.price):,} VNĐ",
        "⚡ Trạng thái: Đã tự động xuất kho & bàn giao tài khoản tức thì!\n",
        "👉 Nhắn tin riêng cho Bot soạn 'MENU' để xem sản phẩm và mua tài khoản tự động 24/7 nhé! ✨"
    ]
    notice_msg = "\n".join(lines)

    sent_count = 0
    for gid in group_ids:
        success = send_zalo_message(gid, notice_msg)
        if success:
            sent_count += 1
            print(f"[Group Notice] Đã gửi thông báo đơn #{order.order_code} tới nhóm {gid} thành công.")
        else:
            print(f"[Group Notice] Gửi thông báo tới nhóm {gid} thất bại.")

    return sent_count
