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
from typing import Dict, Any, Optional, Tuple
import httpx
from sqlalchemy.orm import Session

from app.config import VIOTP_TOKEN, VIOTP_SERVICE_ID
from app.db import SessionLocal
from app.models import Order, User
from .zalo_service import send_zalo_message

logger = logging.getLogger("ViOtpService")

VIOTP_BASE_URL = "https://api.viotp.com"


def normalize_vietnamese_phone(phone: str) -> str:
    """Chuẩn hóa số điện thoại Việt Nam về 10 chữ số chuẩn Shopee."""
    import re
    if not phone:
        return ""
    p = re.sub(r"\D", "", phone)
    if p.startswith("84") and len(p) == 11:
        p = "0" + p[2:]
    elif not p.startswith("0") and len(p) == 9:
        p = "0" + p

    # Bản đồ chuyển đổi 11 số sang 10 số (Quy định Bộ TT&TT)
    prefix_map = {
        # Viettel (016x -> 03x)
        "0162": "032", "0163": "033", "0164": "034", "0165": "035",
        "0166": "036", "0167": "037", "0168": "038", "0169": "039",
        # Mobifone (012x -> 07x)
        "0120": "070", "0121": "079", "0122": "077", "0126": "076", "0128": "078",
        # Vinaphone (012x -> 08x)
        "0123": "083", "0124": "084", "0125": "085", "0127": "081", "0129": "082",
        # Vietnamobile (018x -> 05x)
        "0186": "056", "0188": "058",
        # Gmobile
        "0199": "059"
    }
    if len(p) == 11:
        prefix4 = p[:4]
        if prefix4 in prefix_map:
            p = prefix_map[prefix4] + p[4:]
    return p


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
                # Ưu tiên re_phone_number có sẵn số 0 ở đầu (ví dụ 0523047231)
                full_phone = r_data.get("re_phone_number") or r_data.get("phone_number") or ""
                full_phone = normalize_vietnamese_phone(full_phone)

                req_id = r_data.get("request_id")
                return {
                    "ok": True,
                    "request_id": req_id,
                    "phone_number": full_phone,
                    "phone": full_phone,
                    "balance": r_data.get("balance"),
                    "network": network or "Auto"
                }
            err_msg = data.get("message") or "Hệ thống đang kết nối đầu số mới. Vui lòng thử lại sau 1-2 phút!"
            return {"ok": False, "error": err_msg}
    except Exception as ex:
        logger.error("Lỗi kết nối request_phone_number: %s", ex)
        return {"ok": False, "error": "Hệ thống cấp số tạm thời bận. Vui lòng thử lại sau ít phút!"}


