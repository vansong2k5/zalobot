"""
Router Các Tác Vụ Định Kỳ & Kiểm Tra Hệ Thống (Internal Jobs & Health Check).
Bao gồm:
- POST /internal/jobs/expire-orders: Quét và hủy các đơn hàng quá hạn thanh toán.
- GET /internal/jobs/health-check: Kiểm tra kết nối CSDL và dịch vụ.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config import ADMIN_API_KEY
from app.db import get_db
from app.services import expire_pending_orders

router = APIRouter(prefix="/internal/jobs", tags=["Tác Vụ Nội Bộ (Jobs)"])


def verify_job_key(x_admin_key: str = Header(None)):
    """Kiểm tra Header xác thực cho job nội bộ."""
    if not x_admin_key or x_admin_key.strip() != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Không có quyền gọi job nội bộ")
    return True


@router.post("/expire-orders", dependencies=[Depends(verify_job_key)])
def job_expire_orders(db: Session = Depends(get_db)):
    """
    Tác vụ quét và chuyển trạng thái các đơn hàng 'pending' quá 15 phút thành 'expired'.
    Thường được gọi định kỳ qua cron job (ví dụ mỗi 5 phút một lần).
    """
    expired_count = expire_pending_orders(db)
    return {
        "status": "ok",
        "message": f"Đã quét và hủy {expired_count} đơn hàng quá hạn thanh toán.",
        "expired_count": expired_count
    }


@router.get("/health-check")
def job_health_check(db: Session = Depends(get_db)):
    """
    Kiểm tra tình trạng hoạt động của cơ sở dữ liệu.
    """
    try:
        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return {
        "status": "ok" if db_status == "healthy" else "error",
        "database": db_status
    }
