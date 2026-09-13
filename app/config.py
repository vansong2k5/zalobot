"""
Cấu hình ứng dụng — Đọc các biến từ file .env bằng load_dotenv().
Tập trung vào SePay Webhooks & QR và Zalo Bot API.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Tìm đường dẫn file .env ở thư mục gốc của dự án
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"

# Nạp file .env vào môi trường
load_dotenv(dotenv_path=ENV_FILE)

# --- 1. Cơ sở dữ liệu ---
# Mặc định kết nối PostgreSQL Docker (root:root)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://root:root@localhost:5432/bot_zalo"
)

# Chuyển đổi định dạng URL nếu dùng cú pháp cũ của Render/Heroku/Supabase
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

# --- 2. Zalo Bot Platform ---
# Token bot từ Zalo Bot Creator
ZALO_BOT_TOKEN = os.getenv("ZALO_BOT_TOKEN", "")
LOG_BOT_TOKEN = os.getenv("LOG_BOT_TOKEN", "1829149369477089735:SvyQYPFJWGVhqOJkcAHUFTljTeuhbTJgeBtuZgBHHmptqAjxOdxqLXeyLtrxxrng").strip()

# --- 3. Cổng thanh toán Sepay (https://my.sepay.vn/webhooks) ---
# API key bảo vệ webhook cấu hình trên Sepay
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")

# Thông tin tài khoản ngân hàng để tạo QR thanh toán qua SePay (qr.sepay.vn)
SEPAY_BANK = os.getenv("SEPAY_BANK", "BIDV")
SEPAY_ACCOUNT_NO = os.getenv("SEPAY_ACCOUNT_NO", "8871479007")
SEPAY_ACCOUNT_NAME = os.getenv("SEPAY_ACCOUNT_NAME", "NGUYEN DOAN VAN SONG")

# --- 4. Khóa bí mật & Quản trị viên Zalo ---
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "admin_secret_key_123")
ADMIN_ZALO_IDS = [
    x.strip() for x in os.getenv("ADMIN_ZALO_IDS", "7e643dda3a8cd3d28a9d").split(",") if x.strip()
]

# --- 5. Cấu hình đơn hàng & Môi trường ---
ORDER_TIMEOUT_MINUTES = int(os.getenv("ORDER_TIMEOUT_MINUTES", "15"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# --- 6. Danh sách nhóm Zalo nhận thông báo đơn hàng (ngăn cách bằng dấu phẩy) ---
ZALO_GROUP_IDS = [
    x.strip() for x in os.getenv("ZALO_GROUP_IDS", "").split(",") if x.strip()
]

# --- 7. Vũ Bel API (https://api.vubel.store) & Hòm Thư Ảo ---
VUBEL_API_BASE = os.getenv("VUBEL_API_BASE", "https://api.vubel.store").strip().rstrip("/")
VUBEL_API_KEY = os.getenv("VUBEL_API_KEY", "vubel_0ad7828dd54516eedcebc148456f2d20").strip()
DEFAULT_PROXY = os.getenv("DEFAULT_PROXY", "http://163.61.183.185:38888").strip()
PROXY_LIST = [
    p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()
]

# --- 8. ViOTP API (Thuê SIM nhận mã OTP) ---
VIOTP_TOKEN = os.getenv("VIOTP_TOKEN", "").strip()
# Mặc định Service ID 4 = Shopee / ShopeePay (5k) chuẩn nhận OTP Shopee
VIOTP_SERVICE_ID = int(os.getenv("VIOTP_SERVICE_ID", "4"))
