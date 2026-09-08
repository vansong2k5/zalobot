"""
Dịch vụ tích hợp SePay: Tạo mã QR thanh toán động và Xử lý Webhook giao dịch.
"""

import re
import urllib.parse
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import Session

from app.config import (
    SEPAY_BANK,
    SEPAY_ACCOUNT_NO,
    SEPAY_ACCOUNT_NAME,
)
from app.models import Order, ProductStock, User
from .zalo_service import send_zalo_message


def generate_sepay_qr_url(amount: Decimal | int, order_code: str) -> str:
    """
    Tạo đường dẫn ảnh QR thanh toán tự động thông qua SePay QR Generator.
    Tài liệu: https://qr.sepay.vn
    Cấu trúc: https://qr.sepay.vn/img?acc={acc}&bank={bank}&amount={amount}&des={des}&template=compact
    """
    params = {
        "acc": SEPAY_ACCOUNT_NO,
        "bank": SEPAY_BANK,
        "amount": int(amount),
        "des": order_code,  # Nội dung chuyển khoản chứa mã đơn để SePay Webhook bắt tự động
        "template": "compact",
    }
    query_string = urllib.parse.urlencode(params)
    url = f"https://qr.sepay.vn/img?{query_string}"
    return url


def format_payment_instructions(order: Order, product_name: str, qr_url: str = "") -> str:
    """
    Tạo nội dung hướng dẫn khách quét mã QR SePay để thanh toán (dùng làm caption cho ảnh QR gửi trực tiếp).
    """
    return (
        f"🛒 ĐƠN HÀNG: #{order.order_code}\n"
        f"------------------------------------\n"
        f"📦 Sản phẩm: {product_name}\n"
        f"🔢 Số lượng: {order.quantity}\n"
        f"💰 Tổng tiền: {int(order.price):,} VNĐ\n"
        f"------------------------------------\n"
        f"📱 Quét mã QR trên hoặc chuyển khoản:\n"
        f"• Ngân hàng: {SEPAY_BANK}\n"
        f"• Số tài khoản: {SEPAY_ACCOUNT_NO}\n"
        f"• Chủ tài khoản: {SEPAY_ACCOUNT_NAME}\n"
        f"• Nội dung CK: {order.order_code}\n"
        f"• Số tiền: {int(order.price):,} VNĐ\n\n"
        f"⚠️ QUAN TRỌNG: Nhập ĐÚNG NỘI DUNG '{order.order_code}' để bot tự động gửi tài khoản ngay lập tức!\n"
        f"⏱️ Đơn hàng sẽ tự hủy sau 15 phút nếu chưa thanh toán."
    )



