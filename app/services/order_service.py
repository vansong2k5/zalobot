"""
Dịch vụ quản lý Đơn hàng & Xử lý hết hạn đơn hàng.
"""

import random
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.config import ORDER_TIMEOUT_MINUTES
from app.models import Order


def generate_unique_order_code(db: Session) -> str:
    """
    Tạo mã đơn hàng ngẫu nhiên không trùng lặp, định dạng: DH + 6 chữ số (vd: DH108234).
    """
    while True:
        num = random.randint(100000, 999999)
        code = f"DH{num}"
        existing = db.query(Order).filter(Order.order_code == code).first()
        if not existing:
            return code


def expire_pending_orders(db: Session) -> int:
    """
    Quét các đơn hàng trạng thái 'pending' tạo quá thời gian quy định (ORDER_TIMEOUT_MINUTES)
    và chuyển trạng thái thành 'expired'.
    """
    expire_threshold = datetime.utcnow() - timedelta(minutes=ORDER_TIMEOUT_MINUTES)
    pending_orders = db.query(Order).filter(
        Order.status == "pending",
        Order.created_at < expire_threshold
    ).all()

    count = 0
    for o in pending_orders:
        o.status = "expired"
        count += 1

    if count > 0:
        db.commit()
    return count
