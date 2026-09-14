"""
Dịch vụ tích hợp SePay: Tạo mã QR thanh toán động và Xử lý Webhook giao dịch.
Tự động bàn giao tài khoản, thuê số OTP Shopee / Highlands, và xử lý chuyển tiền 2 lần.
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
    VIOTP_SERVICE_ID,
)
from app.models import Order, ProductStock
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
    Thiết kế siêu gọn gàng để hiển thị vừa vặn trọn vẹn trong 1 màn hình điện thoại.
    """
    return (
        f"🛒 ĐƠN HÀNG #{order.order_code}\n"
        f"📦 {product_name} x{order.quantity}\n"
        f"💰 Số tiền: {int(order.price):,} VNĐ\n"
        f"🏦 {SEPAY_BANK} | STK: {SEPAY_ACCOUNT_NO}\n"
        f"👤 {SEPAY_ACCOUNT_NAME}\n"
        f"✍️ Nội dung: {order.order_code}\n"
        f"⚠️ Nhập đúng nội dung để nhận tài khoản tức thì!"
    )


def _send_order_success_log(order: Order, customer=None):
    """Gửi thông báo đơn hàng thanh toán thành công sang Log Bot cho Admin."""
    try:
        from .log_notifier_service import send_log_message
        buyer_name = customer.display_name if customer else "Khách hàng"
        prod_title = order.product.product_name if order.product else f"Sản phẩm #{order.product_id}"
        send_log_message(
            f"💰 [ĐƠN HÀNG MỚI THÀNH CÔNG]\n"
            f"• Khách: {buyer_name}\n"
            f"• Dịch vụ: {prod_title} (SL: {order.quantity})\n"
            f"• Số tiền: {int(order.price):,} VNĐ\n"
            f"• Mã đơn: #{order.order_code}"
        )
    except Exception:
        pass


