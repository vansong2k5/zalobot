"""
Định nghĩa các Bảng Cơ Sở Dữ Liệu (SQLAlchemy Models).
Gồm 5 bảng chính:
1. User: Khách hàng mua hàng qua Zalo.
2. Product: Danh mục sản phẩm số (Netflix, Spotify, Canva...).
3. ProductStock: Kho tài khoản số (chứa email/pass của từng sản phẩm).
4. Order: Quản lý đơn hàng mua tài khoản.
5. Withdrawal: Quản lý yêu cầu rút tiền hoa hồng giới thiệu.
"""

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Numeric,
    Text,
    DateTime,
    ForeignKey,
    func
)
from sqlalchemy.orm import relationship
from app.db import Base


class User(Base):
    """
    Bảng người dùng — Lưu trữ thông tin khách hàng tương tác qua Zalo Bot.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # Zalo user_id (định danh người dùng từ Zalo Bot platform)
    user_id = Column(String(50), unique=True, index=True, nullable=False)
    # Tên hiển thị của khách hàng
    display_name = Column(String(150), default="Khách", nullable=False)
    # Số dư tài khoản (tiền hoa hồng tiếp thị được rút)
    balance = Column(Numeric(15, 2), default=0.0, nullable=False)
    # Mã Zalo của người đã giới thiệu khách này (nếu có)
    referred_by = Column(String(50), nullable=True)
    # Số điện thoại của người dùng (nếu lấy được từ Zalo/zca-js)
    phone_number = Column(String(30), nullable=True)
    # URL ảnh đại diện Zalo
    avatar = Column(String(500), nullable=True)
    # Trạng thái tài khoản: 'active' (bình thường), 'banned' (bị chặn)
    status = Column(String(20), default="active", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Quan hệ với đơn hàng và yêu cầu rút tiền
    orders = relationship("Order", back_populates="user")
    withdrawals = relationship("Withdrawal", back_populates="user")


class Product(Base):
    """
    Bảng sản phẩm — Chứa thông tin các gói dịch vụ / tài khoản bán ra.
    """
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # Tên sản phẩm, ví dụ: 'Netflix Premium 1 Tháng'
    product_name = Column(String(150), nullable=False)
    # Giá bán (VNĐ)
    price = Column(Numeric(15, 2), nullable=False)
    # Mô tả ngắn về sản phẩm
    description = Column(Text, nullable=True)
    # Tỷ lệ hoa hồng cho người giới thiệu (%) ví dụ: 10 nghĩa là 10%
    commission_rate = Column(Numeric(5, 2), default=0.0, nullable=False)
    # Trạng thái: 'active' (đang bán), 'inactive' (tạm ngưng)
    status = Column(String(20), default="active", nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Quan hệ
    stocks = relationship("ProductStock", back_populates="product")
    orders = relationship("Order", back_populates="product")


class ProductStock(Base):
    """
    Bảng kho tài khoản — Lưu các tài khoản email/mật khẩu thực tế để giao cho khách.
    """
    __tablename__ = "product_stocks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # Thuộc về sản phẩm nào
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    # Tài khoản / Email đăng nhập
    account = Column(String(255), nullable=False)
    # Mật khẩu đăng nhập
    password = Column(String(255), nullable=False)
    # Số điện thoại (nếu có)
    sdt = Column(String(50), nullable=True)
    # Cookie phiên dự phòng (nếu cần)
    cookie_spc_f = Column(Text, nullable=True)
    cookie_spc_st = Column(Text, nullable=True)
    # Trạng thái: 'available' (còn trong kho), 'sold' (đã bán)
    status = Column(String(20), default="available", nullable=False, index=True)
    # Đã bán cho đơn hàng nào (khi status = 'sold')
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sold_at = Column(DateTime, nullable=True)

    # Quan hệ
    product = relationship("Product", back_populates="stocks")
    order = relationship("Order", back_populates="stock_items")


class Order(Base):
    """
    Bảng đơn hàng — Quản lý việc mua hàng và thanh toán.
    """
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # Mã đơn hàng duy nhất, hiển thị cho khách và dùng làm nội dung chuyển khoản (vd: 'DH100201')
    order_code = Column(String(30), unique=True, index=True, nullable=False)
    # Khách hàng mua
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Sản phẩm đã mua
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    # Số lượng
    quantity = Column(Integer, default=1, nullable=False)
    # Tổng tiền đơn hàng cần thanh toán (VNĐ)
    price = Column(Numeric(15, 2), nullable=False)
    # Trạng thái: 'pending' (chờ thanh toán), 'completed' (hoàn thành), 'expired' (quá hạn), 'cancelled' (đã hủy)
    status = Column(String(20), default="pending", nullable=False, index=True)
    # Dữ liệu tài khoản đã bàn giao (lưu dạng chuỗi để tiện đối chiếu lịch sử)
    account_delivered = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    # Quan hệ
    user = relationship("User", back_populates="orders")
    product = relationship("Product", back_populates="orders")
    stock_items = relationship("ProductStock", back_populates="order")


class Withdrawal(Base):
    """
    Bảng yêu cầu rút tiền — Xử lý rút hoa hồng tiếp thị của người dùng.
    """
    __tablename__ = "withdrawals"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # Người yêu cầu rút
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # Số tiền rút (VNĐ)
    amount = Column(Numeric(15, 2), nullable=False)
    # Tên ngân hàng nhận (vd: 'Vietcombank', 'MBBank', 'BIDV')
    bank_name = Column(String(100), nullable=False)
    # Số tài khoản nhận tiền
    bank_account = Column(String(50), nullable=False)
    # Tên chủ tài khoản
    account_holder = Column(String(100), nullable=False)
    # Trạng thái: 'pending' (chờ duyệt), 'approved' (đã chuyển tiền), 'rejected' (từ chối hoàn lại số dư)
    status = Column(String(20), default="pending", nullable=False, index=True)
    # Ghi chú của Admin (nếu từ chối thì ghi lý do)
    admin_note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)

    # Quan hệ
    user = relationship("User", back_populates="withdrawals")


class AffiliateHistory(Base):
    """
    Bảng lịch sử chuyển đổi link Shopee Affiliate của người dùng.
    Dùng để thống kê số lượng link đã convert và sản phẩm hot.
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
    Bảng lưu danh sách các nhóm Zalo mà bot tham gia để gửi thông báo đơn hàng.
    """
    __tablename__ = "bot_groups"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    group_id = Column(String(100), unique=True, index=True, nullable=False)
    group_name = Column(String(255), nullable=True)
    status = Column(String(20), default="active", nullable=False)  # 'active', 'inactive'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
