"""
Định nghĩa các Bảng Cơ Sở Dữ Liệu (SQLAlchemy Models) — Chuẩn Star Schema Đa Kênh.
Mô hình hỗ trợ vận hành xuyên suốt nhiều nền tảng: Zalo, Telegram, Discord, Web.

Cấu trúc Star Schema:
- Dimension Tables:
  1. User: Hồ sơ khách hàng trung tâm (Customer Dimension, dùng chung ví balance, role, trạng thái).
  2. UserIdentity: Định danh kênh liên kết (Channel Dimension: Zalo UID, Telegram ChatID, Discord ID).
  3. Product: Danh mục sản phẩm số & dịch vụ.
  4. ProductStock: Kho tài khoản số bàn giao.
  5. UserProxy: Kho proxy gán theo từng khách hàng.
  6. BotGroup: Danh sách nhóm/kênh bot tham gia.

- Fact Tables:
  1. Order: Đơn hàng mua dịch vụ số theo từng kênh.
  2. Transaction: Sổ cái biến động số dư tài chính (Double-entry Ledger).
  3. ShopeeRegLog: Nhật ký tạo tài khoản Shopee tự động.
  4. Withdrawal: Nhật ký rút tiền hoa hồng tiếp thị.
  5. AffiliateHistory: Lịch sử chuyển đổi link affiliate.
"""

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Numeric,
    Text,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from app.db import Base