def fulfill_order(db: Session, order: Order, transfer_amount: Decimal = None) -> dict:
    """
    Thực hiện bàn giao dịch vụ / tài khoản cho đơn hàng đã thanh toán:
    - Thuê SIM OTP Shopee (ViOTP Core: service_id=4)
    - Thuê số Highlands Coffee (ViOTP: service_id=508)
    - Drive / Khóa học số
    - Tài khoản từ kho ProductStock (Shopee, Netflix, Capcut...)
    """
    order_code = order.order_code
    prod_name_lower = order.product.product_name.lower() if order.product else ""
    customer = order.user
    customer_zalo_id = customer.user_id if customer else str(order.user_id)
    actual_amount = transfer_amount if transfer_amount is not None else order.price

    # 0. ĐƠN NẠP TIỀN TRỰC TIẾP VÀO VÍ (LỆNH NAP)
    if order.product_id == 0 or "nạp" in prod_name_lower or "nap" in prod_name_lower:
        if customer:
            customer.balance = float(customer.balance or 0.0) + float(actual_amount)
        order.status = "completed"
        order.completed_at = datetime.utcnow()
        order.account_delivered = f"Nạp thành công +{int(actual_amount):,} VNĐ vào ví"
        db.commit()

        cur_bal = int(customer.balance if customer else 0)
        deposit_msg = (
            f"🎉 NẠP TIỀN THÀNH CÔNG! [Mã #{order_code}] 💳✨\n\n"
            f"💵 Số tiền nạp: +{int(actual_amount):,} VNĐ\n"
            f"💼 Số dư ví hiện tại: {cur_bal:,} VNĐ\n\n"
            f"👉 Bạn có thể dùng số dư ví để đăng ký tài khoản (REG, REGSDT) hoặc mua hàng (BUY) ngay nhé! ✨"
        )
        send_zalo_message(customer_zalo_id, deposit_msg)
        return {"success": True, "message": f"Nạp thành công {int(actual_amount):,}đ vào ví!"}

    # 1. DỊCH VỤ THUÊ SIM / SỐ OTP SHOPEE (ViOTP Service ID 4)
    is_shopee_otp = (
        order.product_id == 6
        or any(k in prod_name_lower for k in [
            "thuê sim", "thue sim", "thuê số", "thue so",
            "otp shopee", "sim shopee", "shopee otp", "sim otp"
        ])
    ) and ("highlands" not in prod_name_lower)

    if is_shopee_otp:
        from .viotp_service import request_phone_number, start_otp_polling
        # Lấy Service ID từ cấu hình hệ thống (mặc định 20 = Grab giá rẻ 3k)
        target_service = VIOTP_SERVICE_ID or 20
        rent_res = request_phone_number(service_id=target_service)
        if rent_res.get("ok"):
            phone = rent_res["phone_number"]
            req_id = rent_res["request_id"]

            order.status = "processing"
            order.completed_at = datetime.utcnow()
            order.account_delivered = f"SĐT: {phone} | RequestID: {req_id} (Đang chờ mã OTP...)"
            db.commit()

            sim_msg = (
                f"📱 ĐÃ CẤP SỐ ĐIỆN THOẠI SHOPEE! [Đơn #{order_code}] 🚀\n\n"
                f"📞 Số điện thoại: {phone}\n"
                f"📋 Chạm copy: {phone}\n"
                f"⏳ Thời gian chờ mã: 5 phút\n\n"
                f"📌 CÁCH NHẬN MÃ OTP:\n"
                f"1️⃣ Mở app/web Shopee, dán số {phone} vào để đăng ký/đăng nhập.\n"
                f"2️⃣ Bấm 'Gửi mã xác nhận qua SMS'.\n"
                f"3️⃣ Giữ nguyên Zalo, bot sẽ tự động gửi mã OTP vào đây ngay khi có tin nhắn! ✨\n\n"
                f"💡 Mẹo: Bạn có thể nhắn 'OTP' bất kỳ lúc nào để bot kiểm tra lấy mã ngay!\n\n"
                f"⚠️ Sau 5 phút nếu không nhận được mã, hệ thống sẽ TỰ ĐỘNG HOÀN TIỀN 100% vào ví của bạn."
            )
            send_zalo_message(customer_zalo_id, sim_msg)

            # Khởi chạy luồng theo dõi OTP ngầm trong 5 phút
            start_otp_polling(
                order.id,
                req_id,
                customer_zalo_id,
                phone,
                timeout_seconds=300,
                service_title="Shopee",
                app_name="Shopee"
            )

            from .group_service import notify_groups_order_completed
            notify_groups_order_completed(db, order)
            _send_order_success_log(order, customer)

            return {"success": True, "message": f"Đơn hàng thuê SIM #{order_code} đã cấp số thành công!"}
        else:
            # Nếu máy chủ cấp số tạm hết số sạch, tự động hoàn trả tiền vào ví khách
            order.status = "cancelled"
            err_reason = rent_res.get("error") or "Hệ thống tạm hết số khả dụng"
            order.account_delivered = f"Hệ thống tạm hết số ({err_reason}), đã hoàn 100% tiền vào ví"
            if customer:
                customer.balance = float(customer.balance or 0.0) + float(order.price)
            db.commit()

            fail_msg = (
                f"⚠️ ĐƠN HÀNG #{order_code} - HỆ THỐNG TẠM HẾT SỐ SẠCH!\n\n"
                f"Lý do: {err_reason}\n"
                f"💰 Bot đã tự động hoàn lại {int(order.price):,} VNĐ vào ví số dư của bạn!\n"
                f"💼 Số dư ví hiện tại: {int(customer.balance):,} VNĐ\n\n"
                f"👉 Bạn có thể soạn 'BUY 6' để mua lại ngay bằng số dư ví khi có số nhé! ✨"
            )
            send_zalo_message(customer_zalo_id, fail_msg)
            return {"success": False, "message": f"Hết số trên hệ thống: {err_reason}"}

    # 2. DỊCH VỤ HIGHLANDS COFFEE (Service ID 508)
    if order.product_id == 7 or "highlands" in prod_name_lower:
        from .viotp_service import request_highlands_phone, start_otp_polling
        rent_res = request_highlands_phone()
        if rent_res.get("ok"):
            phone = rent_res["phone_number"]
            req_id = rent_res["request_id"]
            carrier = rent_res.get("carrier", "Đầu số sạch")

            order.status = "processing"
            order.completed_at = datetime.utcnow()
            order.account_delivered = f"SĐT: {phone} ({carrier}) | RequestID: {req_id} (Đang chờ OTP nhận cafe...)"
            db.commit()

            highlands_msg = (
                f"☕ ĐÃ CẤP SỐ NHẬN CÀ PHÊ HIGHLANDS! [Đơn #{order_code}] 🎉\n\n"
                f"📞 Số điện thoại: {phone} ({carrier})\n"
                f"📋 Chạm copy SĐT: {phone}\n"
                f"⏳ Hạn chờ mã OTP: 5 phút\n\n"
                f"🚀 QUY TRÌNH 4 BƯỚC NHẬN LY CÀ PHÊ 29K:\n"
                f"1️⃣ BẮT BUỘC: XÓA APP Highlands cũ ➔ Lên App Store TẢI LẠI!\n"
                f"2️⃣ Mở app mới tải, dán số: {phone} ➔ Bấm 'Gửi mã OTP'.\n"
                f"3️⃣ Khi app hỏi mã giới thiệu ➔ BẤM 'BỎ QUA' (Không nhập mã).\n"
                f"4️⃣ Vào được app thành công ➔ ĐỢI ĐÚNG 2 PHÚT, voucher ly Cà phê Sữa Đá 29k sẽ tự động xuất hiện trong mục 'Ưu đãi'!\n\n"
                f"✨ Bot sẽ tự động bắt mã OTP gửi vào đây tức thì! (Hoặc nhắn 'OTP' để lấy mã ngay).\n\n"
                f"⚠️ LƯU Ý ĐẶC BIỆT VỀ BẢO HÀNH:\n"
                f"• Dịch vụ tối ưu tự động trên iOS (iPhone/iPad).\n"
                f"• Khách dùng ANDROID vui lòng LIÊN HỆ ADMIN TRƯỚC! Nếu tự ý đăng ký trên Android bị mất tiền mà không có mã thì hệ thống KHÔNG BẢO HÀNH!\n\n"
                f"💰 Quá 5 phút nếu không có mã, hệ thống sẽ TỰ ĐỘNG HOÀN TIỀN 100% vào ví của bạn."
            )
            send_zalo_message(customer_zalo_id, highlands_msg)

            start_otp_polling(
                order.id,
                req_id,
                customer_zalo_id,
                phone,
                timeout_seconds=300,
                service_title="Highlands Coffee ☕",
                app_name="Highlands Coffee để nhận ngay ly Cà Phê Phin Sữa Đá 29k"
            )

            from .group_service import notify_groups_order_completed
            notify_groups_order_completed(db, order)
            _send_order_success_log(order, customer)

            return {"success": True, "message": f"Đơn hàng Highlands #{order_code} đã cấp số thành công!"}
        else:
            order.status = "cancelled"
            order.account_delivered = "Hệ thống tạm hết đầu số Highlands, đã hoàn 100% tiền vào ví"
            if customer:
                customer.balance = float(customer.balance or 0.0) + float(order.price)
            db.commit()

            fail_msg = (
                f"⚠️ ĐƠN HÀNG #{order_code} - HỆ THỐNG TẠM HẾT ĐẦU SỐ HIGHLANDS!\n"
                f"Hệ thống cấp số đang bảo trì hoặc tạm thời hết đầu số khả dụng.\n\n"
                f"💰 Bot đã tự động hoàn lại {int(order.price):,} VNĐ vào ví số dư của bạn!\n"
                f"👉 Bạn có thể thử lại sau ít phút nhé!"
            )
            send_zalo_message(customer_zalo_id, fail_msg)
            return {"success": False, "message": "Hết số trên hệ thống, đã hoàn tiền"}

    # 3. DỊCH VỤ DRIVE / KHÓA HỌC SỐ
    if order.product_id in [3, 5] or "drive" in prod_name_lower:
        stocks = db.query(ProductStock).filter(
            ProductStock.product_id == order.product_id,
            ProductStock.status == "available"
        ).limit(order.quantity).all()

        if stocks:
            delivered_text = "\n".join([f"🔑 Nick: {s.account} | Pass: {s.password}" for s in stocks])
            for s in stocks:
                s.status = "sold"
                s.order_id = order.id
                s.sold_at = datetime.utcnow()
        else:
            delivered_text = order.product.description or "Vui lòng liên hệ Admin để nhận link kích hoạt."

        order.status = "completed"
        order.completed_at = datetime.utcnow()
        order.account_delivered = delivered_text
        db.commit()

        drive_msg = (
            f"🎉 GIAO DỊCH THÀNH CÔNG ĐƠN #{order_code}! 🚀\n\n"
            f"📦 Dịch vụ: {order.product.product_name}\n"
            f"💰 Đã thanh toán: {int(order.price):,} VNĐ\n\n"
            f"📌 THÔNG TIN NHẬN GÓI / TÀI LIỆU CỦA BẠN:\n\n"
            f"{delivered_text}\n\n"
            f"Cảm ơn bạn đã ủng hộ shop! Chúc bạn học tập và làm việc hiệu quả ✨"
        )
        send_zalo_message(customer_zalo_id, drive_msg)

        from .group_service import notify_groups_order_completed
        notify_groups_order_completed(db, order)
        _send_order_success_log(order, customer)

        return {"success": True, "message": f"Đơn hàng #{order_code} đã bàn giao thành công!"}

    # 4. CÁC TÀI KHOẢN TỪ KHO PRODUCT_STOCKS (Shopee, Netflix, Capcut...)
    needed_qty = order.quantity
    stocks = db.query(ProductStock).filter(
        ProductStock.product_id == order.product_id,
        ProductStock.status == "available"
    ).limit(needed_qty).all()

    if len(stocks) < needed_qty:
        msg = (
            f"⚠️ Đã nhận thanh toán đơn hàng #{order_code} ({int(actual_amount):,} VNĐ), "
            f"tuy nhiên kho hàng hiện tại đang hết tài khoản. Hệ thống đã ghi nhận và admin sẽ nạp bổ sung gửi lại cho bạn ngay!"
        )
        send_zalo_message(customer_zalo_id, msg)
        return {"success": False, "message": "Hết hàng trong kho"}

    account_lines = []
    for idx, s in enumerate(stocks, 1):
        s.status = "sold"
        s.order_id = order.id
        s.sold_at = datetime.utcnow()

        item_block = []
        item_block.append(f"🔑 [TÀI KHOẢN #{s.id}]")
        item_block.append(f"👤 Nick: {s.account}")
        item_block.append(f"🔒 Pass: {s.password}")
        if s.sdt:
            item_block.append(f"📱 SĐT: {s.sdt}")
        if s.cookie_spc_f:
            item_block.append(f"🍪 SPC_F: {s.cookie_spc_f}")
        if s.cookie_spc_st:
            item_block.append(f"🍪 SPC_ST: {s.cookie_spc_st}")

        account_lines.append("\n".join(item_block))

    delivered_text = "\n\n".join(account_lines)

    order.status = "completed"
    order.completed_at = datetime.utcnow()
    order.account_delivered = delivered_text
    db.commit()

    first_stock_id = stocks[0].id if stocks else ""
    success_msg = (
        f"🎉 GIAO DỊCH THÀNH CÔNG ĐƠN #{order_code}! 🚀\n\n"
        f"📦 Dịch vụ: {order.product.product_name}\n"
        f"🔢 Số lượng: {order.quantity} tài khoản\n"
        f"💰 Đã thanh toán: {int(order.price):,} VNĐ\n\n"
        f"🔑 THÔNG TIN TÀI KHOẢN CỦA BẠN:\n\n"
        f"{delivered_text}\n\n"
        f"⚡ HƯỚNG DẪN ĐĂNG NHẬP & BẢO MẬT:\n"
        f"1️⃣ Đăng nhập nick vào Shopee bằng Nick & Pass ở trên.\n"
        f"2️⃣ Nếu tài khoản chưa gán mail: Soạn ADDMAIL {first_stock_id}\n"
        f"3️⃣ Khi Shopee yêu cầu xác minh ➔ Chọn 'Xác minh qua Email'.\n"
        f"4️⃣ Quay lại đây soạn: XACMINH {first_stock_id} (Bot tự duyệt login tức thì!)\n\n"
        f"📌 Quản lý toàn bộ nick đã mua: Nhắn DONHANG\n"
        f"Cảm ơn bạn đã ủng hộ shop! Chúc bạn dùng mượt mà ✨"
    )
    send_zalo_message(customer_zalo_id, success_msg)

    from .group_service import notify_groups_order_completed
    notify_groups_order_completed(db, order)
    _send_order_success_log(order, customer)

    return {"success": True, "message": f"Đơn hàng #{order_code} hoàn tất và đã gửi tài khoản!"}


