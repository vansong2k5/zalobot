"""
Dịch vụ quản lý Người dùng & Phân quyền Admin — Hỗ trợ Đa Nền Tảng (Multi-Channel Identity Service).
Vận hành theo mô hình Star Schema:
- Tách biệt định danh từng nền tảng (Zalo, Telegram, Discord, Web) qua bảng UserIdentity.
- Dùng chung một hồ sơ khách hàng User và một ví số dư (balance).
- Truy vấn O(1) qua composite index (platform, platform_user_id), không quét bảng.
"""

from decimal import Decimal
from datetime import datetime
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import ADMIN_ZALO_IDS
from app.models import User, UserIdentity


def get_or_create_user_by_platform(
    db: Session,
    platform: str,
    platform_user_id: str,
    display_name: str = "",
    username: Optional[str] = None
) -> Tuple[Optional[User], bool]:
    """
    Phân giải danh tính người dùng đa nền tảng (Zalo, Telegram, Discord, Web).
    1. Tìm kiếm trong bảng UserIdentity với (platform, platform_user_id).
    2. Nếu đã tồn tại: Cập nhật last_active_at, tên hiển thị (nếu có đổi) và trả về User.
    3. Nếu chưa tồn tại: Tự động khởi tạo User mới (ví số dư dùng chung) và tạo UserIdentity liên kết.
    
    Trả về (User, is_first_time: bool).
    """
    if not platform_user_id:
        return None, False

    clean_platform = str(platform).strip().lower()
    clean_p_id = str(platform_user_id).strip()

    # Bỏ qua Group ID Zalo (bắt đầu bằng zgr-)
    if clean_platform == "zalo" and clean_p_id.startswith("zgr-"):
        return None, False

    clean_name = str(display_name).strip() if display_name else ""
    if not clean_name:
        clean_name = f"Khách {clean_p_id[-4:]}"

    # 1. Truy vấn O(1) qua Index (platform, platform_user_id)
    identity = db.query(UserIdentity).filter(
        UserIdentity.platform == clean_platform,
        UserIdentity.platform_user_id == clean_p_id
    ).first()

    if identity and identity.user:
        # Cập nhật thời gian hoạt động gần nhất
        identity.last_active_at = datetime.utcnow()
        if username and identity.platform_username != username:
            identity.platform_username = username
        if clean_name and identity.platform_display_name != clean_name:
            identity.platform_display_name = clean_name
            # Nếu tên khách hàng chính chưa đặt, cập nhật luôn
            if identity.user.display_name.startswith("Khách"):
                identity.user.display_name = clean_name
        db.commit()
        return identity.user, False

    # 2. Nếu chưa có UserIdentity, kiểm tra fallback User cũ (cho Zalo tương thích ngược)
    existing_legacy_user = None
    if clean_platform == "zalo":
        existing_legacy_user = db.query(User).filter(User.user_id == clean_p_id).first()

    if existing_legacy_user:
        # Gắn Identity Zalo vào User cũ
        new_identity = UserIdentity(
            user_id=existing_legacy_user.id,
            platform=clean_platform,
            platform_user_id=clean_p_id,
            platform_username=username,
            platform_display_name=clean_name,
            is_primary=1,
            created_at=datetime.utcnow(),
            last_active_at=datetime.utcnow()
        )
        db.add(new_identity)
        db.commit()
        return existing_legacy_user, False

    # 3. Tạo mới hoàn toàn Hồ sơ khách hàng (Customer Profile)
    # Tự động sinh mã khách hàng KH10001, KH10002...
    max_id = db.query(func.coalesce(func.max(User.id), 0)).scalar()
    customer_code = f"KH{10001 + max_id}"

    user = User(
        customer_code=customer_code,
        user_id=clean_p_id if clean_platform == "zalo" else None,
        display_name=clean_name,
        balance=Decimal("0.0"),
        role="customer",
        status="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(user)
    db.flush()  # Để lấy user.id

    # Tạo định danh nền tảng liên kết
    new_identity = UserIdentity(
        user_id=user.id,
        platform=clean_platform,
        platform_user_id=clean_p_id,
        platform_username=username,
        platform_display_name=clean_name,
        is_primary=1,
        created_at=datetime.utcnow(),
        last_active_at=datetime.utcnow()
    )
    db.add(new_identity)
    db.commit()
    db.refresh(user)

    return user, True


def link_user_platform(
    db: Session,
    user_id: int,
    platform: str,
    platform_user_id: str,
    display_name: str = "",
    username: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Liên kết thêm nền tảng mới (ví dụ Telegram, Discord) vào tài khoản User đã có
    để dùng chung một ví tiền (balance) và lịch sử đơn hàng.
    """
    clean_platform = str(platform).strip().lower()
    clean_p_id = str(platform_user_id).strip()

    # Kiểm tra xem platform_user_id này đã gắn với ai chưa
    existing_ident = db.query(UserIdentity).filter(
        UserIdentity.platform == clean_platform,
        UserIdentity.platform_user_id == clean_p_id
    ).first()

    if existing_ident:
        if existing_ident.user_id == user_id:
            return True, f"Tài khoản {clean_platform} này đã được liên kết với bạn từ trước."
        return False, f"Tài khoản {clean_platform} này đã được liên kết với một tài khoản khách khác!"

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, "Không tìm thấy hồ sơ khách hàng trung tâm."

    new_ident = UserIdentity(
        user_id=user.id,
        platform=clean_platform,
        platform_user_id=clean_p_id,
        platform_username=username,
        platform_display_name=display_name or user.display_name,
        is_primary=0,
        created_at=datetime.utcnow(),
        last_active_at=datetime.utcnow()
    )
    db.add(new_ident)
    db.commit()
    return True, f"Liên kết tài khoản {clean_platform.capitalize()} thành công! Số dư khả dụng của bạn: {int(user.balance):,} VNĐ"


def get_or_create_user(db: Session, zalo_user_id: str, display_name: str) -> Tuple[Optional[User], bool]:
    """
    Wrapper tương thích ngược 100% cho Bot Zalo hiện tại.
    Chuyển hướng toàn bộ sang kiến trúc Star Schema (platform='zalo').
    """
    return get_or_create_user_by_platform(
        db=db,
        platform="zalo",
        platform_user_id=zalo_user_id,
        display_name=display_name
    )


def is_admin_user(zalo_user_id: str) -> bool:
    """
    Kiểm tra tài khoản Zalo có thuộc danh sách Admin (ADMIN_ZALO_IDS) hay không.
    So sánh an toàn, không phân biệt hoa thường và loại bỏ khoảng trắng thừa.
    """
    if not zalo_user_id:
        return False
    clean_id = str(zalo_user_id).strip().lower()
    for a_id in ADMIN_ZALO_IDS:
        if a_id and str(a_id).strip().lower() == clean_id:
            return True
    return False