class User(Base):
    """
    Bảng khách hàng trung tâm (Customer Dimension) trong mô hình Star Schema.
    Quản lý thông tin tài khoản, số dư dùng chung, phân quyền và trạng thái.
    Đã loại bỏ hoàn toàn cột avatar theo yêu cầu nghiệp vụ.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # Mã định danh khách hàng chuẩn hóa (vd: KH10001)
    customer_code = Column(String(30), unique=True, index=True, nullable=True)
    # Mã user_id cũ (tương thích ngược cho các query Zalo hiện tại)
    user_id = Column(String(100), index=True, nullable=True)
    # Tên hiển thị của khách hàng
    display_name = Column(String(150), default="Khách", nullable=False)
    # Số dư ví dùng chung (VNĐ) xuyên suốt Zalo, Telegram, Discord, Web
    balance = Column(Numeric(15, 2), default=0.0, nullable=False)
    # Mã người giới thiệu cũ (Zalo ID dạng text)
    referred_by = Column(String(100), nullable=True)
    # ID khách hàng giới thiệu (Khóa ngoại chuẩn hóa)
    referred_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # Số điện thoại liên hệ chuẩn hóa
    phone_number = Column(String(30), nullable=True)
    # Email khách hàng
    email = Column(String(255), nullable=True)
    # Vai trò: 'customer' (khách lẻ), 'admin' (quản trị), 'moderator'
    role = Column(String(20), default="customer", nullable=False)
    # Trạng thái tài khoản: 'active' (bình thường), 'banned' (bị chặn)
    status = Column(String(20), default="active", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Quan hệ Star Schema
    identities = relationship("UserIdentity", back_populates="user", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="user")
    withdrawals = relationship("Withdrawal", back_populates="user")
    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    proxies = relationship("UserProxy", back_populates="user")
    shopee_logs = relationship("ShopeeRegLog", back_populates="user")


class UserIdentity(Base):
    """
    Bảng liên kết định danh đa nền tảng (Zalo, Telegram, Discord, Web).
    Phân tách rõ ràng ID của từng nền tảng, tìm kiếm O(1) qua Index (platform, platform_user_id),
    tránh hoàn toàn việc quét toàn bảng users.
    """
    __tablename__ = "user_identities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(20), nullable=False, default="zalo", index=True)  # 'zalo', 'telegram', 'discord', 'web'
    platform_user_id = Column(String(100), nullable=False, index=True)  # ID duy nhất trên nền tảng đó
    platform_username = Column(String(100), nullable=True)  # @handle Telegram, username Discord
    platform_display_name = Column(String(150), nullable=True)
    is_primary = Column(Integer, default=1, nullable=False)  # 1: kênh chính, 0: kênh phụ liên kết
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_active_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_platform_user_id"),
        Index("idx_platform_user_lookup", "platform", "platform_user_id"),
    )

    user = relationship("User", back_populates="identities")


class Product(Base):
    """
    Bảng sản phẩm — Chứa thông tin các gói dịch vụ / tài khoản bán ra.
    """
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    product_name = Column(String(150), nullable=False)
    price = Column(Numeric(15, 2), nullable=False)
    description = Column(Text, nullable=True)
    commission_rate = Column(Numeric(5, 2), default=0.0, nullable=False)
    status = Column(String(20), default="active", nullable=False)  # 'active', 'inactive'

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    stocks = relationship("ProductStock", back_populates="product")
    orders = relationship("Order", back_populates="product")


class ProductStock(Base):
    """
    Bảng kho tài khoản — Lưu các tài khoản email/mật khẩu thực tế để giao cho khách.
    """
    __tablename__ = "product_stocks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    account = Column(String(255), nullable=False)
    password = Column(String(255), nullable=False)
    sdt = Column(String(50), nullable=True)
    cookie_spc_f = Column(Text, nullable=True)
    cookie_spc_st = Column(Text, nullable=True)
    status = Column(String(20), default="available", nullable=False, index=True)  # 'available', 'sold'
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)

    assigned_email = Column(String(255), nullable=True)
    email_password = Column(String(255), nullable=True)
    mail_status = Column(String(50), default="none", nullable=True)
    last_otp = Column(String(50), nullable=True)
    last_otp_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sold_at = Column(DateTime, nullable=True)

    product = relationship("Product", back_populates="stocks")
    order = relationship("Order", back_populates="stock_items")


class Order(Base):
    """
    Bảng đơn hàng (Fact Orders) — Quản lý việc mua hàng đa kênh (Zalo, Telegram, Discord).
    """
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    order_code = Column(String(30), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    platform = Column(String(20), default="zalo", nullable=False)  # 'zalo', 'telegram', 'discord', 'web'
    platform_channel_id = Column(String(100), nullable=True)  # ID nhóm/kênh/DM phát sinh đơn
    quantity = Column(Integer, default=1, nullable=False)
    price = Column(Numeric(15, 2), nullable=False)
    status = Column(String(20), default="pending", nullable=False, index=True)  # 'pending', 'completed', 'expired', 'cancelled'
    account_delivered = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="orders")
    product = relationship("Product", back_populates="orders")
    stock_items = relationship("ProductStock", back_populates="order")


class Transaction(Base):
    """
    Bảng sổ cái biến động số dư (Fact Transactions / Double-Entry Financial Ledger).
    Lưu vết từng giao dịch: nạp tiền SePay, thanh toán đơn hàng, cộng hoa hồng, rút tiền, hoàn tiền.
    """
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_code = Column(String(50), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(30), nullable=False, index=True)  # 'deposit', 'purchase', 'commission', 'withdrawal', 'refund', 'admin_adjustment'
    amount = Column(Numeric(15, 2), nullable=False)  # Dương (+) nạp/nhận, Âm (-) chi tiêu
    balance_before = Column(Numeric(15, 2), nullable=False)
    balance_after = Column(Numeric(15, 2), nullable=False)
    platform = Column(String(20), default="zalo", nullable=False)  # 'zalo', 'telegram', 'discord', 'web'
    reference_type = Column(String(30), nullable=True)  # 'order', 'sepay', 'withdrawal', 'shopee_reg'
    reference_id = Column(String(100), nullable=True)  # Mã đơn hàng hoặc mã giao dịch SePay
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="transactions")


class Withdrawal(Base):
    """
    Bảng yêu cầu rút tiền — Xử lý rút hoa hồng tiếp thị của người dùng.
    """
    __tablename__ = "withdrawals"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Numeric(15, 2), nullable=False)
    bank_name = Column(String(100), nullable=False)
    bank_account = Column(String(50), nullable=False)
    account_holder = Column(String(100), nullable=False)
    status = Column(String(20), default="pending", nullable=False, index=True)  # 'pending', 'approved', 'rejected'
    admin_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="withdrawals")


class AffiliateHistory(Base):
    """
    Bảng lịch sử chuyển đổi link Shopee Affiliate của người dùng.
    """
    __tablename__ = "affiliate_history"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    item_id = Column(String(50), nullable=True, index=True)
    product_name = Column(String(300), nullable=True)
    original_url = Column(Text, nullable=False)
    affiliate_url = Column(Text, nullable=False)
    price = Column(Numeric(15, 2), nullable=True)
    commission_rate = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User")


class BotGroup(Base):
    """
    Bảng lưu danh sách các nhóm/kênh đa nền tảng mà bot tham gia.
    """
    __tablename__ = "bot_groups"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    platform = Column(String(20), default="zalo", nullable=False)  # 'zalo', 'telegram', 'discord'
    group_id = Column(String(100), unique=True, index=True, nullable=False)
    group_name = Column(String(255), nullable=True)
    status = Column(String(20), default="active", nullable=False)  # 'active', 'inactive'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class ShopeeRegLog(Base):
    """
    Bảng lưu nhật ký các phiên tạo tài khoản Shopee tự động (Fact Shopee Reg Logs).
    Liên kết chặt chẽ với khách hàng cốt lõi (user_id) và phân loại nền tảng.
    """
    __tablename__ = "shopee_reg_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    platform = Column(String(20), default="zalo", nullable=False)  # 'zalo', 'telegram', 'discord', 'web'
    zalo_user_id = Column(String(100), nullable=True, index=True)  # Legacy compatibility
    phone_number = Column(String(50), nullable=True, index=True)
    username = Column(String(100), nullable=True)
    password = Column(String(100), nullable=True)
    proxy_used = Column(String(255), nullable=True)
    external_ip = Column(String(50), nullable=True)
    status = Column(String(20), default="in_progress", index=True)  # in_progress, success, failed
    is_reclaimed = Column(Integer, default=0)  # 0: số mới, 1: số cũ reclaim
    step_failed = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    spc_st = Column(Text, nullable=True)
    spc_f = Column(Text, nullable=True)
    spc_u = Column(String(50), nullable=True)
    cookie_full = Column(Text, nullable=True)
    logs_detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="shopee_logs")


class UserProxy(Base):
    """
    Kho Proxy của từng người dùng đa kênh (Zalo, Telegram, Discord).
    Liên kết với khách hàng cốt lõi (user_id) và phân loại nền tảng.
    """
    __tablename__ = "user_proxies"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    platform = Column(String(20), default="zalo", nullable=False)  # 'zalo', 'telegram', 'discord', 'web'
    zalo_user_id = Column(String(100), nullable=False, index=True)  # Legacy compatibility
    proxy_url = Column(String(255), nullable=False)
    status = Column(String(20), default="active", index=True)  # 'active', 'used', 'die'
    latency_ms = Column(Integer, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="proxies")


class SystemSetting(Base):
    """
    Bảng cấu hình hệ thống & Trạng thái bật/tắt tính năng (Feature Toggles).
    Quản lý trạng thái động của các tính năng (active, paused) và cấu hình runtime.
    """
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(50), unique=True, index=True, nullable=False)
    value = Column(String(255), default="active", nullable=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
