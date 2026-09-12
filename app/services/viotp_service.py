"""
Dịch vụ Thuê SIM nhận mã OTP tự động (Kết nối qua ViOTP Core).
Bảo mật toàn bộ thông tin đối tác & nghiệp vụ lõi:
- Khách hàng chỉ thấy dịch vụ 'Thuê SIM OTP Shopee'.
- Thời hạn chờ mã: Đúng 5 phút (300s).
- Quá 5 phút không có mã -> Tự động hoàn tiền 100% vào số dư ví của khách.
"""

import time
import logging
import threading
from datetime import datetime
from typing import Dict, Any
import httpx
from sqlalchemy.orm import Session

from app.config import VIOTP_TOKEN, VIOTP_SERVICE_ID
from app.db import SessionLocal
from app.models import Order, User
from .zalo_service import send_zalo_message

logger = logging.getLogger("ViOtpService")

VIOTP_BASE_URL = "https://api.viotp.com"


def request_phone_number(service_id: int = None, network: str = None) -> Dict[str, Any]:
    """
    Thuê số điện thoại mới từ ViOTP.
    Mặc định theo VIOTP_SERVICE_ID trong cấu hình (ví dụ 20 cho Grab giá rẻ 3k).
    """
    token = VIOTP_TOKEN.strip()
    if not token:
        logger.error("Chưa cấu hình VIOTP_TOKEN trong hệ thống.")
        return {"ok": False, "error": "Hệ thống thuê số tạm thời đang nâng cấp. Vui lòng thử lại sau!"}

    target_service = service_id or VIOTP_SERVICE_ID or 20
    url = f"{VIOTP_BASE_URL}/request/getv2"
    params = {
        "token": token,
        "serviceId": target_service
    }
    if network:
        params["network"] = network

    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(url, params=params)
            data = resp.json()
            if data.get("status_code") == 200 and data.get("data"):
                r_data = data["data"]
                return {
                    "ok": True,
                    "request_id": r_data.get("request_id"),
                    "phone_number": r_data.get("phone_number"),
                    "balance": r_data.get("balance"),
                    "network": network or "Auto"
                }
            err_msg = data.get("message") or "Hệ thống đang tạm hết số sạch lúc này. Vui lòng thử lại sau 2 phút!"
            return {"ok": False, "error": err_msg}
    except Exception as ex:
        logger.error("Lỗi kết nối ViOTP request_phone_number: %s", ex)
        return {"ok": False, "error": "Lỗi kết nối máy chủ cấp số. Vui lòng thử lại sau ít phút!"}


def request_highlands_phone() -> Dict[str, Any]:
    """
    Thuê số điện thoại cho dịch vụ Highlands Coffee (Service ID: 508).
    Mặc định ưu tiên nhà mạng: VIETTEL -> VIETNAMOBILE -> Mọi nhà mạng.
    """
    # 1. Thử ưu tiên mạng VIETTEL
    res_viettel = request_phone_number(service_id=508, network="VIETTEL")
    if res_viettel.get("ok"):
        res_viettel["carrier"] = "Viettel"
        return res_viettel

    # 2. Nếu Viettel tạm hết số, chuyển sang VIETNAMOBILE
    res_vnmb = request_phone_number(service_id=508, network="VIETNAMOBILE")
    if res_vnmb.get("ok"):
        res_vnmb["carrier"] = "Vietnamobile"
        return res_vnmb

    # 3. Nếu cả 2 đều tạm hết, gọi chế độ tự động
    res_auto = request_phone_number(service_id=508)
    if res_auto.get("ok"):
        res_auto["carrier"] = "Đầu số sạch"
        return res_auto

    return {"ok": False, "error": "Hệ thống tạm thời hết đầu số Highlands khả dụng. Vui lòng thử lại sau ít phút!"}


def check_otp_status(request_id: int) -> Dict[str, Any]:
    """
    Kiểm tra trạng thái tin nhắn SMS chứa mã OTP của request_id.
    Status:
    0: Đang chờ tin nhắn
    1: Thành công (có Code & SmsContent)
    2: Hết hạn / Timeout
    """
    token = VIOTP_TOKEN.strip()
    if not token:
        return {"ok": False, "error": "Chưa cấu hình VIOTP_TOKEN"}

    url = f"{VIOTP_BASE_URL}/session/getv2"
    params = {
        "token": token,
        "requestId": request_id
    }

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(url, params=params)
            data = resp.json()
            if data.get("status_code") == 200 and data.get("data"):
                s_data = data["data"]
                return {
                    "ok": True,
                    "status": s_data.get("Status"),
                    "code": s_data.get("Code"),
                    "sms_content": s_data.get("SmsContent"),
                    "phone": s_data.get("Phone")
                }
            return {"ok": False, "error": data.get("message") or "Lỗi đọc tin"}
    except Exception as ex:
        logger.warning("Lỗi kiểm tra session ViOTP %s: %s", request_id, ex)
        return {"ok": False, "error": str(ex)}


