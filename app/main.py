"""
Điểm Khởi Đầu Ứng Dụng FastAPI (Main Application Entrypoint).
Khởi tạo server, nạp toàn bộ routers, và tự động tạo bảng CSDL khi khởi động.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import ENVIRONMENT
from app.db import init_db
from app.api.webhooks import router as webhooks_router
from app.api.admin import router as admin_router
from app.api.jobs import router as jobs_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Sự kiện diễn ra khi khởi động và tắt server FastAPI:
    1. Khởi động: Tự động khởi tạo các bảng CSDL nếu chưa có.
    2. Tắt: Giải phóng tài nguyên nếu cần.
    """
    print("==================================================")
    print(f"🚀 Bot Zalo FastAPI Backend đang khởi động [{ENVIRONMENT}]")
    init_db()
    print("==================================================")
    yield
    print("🛑 Server đã dừng hoạt động an toàn.")


# Khởi tạo instance FastAPI
app = FastAPI(
    title="Hệ Thống Bot Zalo Bán Tài Khoản",
    description="Backend FastAPI xử lý Webhook Zalo Bot, Cổng thanh toán tự động Sepay, VietQR và Quản trị kho hàng.",
    version="2.0.0",
    docs_url="/docs" if ENVIRONMENT != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)


# Gắn các Router API
app.include_router(webhooks_router)
app.include_router(webhooks_router, prefix="/api")  # Hỗ trợ cả đường dẫn /api/webhooks/sepay từ SePay
app.include_router(admin_router)
app.include_router(jobs_router)


# Endpoint kiểm tra cơ bản
@app.get("/health", tags=["Hệ Thống"])
def health_check():
    """Kiểm tra server có đang hoạt động hay không."""
    return {"status": "ok", "environment": ENVIRONMENT}


@app.get("/", tags=["Hệ Thống"])
def root():
    """Trang chào mừng."""
    return {
        "message": "Hệ thống Bot Zalo Bán Tài Khoản đang chạy ổn định.",
        "docs": "/docs" if ENVIRONMENT != "production" else "Disabled in production"
    }


if __name__ == "__main__":
    import uvicorn
    # Cho phép chạy trực tiếp bằng lệnh: python app/main.py
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