def process_sepay_payment(db: Session, content: str, transfer_amount: Decimal, transfer_type: str) -> dict:
    """
    Xử lý webhook từ SePay:
    1. Chỉ xử lý giao dịch nhận tiền (transferType == 'in')
    2. Quét nội dung chuyển khoản tìm mã đơn DHxxxxxx
    3. Tìm đơn hàng tương ứng
    4. Chống xử lý trùng lặp (Idempotency) nếu đơn hàng đã hoàn tất
    5. Kiểm tra số tiền nhận khớp giá đơn hàng
    6. Lấy tài khoản từ kho cập nhật sang 'sold' và bàn giao cho khách qua Zalo Bot
    7. Trả hoa hồng cho người giới thiệu
    """
    # 1. Chỉ nhận tiền vào
    if transfer_type.lower() != "in":
        return {"success": True, "message": "Bỏ qua giao dịch chuyển tiền đi (out)"}

    # 2. Tìm mã đơn hàng DHxxxxxx trong nội dung chuyển khoản
    match = re.search(r"DH\d{6}", content.upper())
    if not match:
        return {"success": True, "message": "Nội dung chuyển khoản không chứa mã đơn DHxxxxxx"}

    order_code = match.group(0)

    # 3. Tìm đơn hàng
    order = db.query(Order).filter(Order.order_code == order_code).first()
    if not order:
        return {"success": True, "message": f"Không tìm thấy đơn hàng {order_code}"}

    # 4. Kiểm tra xem đơn đã xử lý trước đó chưa (Chống trùng lặp khi SePay retry)
    if order.status == "completed":
        return {"success": True, "message": f"Đơn hàng {order_code} đã hoàn thành trước đó."}

    if order.status != "pending":
        return {"success": True, "message": f"Đơn hàng {order_code} đang ở trạng thái '{order.status}', không thể xử lý."}

    # 5. Kiểm tra số tiền
    if transfer_amount < order.price:
        msg = f"⚠️ Bạn đã chuyển thiếu tiền cho đơn hàng #{order_code}. Yêu cầu: {int(order.price):,} VNĐ, nhận được: {int(transfer_amount):,} VNĐ. Vui lòng liên hệ hỗ trợ!"
        send_zalo_message(order.user.user_id, msg)
        return {"success": False, "message": "Số tiền không khớp (thiếu tiền)"}

    # 6. Lấy tài khoản từ kho
    needed_qty = order.quantity
    stocks = db.query(ProductStock).filter(
        ProductStock.product_id == order.product_id,
        ProductStock.status == "available"
    ).limit(needed_qty).all()

    if len(stocks) < needed_qty:
        msg = (
            f"⚠️ Đã nhận thanh toán đơn hàng #{order_code} ({int(transfer_amount):,} VNĐ), "
            f"tuy nhiên kho hàng hiện tại đang hết tài khoản. Hệ thống đã ghi nhận và admin sẽ nạp bổ sung gửi lại cho bạn ngay!"
        )
        send_zalo_message(order.user.user_id, msg)
        return {"success": False, "message": "Hết hàng trong kho"}

    # Bàn giao tài khoản đầy đủ (hỗ trợ cả Cookie Shopee, SĐT, Khóa học)
    account_lines = []
    for idx, s in enumerate(stocks, 1):
        s.status = "sold"
        s.order_id = order.id
        s.sold_at = datetime.utcnow()

        item_block = []
        if len(stocks) > 1:
            item_block.append(f"🔑 [MỤC {idx}]:")
        item_block.append(f"• Tài khoản: {s.account}")
        item_block.append(f"• Mật khẩu: {s.password}")
        if s.sdt:
            item_block.append(f"• SĐT liên kết: {s.sdt}")
        if s.cookie_spc_f:
            item_block.append(f"• Cookie SPC_F: {s.cookie_spc_f}")
        if s.cookie_spc_st:
            item_block.append(f"• Cookie SPC_ST: {s.cookie_spc_st}")

        account_lines.append("\n".join(item_block))

    delivered_text = "\n\n".join(account_lines)


    # Cập nhật trạng thái đơn hàng
    order.status = "completed"
    order.completed_at = datetime.utcnow()
    order.account_delivered = delivered_text

    # Lưu thay đổi CSDL
    db.commit()

    # Gửi tài khoản cho khách hàng qua Zalo Bot
    customer = order.user
    customer_zalo_id = customer.user_id if customer else str(order.user_id)

    success_msg = (
        f"🎉 THANH TOÁN THÀNH CÔNG ĐƠN HÀNG #{order_code}!\n"
        f"------------------------------------\n"
        f"📦 Sản phẩm: {order.product.product_name}\n"
        f"🔢 Số lượng: {order.quantity}\n"
        f"💰 Đã thanh toán: {int(order.price):,} VNĐ\n"
        f"------------------------------------\n"
        f"🔑 THÔNG TIN TÀI KHOẢN CỦA BẠN:\n"
        f"{delivered_text}\n"
        f"------------------------------------\n"
        f"Cảm ơn bạn đã ủng hộ cửa hàng! Mọi thắc mắc vui lòng liên hệ admin."
    )
    send_zalo_message(customer_zalo_id, success_msg)

    # Gửi thông báo chúc mừng đơn hàng mới vào các Group mà Bot tham gia
    from .group_service import notify_groups_order_completed
    notify_groups_order_completed(db, order)

    return {"success": True, "message": f"Đơn hàng #{order_code} hoàn tất và đã gửi tài khoản!"}
