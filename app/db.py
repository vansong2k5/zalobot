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
    Tự động tạo tất cả các bảng trong CSDL nếu chưa tồn tại,
    và tự động bổ sung các cột mới nếu đã có bảng từ trước (Auto-migration an toàn).
    """
    import app.models
    _ = app.models
    from sqlalchemy import inspect, text

    Base.metadata.create_all(bind=engine)

    # Kiểm tra và tự động thêm các cột mới cho product_stocks nếu thiếu
    try:
        inspector = inspect(engine)
        columns = [c["name"] for c in inspector.get_columns("product_stocks")]
        
        new_cols = {
            "assigned_email": "VARCHAR(255)",
            "email_password": "VARCHAR(255)",
            "mail_status": "VARCHAR(50) DEFAULT 'none'",
            "last_otp": "VARCHAR(50)",
            "last_otp_at": "TIMESTAMP"
        }

        with engine.begin() as conn:
            for col_name, col_type in new_cols.items():
                if col_name not in columns:
                    try:
                        conn.execute(text(f"ALTER TABLE product_stocks ADD COLUMN {col_name} {col_type}"))
                        print(f"-> [Migration] Đã thêm cột '{col_name}' vào bảng 'product_stocks'")
                    except Exception as col_err:
                        print(f"-> [Migration Warning] Không thể thêm cột '{col_name}': {col_err}")
    except Exception as ex:
        print(f"-> [Migration Check Warning]: {ex}")

    print("-> [Database] Khởi tạo các bảng dữ liệu thành công!")

