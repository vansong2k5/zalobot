"""
Quản lý kết nối Cơ sở dữ liệu — Sử dụng SQLAlchemy 2.0 đồng bộ (Synchronous).
Đơn giản, ổn định, tránh toàn bộ các lỗi liên quan đến bất đồng bộ (asyncpg/greenlet).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import DATABASE_URL

# Cấu hình engine kết nối
connect_args = {}
# Nếu dùng SQLite, cần check_same_thread = False để FastAPI chạy đa luồng
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

try:
    engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)
    # Test thử kết nối
    with engine.connect() as conn:
        pass
except Exception as e:
    # Nếu kết nối PostgreSQL thất bại (do chưa bật Docker), tự động chuyển sang SQLite để dev chạy được ngay
    print(f"[CẢNH BÁO] Không thể kết nối '{DATABASE_URL}': {e}")
    print("[THÔNG BÁO] Hệ thống tự động chuyển sang SQLite tạm thời: sqlite:///./bot_zalo.db")
    DATABASE_URL = "sqlite:///./bot_zalo.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False}, echo=False)

# Tạo SessionFactory để cấp phát session cho mỗi request
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class cho toàn bộ SQLAlchemy models
Base = declarative_base()


def get_db():
    """
    Dependency cung cấp database session cho mỗi request trong FastAPI.
    Tự động giải phóng (close) kết nối sau khi request xử lý xong.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    Tự động tạo tất cả các bảng trong CSDL nếu chưa tồn tại.
    Được gọi khi ứng dụng FastAPI khởi động.
    """
    # Import toàn bộ models trước khi gọi create_all
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    print("-> [Database] Khởi tạo các bảng dữ liệu thành công!")
