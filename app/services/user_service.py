"""
Dịch vụ quản lý Người dùng & Phân quyền Admin.
"""

from decimal import Decimal
from sqlalchemy.orm import Session

from app.config import ADMIN_ZALO_IDS
from app.models import User


def get_or_create_user(db: Session, zalo_user_id: str, display_name: str) -> tuple[User | None, bool]:
    """
    Lấy thông tin người dùng theo Zalo ID, nếu chưa có thì tự động tạo mới.
    Trả về (user, is_first_time: bool).
    Tuyệt đối không tạo bản ghi cho Group ID (bắt đầu bằng zgr-).
    """
    if not zalo_user_id or str(zalo_user_id).startswith("zgr-"):
        return None, False

    clean_name = str(display_name).strip() if display_name else ""
    if not clean_name:
        clean_name = f"Khách {str(zalo_user_id)[-4:]}"

    user = db.query(User).filter(User.user_id == str(zalo_user_id)).first()
    is_first_time = False

    if not user:
        user = User(
            user_id=str(zalo_user_id),
            display_name=clean_name,
            balance=Decimal("0.0"),
            status="active"
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_first_time = True
    else:
        if clean_name and user.display_name != clean_name and clean_name != "Khách":
            user.display_name = clean_name
            db.commit()

    return user, is_first_time


def is_admin_user(zalo_user_id: str) -> bool:
    """
    Kiểm tra tài khoản Zalo có thuộc danh sách Admin (ADMIN_ZALO_IDS) hay không.
    """
    return str(zalo_user_id).strip() in ADMIN_ZALO_IDS