def request_shopee_phone_pool(services: list = None, network: str = None) -> Dict[str, Any]:
    """
    Thuê số điện thoại phục vụ đăng ký Shopee qua pool dịch vụ dự phòng:
    - Danh sách dịch vụ: Grab (20), Facebook (7), AWS Amazon (527), Shopee (4)
    - Tự động xáo trộn và thử lần lượt. Nếu dịch vụ này hết số -> tự động nhảy sang dịch vụ còn lại.
    - Hỗ trợ chọn nhà mạng (VIETTEL, VIETNAMOBILE, VINAPHONE, MOBIFONE, ITELECOM, WINTEL hoặc ALL).
    - Bảo mật tuyệt đối: Không để lộ tên dịch vụ bên thứ ba ra ngoài.
    """
    import random
    if not services:
        # Chỉ dùng các dịch vụ 3k: Grab (20), Facebook (7), AWS Amazon (527)
        service_pool = [20, 7, 527]
        random.shuffle(service_pool)
    else:
        # Loại bỏ ID 4 nếu có trong danh sách
        service_pool = [s for s in services if s != 4]
        random.shuffle(service_pool)

    net_param = None if (not network or network.upper() in ("ALL", "TẤT CẢ", "AUTO")) else network.upper()

    for s_id in service_pool:
        res = request_phone_number(service_id=s_id, network=net_param)
        if res.get("ok"):
            logger.info(f"Đã cấp số thành công qua service ID {s_id} (Phone: {res.get('phone')})")
            res["service_id"] = s_id
            return res
        else:
            logger.warning(f"Service ID {s_id} tạm thời chưa có số: {res.get('error')}. Đang chuyển dịch vụ khác trong pool...")

    return {
        "ok": False,
        "error": "Hệ thống tổng đài tạm thời hết đầu số khả dụng. Vui lòng thử lại sau ít phút!"
    }


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
class ViOTPService:
    def __init__(self, token: Optional[str] = None):
        self.token = (token or VIOTP_TOKEN).strip()

    def get_balance(self) -> Tuple[bool, int, str]:
        """Lấy số dư tài khoản thuê SIM."""
        if not self.token:
            return False, 0, "Chưa cấu hình tài khoản hệ thống."
        try:
            url = f"{VIOTP_BASE_URL}/users/balance?token={self.token}"
            with httpx.Client(timeout=10.0) as client:
                r = client.get(url).json()
                if r.get("status_code") == 200:
                    balance = int(r.get("data", {}).get("balance", 0))
                    return True, balance, f"Số dư khả dụng: {balance:,} VNĐ"
                return False, 0, r.get("message", "Lỗi kiểm tra số dư")
        except Exception as e:
            return False, 0, f"Lỗi kết nối máy chủ: {str(e)}"

    def request_phone_number(self, service_id: Optional[int] = None, network: str = "") -> Tuple[bool, Optional[str], Optional[int], str]:
        """
        Cấp số điện thoại mới cho luồng Reg.
        Trả về (success, phone_number, request_id, message)
        """
        res = request_phone_number(service_id=service_id, network=network)
        if res.get("ok"):
            phone = res.get("phone") or res.get("phone_number")
            req_id = res.get("request_id")
            return True, phone, req_id, f"Cấp số thành công: {phone}"
        return False, None, None, res.get("error", "Không thể lấy số điện thoại lúc này.")

    def request_shopee_phone(self, services: Optional[list] = None, network: str = "") -> Tuple[bool, Optional[str], Optional[int], str]:
        """
        Cấp số điện thoại Shopee qua pool dịch vụ (Grab, Facebook, AWS, Shopee) dự phòng tự động.
        """
        res = request_shopee_phone_pool(services=services, network=network)
        if res.get("ok"):
            phone = res.get("phone") or res.get("phone_number")
            req_id = res.get("request_id")
            return True, phone, req_id, "Cấp số thành công"
        return False, None, None, res.get("error", "Hệ thống tổng đài tạm hết đầu số. Vui lòng thử lại sau!")

    def poll_otp(self, request_id: int, timeout_seconds: int = 60, interval: int = 3) -> Tuple[bool, Optional[str], str]:
        """
        Lặp kiểm tra mã OTP trả về cho request_id.
        """
        if not self.token:
            return False, None, "Chưa cấu hình mã kết nối hệ thống."

        import re
        url = f"{VIOTP_BASE_URL}/session/getv2?token={self.token}&requestId={request_id}"
        start_time = time.time()

        with httpx.Client(timeout=8.0) as client:
            while time.time() - start_time < timeout_seconds:
                try:
                    resp = client.get(url)
                    res = resp.json()
                    status = res.get("status_code")
                    data = res.get("data", {})
                    req_status = data.get("Status")

                    if req_status == 1:
                        otp_code = data.get("Code")
                        sms_content = str(data.get("SmsContent") or "")
                        if not otp_code and sms_content:
                            # Tìm 6 chữ số liên tiếp trong tin nhắn SMS
                            m = re.search(r'\b(\d{6})\b', sms_content)
                            if not m:
                                m = re.search(r'\b(\d{4,6})\b', sms_content)
                            if m:
                                otp_code = m.group(1)
                        if otp_code:
                            otp_str = str(otp_code).strip()
                            return True, otp_str, f"Nhận mã OTP thành công: {otp_str}"
                    elif req_status == 2:
                        return False, None, "Yêu cầu cấp mã đã hết hạn."

                except Exception:
                    pass

                time.sleep(interval)

        return False, None, f"Hết thời gian chờ mã OTP ({timeout_seconds}s)."


def resume_pending_otp_polling():
    """
    Quét CSDL các đơn hàng status='pending' đang chờ OTP khi server khởi động lại để tiếp tục poll.
    """
    try:
        from app.db import SessionLocal
        from app.models import Order
        db = SessionLocal()
        pending_orders = db.query(Order).filter(Order.status == "pending").all()
        for o in pending_orders:
            # Bỏ qua các đơn quá cũ (> 10 phút)
            diff = (datetime.utcnow() - o.created_at).total_seconds() if o.created_at else 9999
            if diff < 300 and o.request_id and o.phone:
                remain = max(30, int(300 - diff))
                start_otp_polling(
                    order_id=o.id,
                    request_id=o.request_id,
                    zalo_user_id=o.zalo_user_id or "",
                    phone=o.phone,
                    timeout_seconds=remain,
                    service_title=o.product_title or "Shopee",
                    app_name=o.product_title or "Shopee"
                )
        db.close()
    except Exception as ex:
        logger.warning(f"Lỗi resume pending OTP: {ex}")