def process_sepay_payment(db: Session, content: str, transfer_amount: Decimal, transfer_type: str) -> dict:
    """
    Xử lý webhook từ SePay:
    1. Chỉ xử lý giao dịch nhận tiền (transferType == 'in')
    2. Quét nội dung chuyển khoản tìm mã đơn DHxxxxxx
    3. Tìm đơn hàng tương ứng
    4. Xử lý trường hợp khách CHUYỂN KHOẢN 2 LẦN (chuyển đúp / chuyển thừa):
       - Tự động nạp tiền vào Số dư ví của khách và báo tin nhắn Zalo tức thì!
    5. Kiểm tra số tiền nhận khớp giá đơn hàng
    6. Tự động bàn giao qua fulfill_order()
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

    customer = order.user
    customer_zalo_id = customer.user_id if customer else str(order.user_id)

    # 4. XỬ LÝ KHÁCH CHUYỂN KHOẢN 2 LẦN / CHUYỂN ĐÚP VÀO ĐƠN ĐÃ XỬ LÝ
    if order.status != "pending":
        # Khách chuyển tiền thêm vào đơn đã hoàn thành hoặc đơn đã hủy/hoàn tiền
        if customer:
            customer.balance = float(customer.balance or 0.0) + float(transfer_amount)
            db.commit()

            double_msg = (
                f"💰 ĐÃ NHẬN KHOẢN CHUYỂN TIỀN BỔ SUNG! [Đơn #{order_code}] 💳✨\n\n"
                f"💵 Số tiền nhận: +{int(transfer_amount):,} VNĐ\n"
                f"ℹ️ Trạng thái: Đơn #{order_code} trước đó đã được xử lý ({order.status}).\n\n"
                f"✅ Bot đã TỰ ĐỘNG NẠP {int(transfer_amount):,} VNĐ VÀO SỐ DƯ VÍ của bạn!\n"
                f"💼 Số dư ví hiện tại: {int(customer.balance):,} VNĐ\n\n"
                f"👉 Bạn có thể soạn lệnh mua (Ví dụ: 'BUY 6' hoặc '1') để mua dịch vụ mới bằng số dư ví mà không cần chuyển khoản nữa nhé! ✨"
            )
            send_zalo_message(customer_zalo_id, double_msg)

        return {
            "success": True,
            "message": f"Đơn hàng {order_code} ở trạng thái '{order.status}'. Đã tự động cộng {int(transfer_amount):,}đ vào ví khách hàng {customer_zalo_id}."
        }

    # 5. Kiểm tra số tiền
    if transfer_amount < order.price:
        msg = f"⚠️ Bạn đã chuyển thiếu tiền cho đơn hàng #{order_code}. Yêu cầu: {int(order.price):,} VNĐ, nhận được: {int(transfer_amount):,} VNĐ. Vui lòng liên hệ hỗ trợ!"
        send_zalo_message(customer_zalo_id, msg)
        return {"success": False, "message": "Số tiền không khớp (thiếu tiền)"}

    # Nếu khách chuyển thừa tiền cho đơn pending, phần thừa tự động nạp vào ví
    if transfer_amount > order.price and customer:
        extra_amount = float(transfer_amount - order.price)
        customer.balance = float(customer.balance or 0.0) + extra_amount
        db.commit()

    # 6. BÀN GIAO ĐƠN HÀNG
    return fulfill_order(db, order, transfer_amount=transfer_amount)