def _otp_poll_worker(
    order_id: int,
    request_id: int,
    zalo_user_id: str,
    phone: str,
    timeout_seconds: int = 300,
    service_title: str = "Shopee",
    app_name: str = "Shopee"
):
    """
    Worker chạy ngầm theo dõi mã OTP trong tối đa 5 phút (300 giây).
    Poll mỗi 3.5 giây.
    """
    start_time = time.time()
    logger.info("Bắt đầu poll OTP cho Đơn #%s [%s], RequestID=%s, Phone=%s (Hạn %ss)...", order_id, service_title, request_id, phone, timeout_seconds)

    while (time.time() - start_time) < timeout_seconds:
        time.sleep(3.5)
        res = check_otp_status(request_id)
        if not res.get("ok"):
            continue

        status = res.get("status")
        # Status = 1: Đã nhận được tin nhắn SMS chứa mã OTP!
        if status == 1 and res.get("code"):
            otp_code = res.get("code")
            sms_raw = res.get("sms_content") or ""
            logger.info("Đã nhận được OTP %s cho Đơn #%s [%s]!", otp_code, order_id, service_title)

            # Cập nhật đơn hàng trong DB
            try:
                db: Session = SessionLocal()
                order = db.query(Order).filter(Order.id == order_id).first()
                if order:
                    order.status = "completed"
                    order.completed_at = datetime.utcnow()
                    order.account_delivered = f"SĐT: {phone} | OTP: {otp_code}\nTin nhắn: {sms_raw}"
                    db.commit()
                db.close()
            except Exception as dbe:
                logger.error("Lỗi cập nhật CSDL khi nhận OTP: %s", dbe)

            # Bắn tin nhắn Zalo gửi OTP tức thì cho khách
            if "highland" in service_title.lower():
                success_msg = (
                    f"📩 ĐÃ CÓ MÃ OTP HIGHLANDS COFFEE! ☕🎉\n"
                    f"───────────────────────\n"
                    f"🔑 MÃ XÁC MINH: 👉 {otp_code} 👈\n"
                    f"📋 Chạm copy mã: {otp_code}\n\n"
                    f"📞 SĐT đăng ký: {phone}\n"
                    f"⏰ Nhận lúc: {datetime.now().strftime('%H:%M:%S')}\n"
                    f"───────────────────────\n"
                    f"📌 3 BƯỚC NHẬN VOUCHER LY 29K:\n"
                    f"1️⃣ Dán mã {otp_code} vào app Highlands Coffee.\n"
                    f"2️⃣ Bấm 'BỎ QUA' bước nhập mã giới thiệu (Không nhập mã).\n"
                    f"3️⃣ Sau khi vào app ➔ ĐỢI ĐÚNG 2 PHÚT, voucher ly Cà phê Sữa Đá 29k sẽ tự động xuất hiện trong mục 'Ưu đãi' của bạn! ✨\n\n"
                    f"💬 Tin nhắn SMS: \"{sms_raw}\""
                )
            else:
                success_msg = (
                    f"📩 ĐÃ CÓ MÃ OTP {service_title.upper()}! 🎉\n"
                    f"───────────────────────\n"
                    f"🔑 MÃ XÁC MINH: 👉 {otp_code} 👈\n\n"
                    f"📞 Số điện thoại: {phone}\n"
                    f"⏰ Nhận lúc: {datetime.now().strftime('%H:%M:%S')}\n"
                    f"───────────────────────\n"
                    f"💬 Tin nhắn: \"{sms_raw}\"\n\n"
                    f"👉 Nhập mã vào app {app_name} ngay nhé! ✨"
                )
            send_zalo_message(zalo_user_id, success_msg)
            return

        # Status = 2: ViOTP báo hết hạn sớm
        if status == 2:
            break

    # KHI HẾT 5 PHÚT (TIMEOUT) MÀ CHƯA CÓ MÃ: TỰ ĐỘNG HOÀN TIỀN 100%
    logger.info("Hết hạn 5 phút chờ OTP cho Đơn #%s [%s]. Tiến hành hoàn tiền...", order_id, service_title)
    try:
        db: Session = SessionLocal()
        order = db.query(Order).filter(Order.id == order_id).first()
        refund_amount = 5000.0
        if order and order.status != "completed":
            refund_amount = float(order.price)
            order.status = "cancelled"
            order.account_delivered = f"SĐT: {phone} (Hết hạn 5 phút, đã hoàn {int(refund_amount):,}đ vào ví)"

            # Hoàn tiền vào tài khoản User
            user = db.query(User).filter(User.id == order.user_id).first()
            if user:
                user.balance = float(user.balance or 0.0) + refund_amount
            db.commit()
        db.close()

        # Thông báo hoàn tiền tới khách hàng
        refund_msg = (
            f"⏱️ HẾT THỜI GIAN CHỜ MÃ (5 PHÚT) ⚠️\n"
            f"───────────────────────\n"
            f"📞 Số điện thoại: {phone}\n"
            f"Chưa nhận được tin nhắn OTP từ {service_title}.\n\n"
            f"💰 HỆ THỐNG ĐÃ TỰ ĐỘNG HOÀN LẠI {int(refund_amount):,} VNĐ VÀO VÍ CỦA BẠN!\n"
            f"👉 Bạn có thể soạn 'MENU' để thuê số điện thoại khác ngay lập tức nhé! ✨"
        )
        send_zalo_message(zalo_user_id, refund_msg)
    except Exception as dbe:
        logger.error("Lỗi hoàn tiền cho đơn #%s: %s", order_id, dbe)


def start_otp_polling(
    order_id: int,
    request_id: int,
    zalo_user_id: str,
    phone: str,
    timeout_seconds: int = 300,
    service_title: str = "Shopee",
    app_name: str = "Shopee"
):
    """
    Khởi chạy luồng theo dõi OTP ngầm.
    """
    thread = threading.Thread(
        target=_otp_poll_worker,
        args=(order_id, request_id, zalo_user_id, phone, timeout_seconds, service_title, app_name),
        daemon=True
    )
    thread.start()
