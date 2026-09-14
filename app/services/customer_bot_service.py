"""
Dịch vụ xử lý tin nhắn và lệnh từ Khách hàng tương tác qua Zalo Bot.
Giao diện Gen Z hiện đại, trực quan, hỗ trợ tương tác mượt mà trong Nhóm (Group) & Chat 1-1.
Tập trung chuyên sâu vào BÁN HÀNG TỰ ĐỘNG & GIAO DỊCH QUA SEPAY:
1. MENU / Danh sách sản phẩm kèm giá & tồn kho trực tiếp (🟢/🔴).
2. Mua hàng siêu tốc: Nhắn số [Mã SP] hoặc [BUY <Mã> <SL>].
3. Tạo đơn hàng tự động & gửi QR SePay chuyển khoản tức thì.
4. Quản lý email, tự lấy OTP và xác minh đăng nhập Shopee hoàn toàn tự động.
"""

import re
import time
import logging
from datetime import datetime
from decimal import Decimal
from typing import Union
from sqlalchemy.orm import Session

logger = logging.getLogger("CustomerBotService")

from app.db import SessionLocal
from app.models import Product, ProductStock, Order, ShopeeRegLog, User
from .task_manager import (
    is_user_busy,
    start_user_task,
    update_user_task_progress,
    request_stop_user_task,
    finish_user_task,
    is_stop_requested,
    is_task_phone_rented,
    is_task_otp_received,
    is_waiting_for_otp,
    submit_user_otp,
)
from .zalo_service import send_zalo_message, send_chat_action, send_zalo_photo, send_zalo_sticker, ZaloTypingKeeper
from .user_service import get_or_create_user, is_admin_user
from .order_service import generate_unique_order_code
from .sepay_service import generate_sepay_qr_url
from .admin_bot_service import handle_admin_message
from .group_service import register_or_update_group
from .vubel_email_service import (
    find_user_stock_by_identifier,
    add_email_to_shopee_account,
    verify_shopee_email_link,
)
from .phone_check_service import (
    extract_phone_number,
    check_shopee_phone,
    format_phone_check_result,
)
from .feature_service import is_feature_active


COMMAND_KEYWORDS = {
    "MENU", "BUY", "HELP", "DONHANG", "SODU", "NAP",
    "REG", "REGSDT", "REGLOG", "OTP",
    "ADDMAIL", "XACMINH",
    "ADDPROXY", "LISTPROXY", "CLEARPROXY",
    "STOP", "TRACK", "CHECKSDT", "SETGROUP", "ADMIN"
}


def clean_zalo_message_text(raw_text: str) -> str:
    """
    Chuẩn hóa nội dung tin nhắn Zalo, tách triệt để mention tag bot trong nhóm.
    Hỗ trợ chuẩn xác:
    - '@Bot Shopee VIP Deal BUY 1' -> 'BUY 1'
    - '@Bot Shopee VIP Deal 1' -> '1'
    - '@Bot Shopee VIP Deal' -> 'MENU'
    - '@Bot Shopee VIP Deal MENU' -> 'MENU'
    - '@Bot Shopee VIP Deal DONHANG' -> 'DONHANG'
    - 'BUY 1' -> 'BUY 1'
    """
    if not raw_text:
        return ""
    text = raw_text.replace("\xa0", " ").strip()
    if not text.startswith("@"):
        return text

    tokens = text.split()
    command_idx = -1
    for i, tok in enumerate(tokens):
        cleaned_tok = tok.upper().strip(":/.,!?#")
        if cleaned_tok in COMMAND_KEYWORDS or cleaned_tok.isdigit() or extract_phone_number(tok):
            command_idx = i
            break

    if command_idx != -1:
        text = " ".join(tokens[command_idx:]).strip()
    else:
        text = "MENU"

    # Chuẩn hóa nếu khách gõ dính phím như BUY6, BUY1, BUY7 -> BUY 6
    match_buy = re.match(r"^BUY(\d+)$", text.upper())
    if match_buy:
        text = f"BUY {match_buy.group(1)}"

    return text


def format_price_tag(amount: Union[Decimal, float, int]) -> str:
    """Format giá tiền theo phong cách Gen Z ngắn gọn (ví dụ 7,000 -> 7k, 60,000 -> 60k)."""
    val = int(amount)
    if val >= 1000 and val % 1000 == 0:
        return f"{val // 1000:,}k"
    return f"{val:,}đ"


def render_product_menu_text(db: Session, target_name: str = "") -> str:
    """Tạo bảng menu sản phẩm phong cách trực quan, bắt mắt."""
    products = db.query(Product).order_by(Product.id.asc()).all()
    greeting = f"👋 Hé lô {target_name}! Chúc bạn săn deal vui vẻ 🚀\n" if target_name else ""

    lines = [
        "✨ 𝐇Ệ 𝐓𝐇Ố𝐍𝐆 𝐓Ự ĐỘ𝐍𝐆 𝟐𝟒/𝟕 ✨",
        greeting,
        "🛒 𝐁Ả𝐍𝐆 𝐆𝐈Á 𝐃Ị𝐂𝐇 𝐕Ụ 𝐒Ẵ𝐍 𝐇À𝐍𝐆:",
    ]

    if not products:
        lines.append("• Hiện kho đang cập nhật thêm dịch vụ mới...")
    else:
        ICONS = {1: "💎", 2: "🎬", 3: "🤖", 4: "🎨", 5: "📚", 6: "📱", 7: "☕"}
        for p in products:
            p_name_lower = p.product_name.lower()
            if "nạp tiền" in p_name_lower or "nap tien" in p_name_lower or p.id == 8 or float(p.price or 0.0) <= 0:
                continue

            if p.status != "active":
                stock_tag = "🔴 Tạm dừng phục vụ (Bảo trì)"
            elif p.id in [3, 5] or "drive" in p_name_lower:
                stock_tag = "🟢 Sẵn hàng 24/7"
            elif p.id == 6 or "thuê sim" in p_name_lower or "otp" in p_name_lower:
                stock_tag = "🟢 Sẵn sàng (Cấp số tự động 24/7)"
            elif p.id == 7 or "highlands" in p_name_lower:
                stock_tag = "🟢 Sẵn sàng (Cấp OTP tự động)"
            else:
                stock = db.query(ProductStock).filter(
                    ProductStock.product_id == p.id,
                    ProductStock.status == "available"
                ).count()
                stock_tag = f"🟢 Sẵn {stock} acc" if stock > 0 else "🔴 Tạm hết"

            price_tag = format_price_tag(p.price)
            ico = ICONS.get(p.id, "📦")
            lines.append(f"{ico} [{p.id}] {p.product_name} ➔ {price_tag}")
            lines.append(f"    └ {stock_tag}\n")

    lines.extend([
        "⚡ 𝐋Ố𝐈 𝐓Ắ𝐂 𝐌𝐔𝐀 𝐒𝐈Ê𝐔 𝐓Ố𝐂:",
        "👉 Nạp tiền vào ví: Nhắn NAP (Tối thiểu 10k)",
        "👉 Mua bằng số dư ví: BUY <Mã> (Ví dụ: BUY 1 - An toàn, tránh bấm nhầm)",
        "👉 Lấy QR chuyển khoản: Nhắn số [Mã] (Ví dụ: 1 hoặc 6)",
        "👉 Mua nhiều cái: BUY <Mã> <SL> (Ví dụ: BUY 1 2)",
        "👉 Check SĐT Shopee: CHECKSDT <SĐT> (hoặc gửi SĐT)",
        "👉 Lấy mã OTP: Nhắn OTP",
        "👉 Xem số dư ví: Nhắn SODU",
        "👉 Lấy nick đã mua: Nhắn DONHANG",
        "👉 Tra cứu SPX: CO <Mã vận đơn>",
        "👉 Hướng dẫn từ A-Z: Nhắn HELP",
    ])
    return "\n".join([line for line in lines if line is not None]).strip()


def handle_zalo_user_message(
    db: Session,
    zalo_user_id: str,
    display_name: str,
    message_text: str,
    sender_id: str = None,
    is_group: bool = False
):
    """
    Xử lý tương tác của khách hàng với Bot (Giao diện Gen Z & Xử lý chuẩn nhóm/tag):
    """
    text = clean_zalo_message_text(message_text)
    if not text:
        return

    # Nhận diện nếu là tin nhắn từ trong Nhóm (Group)
    is_group_chat = is_group or str(zalo_user_id).startswith("zgr-")

    # Xác định user thực tế (người nhắn), tuyệt đối không gán Group ID vào User
    effective_user_id = str(sender_id) if (sender_id and not str(sender_id).startswith("zgr-")) else (
        None if is_group_chat else str(zalo_user_id)
    )

    user = None
    is_first_time = False
    if effective_user_id:
        user, is_first_time = get_or_create_user(db, effective_user_id, display_name)
        if user and user.status == "banned":
            send_zalo_message(zalo_user_id, "⛔ Tài khoản của bạn đã bị tạm khóa tương tác. Vui lòng liên hệ hỗ trợ.")
            return

    # Nếu người dùng nhắn lần đầu qua chat 1-1, gửi sticker chào mừng dễ thương
    if is_first_time and not is_group_chat:
        send_zalo_sticker(zalo_user_id, "c963283f58a364beeb82")

    # Kiểm tra lệnh Quản trị viên Admin trước
    admin_check_id = effective_user_id or zalo_user_id
    if handle_admin_message(db, admin_check_id, text):
        return

    # 1. Chuẩn hóa chuỗi, loại bỏ phần mention @... nếu nhắn trong nhóm
    clean_text = text.strip()
    clean_text = re.sub(r"^@\S+(?:\s+\S+){0,5}?\s+(?=(?:CO|TRACK|CHECK|TRA|MENU|SODU|VI|BUY|DH|SPX|\d)\b)", "", clean_text, flags=re.IGNORECASE).strip()
    if clean_text.startswith("@"):
        t_parts = clean_text.split(None, 1)
        if len(t_parts) > 1:
            clean_text = t_parts[1].strip()

    # 2. Tự động nhận diện mã vận đơn SPX Express (SPXVN...) ở bất kỳ đâu trong tin nhắn
    spx_match = re.search(r"\b(SPXVN[A-Z0-9]+)\b", clean_text, re.IGNORECASE)
    if spx_match:
        if not is_feature_active(db, "TRACK"):
            send_zalo_message(
                zalo_user_id,
                "⚠️ Tính năng TRA CỨU VẬN ĐƠN SPX hiện đang tạm dừng để bảo trì. Vui lòng thử lại sau nhé!"
            )
            return
        spx_code = spx_match.group(1).upper()
        send_chat_action(zalo_user_id, "typing")
        from .tracking_service import format_tracking_result
        tracking_reply = format_tracking_result(db, spx_code)
        send_zalo_message(zalo_user_id, tracking_reply)
        return

    parts = clean_text.split()
    first_word = parts[0].upper() if parts else ""

    user_task_key = effective_user_id or str(zalo_user_id)

    # -------------------------------------------------------------------------
    # QUY TẮC: LỆNH STOP TIẾN TRÌNH ĐANG CHẠY
    # -------------------------------------------------------------------------
    if first_word == "STOP":
        busy, task_info = is_user_busy(user_task_key)
        if busy:
            # Nếu đã nhận thành công mã OTP -> KHÔNG ĐƯỢC DÙNG LỆNH STOP
            if is_task_otp_received(user_task_key):
                send_zalo_message(
                    zalo_user_id,
                    "⚠️ KHÔNG THỂ DỪNG TIẾN TRÌNH LÚC NÀY!\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "Hệ thống ĐÃ NHẬN THÀNH CÔNG MÃ OTP và đang trong bước tạo tài khoản.\n"
                    "Lệnh STOP không được phép sử dụng ở bước này vì sẽ làm mất tiền và không được hoàn lại!\n"
                    "👉 Vui lòng đợi vài giây để bot hoàn tất lưu tài khoản cho bạn nhé. ✨"
                )
                return

            request_stop_user_task(user_task_key)
            is_rented, r_phone = is_task_phone_rented(user_task_key)
            phone_notice = ""
            if is_rented:
                p_text = f" (SĐT: {r_phone})" if r_phone else ""
                phone_notice = (
                    f"⚠️ LƯU Ý: Tiến trình ĐÃ THUÊ SỐ ĐIỆN THOẠI{p_text} từ tổng đài và đang trong giai đoạn tạo tài khoản.\n"
                    f"Khoản chi phí thuê số của nick này đã được trừ và không thể hoàn lại!\n\n"
                )
            send_zalo_message(
                zalo_user_id,
                f"🛑 ［ĐÃ PHÁT LỆNH DỪNG TIẾN TRÌNH］\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 Nhiệm vụ: {task_info.get('task_name', 'Đang xử lý')}\n"
                f"📍 Bước hiện tại: {task_info.get('current_step', 'Chờ phản hồi')}\n\n"
                f"{phone_notice}"
                f"⏳ Bot đang an toàn đóng trình duyệt và dừng tác vụ.\n"
                f"👉 Sau vài giây bạn có thể thực hiện thao tác mới bình thường! ✨"
            )
        else:
            send_zalo_message(zalo_user_id, "ℹ️ Hiện tại bạn không có tiến trình nào đang chạy.")
        return

    # -------------------------------------------------------------------------
    # BẮT MÃ OTP TỪ NGƯỜI DÙNG KHI REG BẰNG SĐT RIÊNG
    # -------------------------------------------------------------------------
    if is_waiting_for_otp(user_task_key):
        otp_candidate = None
        if first_word == "OTP" and len(parts) >= 2:
            otp_candidate = re.sub(r"\D", "", parts[1])
        elif clean_text.strip().isdigit() and 4 <= len(clean_text.strip()) <= 8:
            otp_candidate = clean_text.strip()
        elif re.search(r"\b\d{6}\b", clean_text):
            m = re.search(r"\b\d{6}\b", clean_text)
            otp_candidate = m.group(0)

        if otp_candidate:
            ok_sub = submit_user_otp(user_task_key, otp_candidate)
            if ok_sub:
                send_zalo_message(zalo_user_id, f"✅ Đã tiếp nhận mã OTP: {otp_candidate}! Đang tiến hành xác thực trên Shopee...")
                return

    # Lệnh lưu/kích hoạt nhóm trực tiếp khi gõ trong Group
    if is_group_chat and first_word == "SETGROUP":
        register_or_update_group(db, zalo_user_id)
        send_zalo_message(
            zalo_user_id,
            "✅ Nhóm Zalo này đã được kết nối tự động thành công! 🎉"
        )
        return

    # Khách gõ nhanh 1 con số (ví dụ: '1') để mua sản phẩm mã 1 (tối đa 3 chữ số, không phải SĐT)
    # Kiểm tra tính năng QUICK_BUY: nếu admin dừng tính năng QUICK_BUY thì bỏ qua việc nhận diện số trần trụi
    is_quick_buy_number = (
        is_feature_active(db, "QUICK_BUY")
        and len(parts) == 1
        and parts[0].isdigit()
        and len(parts[0]) <= 3
        and not extract_phone_number(parts[0])
    )

    # =========================================================================
    # LỆNH 1: XEM MENU SẢN PHẨM (MENU)
    # =========================================================================
    if first_word == "MENU":
        send_chat_action(zalo_user_id, "typing")
        target_name = (display_name or (user.display_name if user else "")) if not is_group_chat else ""
        menu_text = render_product_menu_text(db, target_name)
        send_zalo_message(zalo_user_id, menu_text)
        return

    # =========================================================================
    # LỆNH 1.1: XEM SỐ DƯ VÍ (SODU)
    # =========================================================================
    if first_word == "SODU":
        send_chat_action(zalo_user_id, "typing")
        user_bal = float(user.balance or 0.0) if user else 0.0
        c_name = display_name or (user.display_name if user else "Khách hàng")
        target_chat_id = effective_user_id if is_group_chat and effective_user_id else zalo_user_id

        wallet_msg = (
            f"💼 THÔNG TIN VÍ & SỐ DƯ CỦA BẠN 💳\n\n"
            f"👤 Khách hàng: {c_name}\n"
            f"💰 Số dư khả dụng: {int(user_bal):,} VNĐ\n\n"
            f"⚡ TIỆN ÍCH DÀNH CHO BẠN:\n"
            f"• Dùng số dư ví để mua trực tiếp mọi tài khoản & dịch vụ trên bot.\n"
            f"• Khi đủ tiền trong ví, bạn chỉ cần soạn mua (Ví dụ: '1' hoặc 'BUY 1 2'), hệ thống sẽ TỰ ĐỘNG TRỪ VÍ và cấp số tức thì không cần chuyển khoản!\n"
            f"• Nạp tiền vào ví: Vui lòng liên hệ trực tiếp Admin để được cộng số dư.\n\n"
            f"👉 Soạn 'MENU' để xem danh sách dịch vụ sẵn hàng nhé! ✨"
        )

        if is_group_chat and effective_user_id:
            sent_p = send_zalo_message(target_chat_id, wallet_msg)
            if sent_p:
                send_zalo_message(
                    zalo_user_id,
                    f"🔒 @{display_name} Bot đã gửi thông tin số dư ví vào tin nhắn riêng Zalo của bạn nhé! ✨"
                )
            else:
                send_zalo_message(zalo_user_id, wallet_msg)
        else:
            send_zalo_message(zalo_user_id, wallet_msg)
        return

    # =========================================================================
    # LỆNH 1.2: NẠP TIỀN VÀO VÍ (TẠM ĐÓNG CỔNG TỰ ĐỘNG)
    # =========================================================================
    if first_word in ["NAP", "NAPTIEN", "NẠP"]:
        send_chat_action(zalo_user_id, "typing")
        send_zalo_message(
            zalo_user_id,
            "⚠️ CỔNG NẠP TIỀN TỰ ĐỘNG TẠM ĐÓNG!\n"
            "📌 Đang bảo trì nâng cấp, vui lòng liên hệ Admin để được cộng số dư.\n"
            "💡 Vẫn có thể mua từng đơn qua QR: BUY <mã> hoặc gõ số mã (vd: 1)."
        )
        return

    # =========================================================================
    # LỆNH 1.2: TRA CỨU & NHẬN MÃ OTP TỨC THÌ (OTP / LAY OTP / CHECK OTP)
    # =========================================================================
    raw_text_clean = re.sub(r"\s+", " ", text.upper().strip())
    if first_word in ["OTP", "LAYOTP", "CHECKOTP", "MAOTP"] or raw_text_clean in [
        "OTP", "LAY OTP", "LẤY OTP", "MÃ OTP", "MA OTP", "CHECK OTP", "XEM OTP", "XEMOTP", "XIN OTP", "XIN MA"
    ]:
        if not is_feature_active(db, "OTP"):
            send_zalo_message(
                zalo_user_id,
                "⚠️ Tính năng TRA CỨU & CẤP MÃ OTP hiện đang tạm dừng để bảo trì tổng đài.\n"
                "👉 Vui lòng liên hệ trực tiếp Admin để được hỗ trợ!"
            )
            return
        send_chat_action(zalo_user_id, "typing")
        if not user:
            send_zalo_message(zalo_user_id, "⚠️ Không tìm thấy thông tin tài khoản của bạn. Soạn MENU để bắt đầu nhé!")
            return

        # Tìm đơn hàng thuê SIM gần nhất của user có RequestID
        recent_order = db.query(Order).filter(
            Order.user_id == user.id,
            Order.account_delivered.like("%RequestID:%")
        ).order_by(Order.id.desc()).first()

        if not recent_order:
            # Kiểm tra xem có đơn OTP nào đã nhận thành công gần đây không
            completed_otp_order = db.query(Order).filter(
                Order.user_id == user.id,
                Order.account_delivered.like("%OTP:%")
            ).order_by(Order.id.desc()).first()

            if completed_otp_order:
                send_zalo_message(
                    zalo_user_id,
                    f"📩 ĐƠN HÀNG GẦN NHẤT CỦA BẠN [Đơn #{completed_otp_order.order_code}]:\n\n"
                    f"{completed_otp_order.account_delivered}\n\n"
                    f"👉 Nếu muốn thuê số mới, bạn soạn 'BUY 6' nhé! ✨"
                )
            else:
                send_zalo_message(
                    zalo_user_id,
                    "⚠️ Bạn hiện không có đơn thuê số nào đang chờ mã OTP.\n\n"
                    "👉 Soạn 'BUY 6' (hoặc gõ 6) để thuê SIM OTP Shopee ngay nhé! ✨"
                )
            return

        # Đơn hàng đang có RequestID
        delivered_str = recent_order.account_delivered or ""

        # Nếu đơn đã có OTP sẵn trong DB
        if "OTP:" in delivered_str:
            send_zalo_message(
                zalo_user_id,
                f"📩 MÃ OTP CỦA BẠN [Đơn #{recent_order.order_code}]:\n\n"
                f"{delivered_str}\n\n"
                f"👉 Soạn 'BUY 6' để thuê số điện thoại mới nhé! ✨"
            )
            return

        # Nếu đang chờ: bóc tách RequestID & SĐT
        req_match = re.search(r"RequestID:\s*(\d+)", delivered_str)
        phone_match = re.search(r"SĐT:\s*([0-9+]+)", delivered_str)

        if not req_match:
            send_zalo_message(zalo_user_id, f"⚠️ Đơn #{recent_order.order_code} không hợp lệ. Vui lòng liên hệ Admin!")
            return

        req_id = int(req_match.group(1))
        phone = phone_match.group(1) if phone_match else ""
        is_highlands = recent_order.product_id == 7
        srv_title = "Highlands Coffee ☕" if is_highlands else "Shopee"
        app_nm = "Highlands Coffee" if is_highlands else "Shopee"

        from .viotp_service import query_and_fulfill_otp, start_otp_polling
        res = query_and_fulfill_otp(
            order_id=recent_order.id,
            request_id=req_id,
            zalo_user_id=zalo_user_id,
            phone=phone,
            service_title=srv_title,
            app_name=app_nm
        )

        if res.get("done"):
            # Hàm query_and_fulfill_otp đã gửi mã OTP hoặc hoàn tiền rồi
            return

        # Nếu ViOTP vẫn đang chờ tin nhắn
        waiting_msg = (
            f"⏳ ĐANG CHỜ MÃ OTP TỪ NHÀ MẠNG! [Đơn #{recent_order.order_code}]\n\n"
            f"📞 Số điện thoại: {phone}\n"
            f"⏱️ Hạn chờ tối đa: 5 phút\n\n"
            f"📌 HƯỚNG DẪN:\n"
            f"1️⃣ Đảm bảo bạn đã nhập SĐT {phone} vào Shopee và bấm 'Gửi mã xác nhận qua SMS'.\n"
            f"2️⃣ Bot đang tự động theo dõi 24/7. Ngay khi có tin nhắn từ tổng đài, bot sẽ gửi mã ngay vào đây cho bạn!\n\n"
            f"⚠️ Sau 5 phút nếu không có mã, hệ thống sẽ TỰ ĐỘNG HOÀN TIỀN 100% vào ví của bạn."
        )
        send_zalo_message(zalo_user_id, waiting_msg)

        # Kích hoạt lại background poll worker để chắc chắn không bị sót
        start_otp_polling(
            order_id=recent_order.id,
            request_id=req_id,
            zalo_user_id=zalo_user_id,
            phone=phone,
            timeout_seconds=300,
            service_title=srv_title,
            app_name=app_nm
        )
        return

    # =========================================================================
    # LỆNH 1.3: KIỂM TRA SỐ ĐIỆN THOẠI SHOPEE (CHECKSDT <SĐT>)
    # =========================================================================
    phone_to_check = None
    if first_word == "CHECKSDT":
        if not is_feature_active(db, "CHECKSDT"):
            send_zalo_message(
                zalo_user_id,
                "⚠️ Tính năng KIỂM TRA SỐ ĐIỆN THOẠI SHOPEE hiện đang tạm dừng để bảo trì hệ thống.\n"
                "👉 Vui lòng quay lại sau ít phút!"
            )
            return
        phone_to_check = extract_phone_number(text) or (parts[1] if len(parts) >= 2 else None)
        if not phone_to_check:
            send_zalo_message(
                zalo_user_id,
                "📱 KIỂM TRA SỐ ĐIỆN THOẠI SHOPEE (FREE):\n"
                "• CHECKSDT <SĐT> (vd: CHECKSDT 0987654321)\n"
                "• Hoặc gửi thẳng số điện thoại\n"
                "✨ Kiểm tra đầu số sạch, đã đăng ký hay có thể back số!"
            )
            return
    elif first_word not in COMMAND_KEYWORDS and not is_quick_buy_number:
        if is_feature_active(db, "CHECKSDT"):
            phone_to_check = extract_phone_number(text)

    if phone_to_check:
        with ZaloTypingKeeper(zalo_user_id):
            phone_res = check_shopee_phone(phone_to_check)
            formatted_check_msg = format_phone_check_result(phone_res)
            send_zalo_message(zalo_user_id, formatted_check_msg)
        return

    # =========================================================================
    # LỆNH 1.4.1: THÊM PROXY VÀO KHO (ADDPROXY <danh sách proxy>)
    # =========================================================================
    if first_word == "ADDPROXY":
        send_chat_action(zalo_user_id, "typing")
        proxy_raw_input = text[len(parts[0]):].strip()
        if not proxy_raw_input:
            send_zalo_message(
                zalo_user_id,
                "🌐 HƯỚNG DẪN THÊM PROXY:\n"
                "• ADDPROXY 103.152.118.25:8080\n"
                "• ADDPROXY user:pass@104.28.1.1:8080\n"
                "✨ Bot tự test IP sạch trước khi lưu!"
            )
            return

        from app.services.proxy_service import normalize_proxy_url as parse_and_normalize_proxy, validate_proxy_connection, load_admin_proxies, save_admin_proxies

        is_admin = is_admin_user(zalo_user_id) or (sender_id and is_admin_user(sender_id))
        if not is_admin and not is_feature_active(db, "PROXY"):
            send_zalo_message(
                zalo_user_id,
                "⚠️ Tính năng KHO PROXY hiện đang tạm dừng để bảo trì. Vui lòng quay lại sau!"
            )
            return

        if is_admin:
            # ADMIN THÊM PROXY TOÀN HỆ THỐNG DÙNG ĐẾN KHI DIE
            raw_lines = re.split(r"[\r\n;,]+", proxy_raw_input.strip())
            admin_proxies = load_admin_proxies()
            added_admin = []
            failed_admin = []

            for raw in raw_lines:
                cleaned = re.sub(r"^(ADDPROXY|THEMPROXY|PROXY)\s*", "", raw.strip(), flags=re.IGNORECASE).strip()
                if not cleaned:
                    continue
                norm = parse_and_normalize_proxy(cleaned)
                if not norm:
                    failed_admin.append(f"{cleaned} (Định dạng không đúng)")
                    continue

                is_ok, msg, p_info = validate_proxy_connection(norm, timeout=6)
                if is_ok and p_info:
                    if norm in admin_proxies:
                        admin_proxies.remove(norm)
                    admin_proxies.insert(0, norm)
                    added_admin.append(f"{norm} (IP: {p_info.get('ip', 'OK')}, Ping: {p_info.get('latency_ms', 0)}ms)")
                else:
                    failed_admin.append(f"{cleaned} ({msg})")

            if added_admin:
                save_admin_proxies(admin_proxies)
                detail_txt = "\n".join([f"• {p}" for p in added_admin])
                send_zalo_message(
                    zalo_user_id,
                    f"👑 [ADMIN] ĐÃ THIẾT LẬP PROXY HỆ THỐNG TOÀN CỤC! 🎉\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🟢 Proxy đang kích hoạt:\n{detail_txt}\n\n"
                    f"⚡ CƠ CHẾ HOẠT ĐỘNG:\n"
                    f"• Mọi lượt Reg của khách & admin sẽ tự động dùng Proxy này!\n"
                    f"• Hệ thống giữ Proxy này chạy liên tục cho đến khi bị die.\n"
                    f"• Nếu die hoặc không có proxy, hệ thống sẽ tự động fallback về IP mặc định như trước giờ! 🚀"
                )
            else:
                fail_txt = "\n".join([f"• {f}" for f in failed_admin])
                send_zalo_message(
                    zalo_user_id,
                    f"⚠️ [ADMIN] KHÔNG THỂ LƯU PROXY HỆ THỐNG:\n\n{fail_txt}\n\n"
                    f"👉 Vui lòng kiểm tra lại địa chỉ proxy hoặc cổng kết nối!"
                )
            return

        # Khách thường -> Thêm vào kho cá nhân
        from .user_proxy_service import add_user_proxies
        res = add_user_proxies(user_task_key, proxy_raw_input)
        if res.get("ok"):
            details_str = "\n".join([f"• IP: {p}" for p in res.get("added_details", [])])
            msg = (
                "✅ ĐÃ NẠP PROXY VÀO KHO CỦA BẠN!\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🟢 Thêm thành công: {res.get('added_count')} proxy\n"
                f"{details_str}\n\n"
                f"📦 Tổng kho hiện có: {res.get('total_active')} proxy khả dụng!\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "👉 Bạn đã sẵn sàng tạo tài khoản! Soạn:\n"
                "• REG <số_lượng> : Thuê SIM tự động (6,000đ/nick)\n"
                "• REGSDT <SĐT> : Dùng SĐT của bạn (1,000đ/nick)"
            )
            send_zalo_message(zalo_user_id, msg)
        else:
            err_details = "\n".join([f"• {f}" for f in res.get("failed_details", [])])
            send_zalo_message(
                zalo_user_id,
                f"⚠️ KHÔNG THỂ LƯU PROXY:\n\n"
                f"{res.get('message')}\n"
                f"{err_details}\n\n"
                f"👉 Vui lòng kiểm tra lại Proxy (phải còn sống và không bị Shopee chặn)!"
            )
        return

    # =========================================================================
    # LỆNH 1.4.2: XEM VÀ QUẢN LÝ KHO PROXY (LISTPROXY / CLEARPROXY)
    # =========================================================================
    if first_word in ["LISTPROXY", "CLEARPROXY"]:
        is_admin = is_admin_user(zalo_user_id) or (sender_id and is_admin_user(sender_id))
        if not is_admin and not is_feature_active(db, "PROXY"):
            send_zalo_message(
                zalo_user_id,
                "⚠️ Tính năng KHO PROXY hiện đang tạm dừng để bảo trì. Vui lòng quay lại sau!"
            )
            return

    if first_word == "LISTPROXY":
        send_chat_action(zalo_user_id, "typing")
        from .user_proxy_service import get_user_proxy_stats
        stats = get_user_proxy_stats(user_task_key)
        if stats["total"] == 0:
            send_zalo_message(
                zalo_user_id,
                "ℹ️ Kho Proxy của bạn hiện đang TRỐNG!\n\n"
                "👉 Soạn: ADDPROXY <địa chỉ proxy> để nạp proxy vào kho trước khi Reg acc nhé! ✨"
            )
            return

        lines = [f"📦 KHO PROXY ({stats['total']} proxy khả dụng):"]
        for idx, p in enumerate(stats["proxies"][:10], 1):
            lines.append(f"{idx}. {p['url']} ({p['latency_ms']}ms) — {p['created_at']}")
        lines.append("• ADDPROXY <ip:port> — thêm proxy")
        lines.append("• CLEARPROXY — xóa toàn bộ")
        send_zalo_message(zalo_user_id, "\n".join(lines))
        return

    if first_word == "CLEARPROXY":
        send_chat_action(zalo_user_id, "typing")
        from .user_proxy_service import clear_user_proxies
        deleted = clear_user_proxies(user_task_key)
        send_zalo_message(zalo_user_id, f"🗑️ Đã xóa sạch toàn bộ {deleted} proxy trong kho của bạn!")
        return

    # =========================================================================
    # LỆNH ĐĂNG KÝ TÀI KHOẢN SHOPEE (TẠM ĐÓNG ĐỂ BẢO TRÌ NÂNG CẤP)
    # =========================================================================
    if first_word in ["REG", "REGSDT", "REGLOG"]:
        send_chat_action(zalo_user_id, "typing")
        send_zalo_message(
            zalo_user_id,
            "⚠️ 𝐓Í𝐍𝐇 𝐍Ă𝐍𝐆 ĐĂ𝐍𝐆 𝐊Ý 𝐓À𝐈 𝐊𝐇𝐎Ả𝐍 𝐓Ạ𝐌 ĐÓ𝐍𝐆!\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 Tính năng đăng ký tài khoản Shopee tự động hiện đang tạm đóng để nâng cấp hệ thống.\n"
            "👉 Bạn có thể đặt mua tài khoản có sẵn trong kho bằng cách gõ 'MENU' hoặc số thứ tự sản phẩm (Ví dụ: 1, 2, 3).\n"
            "👉 Mọi thắc mắc hoặc cần hỗ trợ vui lòng liên hệ trực tiếp Admin!"
        )
        return

    # =========================================================================
    # LỆNH 2: ĐẶT MUA SẢN PHẨM (BUY <Mã> [SL] hoặc gõ số [Mã])
    # =========================================================================
    if first_word == "BUY" or is_quick_buy_number:
        if not is_feature_active(db, "BUY"):
            send_zalo_message(
                zalo_user_id,
                "⚠️ Tính năng ĐẶT MUA DỊCH VỤ hiện đang tạm dừng để bảo trì / kiểm kê kho.\n"
                "👉 Vui lòng quay lại sau hoặc liên hệ Admin để được hỗ trợ!"
            )
            return
        send_chat_action(zalo_user_id, "typing")

        if is_quick_buy_number:
            product_id = int(parts[0])
            quantity = 1
        else:
            if len(parts) < 2:
                send_zalo_message(
                    zalo_user_id,
                    "⚠️ 𝐇ƯỚ𝐍𝐆 𝐃Ẫ𝐍 𝐌𝐔𝐀 𝐇À𝐍𝐆 𝐒𝐈Ê𝐔 𝐓Ố𝐂:\n\n"
                    "👉 Mua bằng ví (An toàn): BUY <Mã> (Ví dụ: BUY 1)\n"
                    "👉 Mua nhiều cái: BUY <Mã> <Số lượng> (Ví dụ: BUY 1 2)\n"
                    "👉 Lấy QR chuyển khoản: Gõ số [Mã] (Ví dụ: 1 hoặc 6)\n\n"
                    "💡 Nhắn 'MENU' để xem danh sách mã sản phẩm!"
                )
                return

            try:
                product_id = int(parts[1])
            except ValueError:
                send_zalo_message(zalo_user_id, "⚠️ Mã sản phẩm phải là số. Ví dụ soạn: BUY 1")
                return

            quantity = 1
            if len(parts) >= 3:
                try:
                    quantity = int(parts[2])
                    if quantity <= 0:
                        quantity = 1
                    if quantity > 10:
                        send_zalo_message(zalo_user_id, "⚠️ Mỗi lần mua tối đa 10 tài khoản bạn nhé!")
                        return
                except ValueError:
                    quantity = 1

        # Tìm sản phẩm trong DB
        product = db.query(Product).filter(Product.id == product_id).first()
        if not product:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy sản phẩm mã [{product_id}]. Nhắn 'MENU' để xem danh sách nhé!")
            return

        # Kiểm tra nếu dịch vụ đang bị Admin tạm dừng / bảo trì
        if product.status != "active":
            send_zalo_message(
                zalo_user_id,
                f"🛠️ DỊCH VỤ ĐANG TẠM DỪNG BẢO TRÌ!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Dịch vụ: [{product.id}] {product.product_name}\n"
                f"🔧 Trạng thái: 🔴 Tạm ngưng nhận đơn để bảo trì/nâng cấp.\n\n"
                f"👉 Bạn vui lòng quay lại sau ít phút hoặc nhắn 'MENU' để chọn dịch vụ khác nhé! ✨"
            )
            return

        # Kiểm tra tồn kho (Drive, Thuê SIM OTP & Highlands luôn sẵn hàng 24/7, không cần kiểm kho cứng)
        prod_lower = product.product_name.lower()
        is_unlimited = (product.id in [3, 5, 6, 7]) or ("drive" in prod_lower) or ("thuê sim" in prod_lower) or ("otp" in prod_lower) or ("highlands" in prod_lower)

        if not is_unlimited:
            available_stock = db.query(ProductStock).filter(
                ProductStock.product_id == product.id,
                ProductStock.status == "available"
            ).count()

            if available_stock < quantity:
                if available_stock == 0:
                    send_zalo_message(zalo_user_id, f"❌ Dịch vụ '{product.product_name}' hiện tạm hết hàng. Bạn quay lại sau ít phút nhé!")
                else:
                    send_zalo_message(
                        zalo_user_id,
                        f"❌ Dịch vụ '{product.product_name}' hiện chỉ còn {available_stock} tài khoản. Bạn chọn số lượng ít hơn nhé!"
                    )
                return

        # Tính tiền và tạo đơn hàng
        total_price = product.price * quantity
        order_code = generate_unique_order_code(db)

        new_order = Order(
            order_code=order_code,
            user_id=user.id if user else None,
            product_id=product.id,
            quantity=quantity,
            price=total_price,
            status="pending"
        )
        db.add(new_order)
        db.commit()
        db.refresh(new_order)

        # Kiểm tra nếu khách có đủ số dư ví (Balance) để thanh toán ngay
        user_balance = float(user.balance or 0.0) if user else 0.0
        if user and user_balance >= float(total_price):
            # 🛡️ CƠ CHẾ BẢO VỆ VÍ: TRÁNH TRƯỜNG HỢP VÍ CÒN DƯ TIỀN (VD: 10K) VÀ LỠ GÕ NHẦM 1
            # Nếu người dùng CHỈ GÕ SỐ ĐƠN LẺ (is_quick_buy_number) -> TUYỆT ĐỐI KHÔNG TỰ ĐỘNG TRỪ VÍ!
            if is_quick_buy_number:
                wallet_guard_msg = (
                    f"🛡️ ［CẢNH BÁO BẢO VỆ VÍ - TRÁNH BẤM NHẦM］\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📦 Dịch vụ: [{product.id}] {product.product_name}\n"
                    f"💰 Giá thanh toán: {int(total_price):,} VNĐ\n"
                    f"💼 Số dư ví của bạn: {int(user_balance):,} VNĐ (Đủ thanh toán)\n\n"
                    f"⚠️ Bạn vừa chỉ gõ phím số '{parts[0]}'. Nhằm bảo vệ số dư ví và tránh bị trừ tiền oan do lỡ gõ nhầm:\n"
                    f"👉 Để xác nhận DÙNG VÍ mua dịch vụ này, vui lòng soạn rõ cú pháp:\n"
                    f"   BUY {product.id}\n\n"
                    f"💡 Hoặc nếu bạn muốn chuyển khoản ngân hàng SePay:\n"
                    f"   Quét mã QR được gửi bên dưới nhé! 👇"
                )
                send_zalo_message(zalo_user_id, wallet_guard_msg)
            else:
                # Khách gõ rõ ràng lệnh BUY <Mã> -> Xác nhận có chủ đích thanh toán bằng ví
                user.balance = user_balance - float(total_price)
                db.commit()

                send_zalo_message(
                    zalo_user_id,
                    f"💳 TỰ ĐỘNG TRỪ SỐ DƯ VÍ THÀNH CÔNG! [Đơn #{order_code}]\n\n"
                    f"💰 Số tiền: -{int(total_price):,} VNĐ\n"
                    f"💼 Số dư ví còn lại: {int(user.balance):,} VNĐ\n\n"
                    f"🚀 Đang tiến hành cấp dịch vụ ngay tức thì..."
                )

                from .sepay_service import fulfill_order
                fulfill_order(db, new_order, transfer_amount=total_price)
                return

        # Nếu không đủ tiền trong ví -> Tạo mã QR SePay tự động
        qr_url = generate_sepay_qr_url(amount=total_price, order_code=order_code)
        
        # Caption thanh toán Gen Z gọn gàng
        from app.config import SEPAY_BANK, SEPAY_ACCOUNT_NO, SEPAY_ACCOUNT_NAME
        buyer_label = f" @{display_name}" if (is_group_chat and display_name) else ""
        extra_note = ""
        if product.id == 6 or "thuê sim" in prod_lower or ("otp" in prod_lower and "shopee" in prod_lower):
            extra_note = (
                "\n\n📌 LƯU Ý THUÊ SIM OTP SHOPEE:\n"
                "• Thời hạn chờ mã OTP tự động: 5 phút.\n"
                "• Nếu không nhận được mã OTP: Bạn vui lòng đợi hết 5 phút hệ thống sẽ TỰ ĐỘNG HOÀN TIỀN 100% vào ví, sau đó chỉ cần soạn 'BUY 6' là bot tự động trừ ví lấy số mới ngay nhé! ✨"
            )
        elif product.id == 7 or "highlands" in prod_lower:
            extra_note = (
                "\n\n📌 ĐIỀU KIỆN BẮT BUỘC ĐỂ NHẬN VOUCHER 29K:\n"
                "• BẮT BUỘC: XÓA APP Highlands cũ ➔ Tải lại từ App Store! (Không xóa app tải lại sẽ KHÔNG CÓ voucher).\n"
                "• Bỏ qua bước nhập mã giới thiệu ➔ Vào app đợi 2 phút là voucher tự xuất hiện!\n"
                "⚠️ BẢO HÀNH: Tối ưu cho iOS (iPhone/iPad). Máy ANDROID vui lòng nhắn Admin trước! Tự ý mua trên Android lỗi không có mã Admin KHÔNG BẢO HÀNH!"
            )

        caption = (
            f"⚡ ĐƠN HÀNG MỚI: #{order_code}{buyer_label} ⚡\n\n"
            f"📦 Dịch vụ: {product.product_name}\n"
            f"🔢 Số lượng: {quantity} tài khoản\n"
            f"💰 Cần thanh toán: {int(total_price):,} VNĐ\n\n"
            f"🏦 Ngân hàng: {SEPAY_BANK}\n"
            f"💳 Số tài khoản: {SEPAY_ACCOUNT_NO}\n"
            f"👤 Chủ TK: {SEPAY_ACCOUNT_NAME}\n"
            f"✍️ Nội dung CK: {order_code} (Bắt buộc)\n\n"
            f"🚀 Quét QR chuyển khoản nhận mã tự động trong 3 giây!{extra_note}"
        )

        photo_sent = send_zalo_photo(zalo_user_id, photo_url=qr_url, caption=caption)
        if not photo_sent:
            send_zalo_message(zalo_user_id, f"{caption}\n\n🔗 Quét mã QR tại: {qr_url}")
        return

    # =========================================================================
    # LỆNH 3: XEM DANH SÁCH TÀI KHOẢN ĐÃ MUA (DONHANG)
    # =========================================================================
    if first_word == "DONHANG":
        if not is_feature_active(db, "DONHANG"):
            send_zalo_message(
                zalo_user_id,
                "⚠️ Tính năng TRA CỨU ĐƠN HÀNG hiện đang tạm dừng để bảo trì. Vui lòng liên hệ Admin để được hỗ trợ!"
            )
            return
        send_chat_action(zalo_user_id, "typing")
        if not user:
            send_zalo_message(zalo_user_id, "⚠️ Không tìm thấy thông tin tài khoản của bạn.")
            return

        purchased_stocks = db.query(ProductStock).join(Order).filter(
            Order.user_id == user.id,
            Order.status == "completed",
            ProductStock.status == "sold"
        ).order_by(ProductStock.id.desc()).limit(10).all()

        # Lấy thêm các đơn hàng dịch vụ số đã hoàn thành (Highlands, SIM OTP, Drive...)
        digital_orders = db.query(Order).filter(
            Order.user_id == user.id,
            Order.status == "completed",
            Order.account_delivered.isnot(None),
            Order.product_id.in_([3, 5, 6, 7])
        ).order_by(Order.id.desc()).limit(5).all()

        if not purchased_stocks and not digital_orders:
            send_zalo_message(
                zalo_user_id,
                "📦 Bạn chưa có đơn hàng nào trong hệ thống.\n"
                "👉 Nhắn 'MENU' hoặc số '1' để chốt đơn ngay nhé!"
            )
            return

        # Nếu gọi lệnh DONHANG trong Nhóm, gửi riêng để bảo vệ mật khẩu không bị người khác lấy
        target_chat_id = effective_user_id if is_group_chat and effective_user_id else zalo_user_id

        lines = ["📦 LỊCH SỬ ĐƠN HÀNG & TÀI KHOẢN ĐÃ MUA"]

        # 1. Hiển thị tài khoản vật lý (Shopee, Netflix, Capcut...)
        for s in purchased_stocks:
            order_sn = s.order.order_code if s.order else "N/A"
            prod_title = s.product.product_name if s.product else "Tài khoản"
            acc_name = s.account or "user"

            lines.append(f"🔑 [Mã TK: #{s.id}] ➔ Đơn #{order_sn}")
            lines.append(f"• Loại: {prod_title}")
            lines.append(f"• Nick: {acc_name}")
            lines.append(f"• Pass: {s.password}")
            if s.sdt:
                lines.append(f"• SĐT: {s.sdt}")
            lines.append(f"📋 Định dạng: {acc_name}|{s.password}")

            if s.assigned_email:
                lines.append(f"• 📧 Mail: {s.assigned_email}")
                lines.append(f"👉 Duyệt login Shopee: XACMINH {s.id}\n")
            else:
                lines.append("• 📧 Mail: [Chưa gắn]")
                lines.append(f"👉 Thêm mail free: ADDMAIL {s.id}\n")

        # 2. Hiển thị đơn hàng số (Highlands, Thuê SIM OTP, Drive...)
        for d in digital_orders:
            # Nếu đơn này đã được hiển thị qua stock thì bỏ qua để tránh trùng
            if any(s.order_id == d.id for s in purchased_stocks):
                continue
            d_title = d.product.product_name if d.product else "Dịch vụ"
            d_time = d.completed_at.strftime("%H:%M %d/%m") if d.completed_at else ""
            lines.append(f"⚡ [ĐƠN #{d.order_code}] ➔ {d_title} ({d_time})")
            lines.append("• Trạng thái: ✅ Hoàn tất")
            lines.append(f"• Thông tin bàn giao:\n{d.account_delivered}\n")

        lines.extend([
            "",
            "• Gán mail: ADDMAIL <Mã TK>",
            "• Duyệt login: XACMINH <Mã TK>",
            "• OTP SIM: Nhắn OTP | Mua thêm: MENU"
        ])

        msg_content = "\n".join(lines)

        if is_group_chat and effective_user_id:
            # Gửi riêng cho khách
            sent_private = send_zalo_message(target_chat_id, msg_content)
            if sent_private:
                send_zalo_message(
                    zalo_user_id,
                    f"🔒 @{display_name} Bot đã gửi danh sách tài khoản & MẬT KHẨU vào tin nhắn riêng Zalo của bạn để bảo mật nhé! ✨"
                )
            else:
                # Nếu gửi riêng thất bại (chưa mở chat với bot), thông báo khách vào chat riêng
                send_zalo_message(
                    zalo_user_id,
                    f"🔒 @{display_name} Để bảo mật mật khẩu không bị lộ trong nhóm, bạn vui lòng nhắn tin riêng cho Bot soạn 'DONHANG' nhé!"
                )
        else:
            # Chat 1-1 gửi trực tiếp
            send_zalo_message(zalo_user_id, msg_content)
        return

    # =========================================================================
    # LỆNH 4: GÁN EMAIL VÀO TÀI KHOẢN (ADDMAIL <Mã TK> [Email riêng])
    # BẮT BUỘC: 1 NGƯỜI DÙNG 1 LUỒNG 1 PROXY RIÊNG TRONG KHO
    # =========================================================================
    if first_word == "ADDMAIL":
        send_chat_action(zalo_user_id, "typing")
        if not user:
            send_zalo_message(zalo_user_id, "⚠️ Không tìm thấy thông tin tài khoản của bạn.")
            return

        # 1. Kiểm tra quy tắc: 1 người dùng 1 luồng độc quyền
        busy, active_t = is_user_busy(user_task_key)
        if busy:
            send_zalo_message(
                zalo_user_id,
                f"⚠️ 𝐁Ạ𝐍 Đ𝐀𝐍𝐆 𝐂Ó 𝟏 𝐓𝐈Ế𝐍 𝐓𝐑Ì𝐍𝐇 Đ𝐀𝐍𝐆 𝐂𝐇Ạ𝐘!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 Nhiệm vụ: {active_t.get('task_name')}\n"
                f"📍 Tiến độ: {active_t.get('current_step')}\n\n"
                f"👉 Hệ thống chỉ cho phép 1 tiến trình / người dùng tại một thời điểm.\n"
                f"👉 Để dừng tiến trình hiện tại, vui lòng soạn: STOP (hoặc HUY)"
            )
            return

        if len(parts) < 2:
            send_zalo_message(
                zalo_user_id,
                "⚠️ Cú pháp thêm email:\n"
                "• ADDMAIL <Mã TK> (Hệ thống tự cấp hòm thư bảo mật)\n"
                "• ADDMAIL <Mã TK> <email_riêng>\n\n"
                "Ví dụ: ADDMAIL 15"
            )
            return

        identifier = parts[1]
        custom_email = parts[2].strip() if len(parts) >= 3 else None

        stock, err_msg = find_user_stock_by_identifier(db, user.id, identifier)
        if not stock:
            send_zalo_message(zalo_user_id, err_msg or "❌ Không tìm thấy tài khoản hợp lệ.")
            return

        # 2. Cấp Proxy theo thứ tự ưu tiên:
        from .user_proxy_service import get_user_verified_proxies, deactivate_dead_proxy
        from app.services.proxy_service import load_admin_proxies, save_admin_proxies, validate_proxy_connection
        from app.config import DEFAULT_PROXY

        user_proxy = None
        available_proxies, dead_proxies = get_user_verified_proxies(user_task_key, count=1)
        if dead_proxies:
            send_zalo_message(
                zalo_user_id,
                f"🧹 ĐÃ DỌN DẸP KHO PROXY:\n"
                f"⚠️ Phát hiện {len(dead_proxies)} proxy riêng đã hết hạn hoặc mất kết nối và đã được loại bỏ."
            )

        if available_proxies:
            user_proxy = available_proxies[0]
        else:
            # Kiểm tra proxy hệ thống của Admin
            admin_proxies = load_admin_proxies()
            valid_admin = []
            for a_p in admin_proxies:
                is_alive, msg, _ = validate_proxy_connection(a_p, timeout=5)
                if is_alive:
                    valid_admin.append(a_p)
                else:
                    logger.warning(f"Admin proxy {a_p} đã die ({msg}), tự động loại bỏ.")
            if len(valid_admin) != len(admin_proxies):
                save_admin_proxies(valid_admin)

            if valid_admin:
                user_proxy = valid_admin[0]
            else:
                user_proxy = DEFAULT_PROXY or "direct"

        # 3. Khởi tạo khóa tiến trình độc quyền cho User
        task_title = f"Gán Email [#{stock.id}]"
        if not start_user_task(user_task_key, task_title):
            send_zalo_message(zalo_user_id, "⚠️ Không thể bắt đầu tiến trình mới. Vui lòng thử lại sau ít phút hoặc soạn STOP!")
            return

        send_zalo_message(
            zalo_user_id,
            f"⏳ Đang xử lý gán email cho tài khoản [#{stock.id}]...\n"
            f"🌐 Đường truyền riêng: {user_proxy}\n"
            f"Chờ xíu nhé!"
        )

        try:
            update_user_task_progress(user_task_key, "Đang kết nối Shopee Core qua Proxy...")
            result = add_email_to_shopee_account(
                db,
                stock,
                custom_email=custom_email,
                proxy=user_proxy,
                strict_user_proxy=False
            )

            if result.get("ok"):
                mail_assigned = result.get("email") or stock.assigned_email
                if result.get("already_linked"):
                    resp_lines = [
                        f"ℹ️ TÀI KHOẢN [#{stock.id}] ĐÃ CÓ SẴN EMAIL LIÊN KẾT 📧\n",
                        f"• Nick: {stock.account}",
                        f"• Email: {mail_assigned}\n",
                        "📌 CÁCH ĐĂNG NHẬP & DUYỆT NICK:",
                        "1️⃣ Mở Shopee đăng nhập nick & mật khẩu được cấp.",
                        "2️⃣ Khi Shopee yêu cầu xác minh ➔ Chọn 'Xác minh qua Email'.",
                        f"3️⃣ Quay lại đây soạn tin: XACMINH {stock.id}",
                        "\n✨ Bot sẽ tự động quét hòm thư và duyệt đăng nhập thiết bị mới ngay tức thì!"
                    ]
                else:
                    resp_lines = [
                        f"✅ GÁN EMAIL THÀNH CÔNG CHO [#{stock.id}]! 🎉\n",
                        f"• Tài khoản: {stock.account}",
                        f"• Email bảo mật: {mail_assigned}\n",
                        "📌 CÁCH ĐĂNG NHẬP & DUYỆT NICK:",
                        "1️⃣ Mở Shopee đăng nhập nick & mật khẩu được cấp.",
                        "2️⃣ Khi Shopee yêu cầu xác minh ➔ Chọn 'Xác minh qua Email'.",
                        f"3️⃣ Quay lại đây soạn tin: XACMINH {stock.id}",
                        "\n✨ Bot sẽ tự động quét hòm thư và duyệt đăng nhập thiết bị mới ngay tức thì!"
                    ]
                send_zalo_message(zalo_user_id, "\n".join(resp_lines))
            else:
                fail_reason = result.get("error") or "Lỗi kết nối máy chủ."
                send_zalo_message(
                    zalo_user_id,
                    f"❌ Thất bại khi gán mail [#{stock.id}]:\n{fail_reason}\n\n👉 Thử lại sau ít phút nhé!"
                )
        finally:
            finish_user_task(user_task_key)

        return

    # =========================================================================
    # LỆNH 5: HƯỚNG DẪN CHUYỂN ĐỔI KHI KHÁCH HỎI MAIL / DUYỆT ĐĂNG NHẬP
    # =========================================================================
    if first_word in ["GETOTP", "DOCMAIL", "MAIL", "CHECKMAIL"]:
        send_chat_action(zalo_user_id, "typing")
        target_tk = parts[1] if len(parts) >= 2 else ""
        if not target_tk and user:
            latest_s = db.query(ProductStock).join(Order).filter(
                Order.user_id == user.id,
                ProductStock.status == "sold"
            ).order_by(ProductStock.id.desc()).first()
            if latest_s:
                target_tk = str(latest_s.id)

        target_display = target_tk or "<Mã TK>"
        otp_redirect_msg = [
            f"💡 HƯỚNG DẪN XÁC MINH ĐĂNG NHẬP [#{target_display}]:\n",
            "Shopee hiện tại duyệt thiết bị mới trực tiếp qua Email an toàn:",
            "1️⃣ Đăng nhập nick vào Shopee bằng Nick & Pass được cấp.",
            "2️⃣ Khi Shopee yêu cầu xác minh ➔ Bấm chọn 'Xác minh qua Email'.",
            f"3️⃣ Sau đó quay lại đây soạn tin: XACMINH {target_display}\n",
            "🚀 Bot sẽ tự động duyệt đăng nhập thiết bị mới ngay tức thì, không cần nhập OTP thủ công! ✨"
        ]
        send_zalo_message(zalo_user_id, "\n".join(otp_redirect_msg))
        return

    # =========================================================================
    # LỆNH 6: XÁC MINH ĐĂNG NHẬP / DUYỆT THIẾT BỊ SHOPEE (XACMINH <Mã TK>)
    # =========================================================================
    if first_word == "XACMINH":
        send_chat_action(zalo_user_id, "typing")
        if not user:
            send_zalo_message(zalo_user_id, "⚠️ Không tìm thấy thông tin tài khoản của bạn.")
            return

        if len(parts) < 2:
            send_zalo_message(
                zalo_user_id,
                "⚠️ CÚ PHÁP: XACMINH <Mã TK>\n"
                "Ví dụ: XACMINH 3\n\n"
                "👉 Nhắn 'DONHANG' để xem danh sách Mã TK của bạn nhé!"
            )
            return

        identifier = parts[1]
        stock, err_msg = find_user_stock_by_identifier(db, user.id, identifier)
        if not stock:
            send_zalo_message(zalo_user_id, err_msg or "❌ Không tìm thấy tài khoản hợp lệ.")
            return

        if not stock.assigned_email:
            send_zalo_message(
                zalo_user_id,
                f"⚠️ Tài khoản [#{stock.id}] chưa gán Email!\n"
                f"👉 Soạn tin: ADDMAIL {stock.id} trước nhé."
            )
            return

        send_zalo_message(
            zalo_user_id,
            f"⏳ Đang tự động quét hòm thư và duyệt đăng nhập cho [#{stock.id}]...\nChờ xíu nhé!"
        )

        verify_res = verify_shopee_email_link(db, stock)
        if verify_res.get("ok"):
            link_extra = ""
            if verify_res.get("verify_link"):
                link_extra = f"\n🔗 Link xác nhận dự phòng: {verify_res.get('verify_link')}"

            ok_lines = [
                f"✅ DUYỆT ĐĂNG NHẬP THÀNH CÔNG CHO [#{stock.id}]! 🎉\n",
                f"• Tài khoản: {stock.account}",
                f"• Trạng thái: Đã xác nhận thiết bị mới an toàn.{link_extra}",
                "\n👉 Bạn hãy quay lại Shopee, màn hình sẽ tự động chuyển vào tài khoản ngay! Chúc bạn mua sắm vui vẻ ✨"
            ]
            send_zalo_message(zalo_user_id, "\n".join(ok_lines))
        elif verify_res.get("no_mail_pass"):
            no_pass_lines = [
                f"ℹ️ TÀI KHOẢN [#{stock.id}] DÙNG XÁC NHẬN THIẾT BỊ LẠ 📱\n",
                f"• Nick: {stock.account}",
                f"• SĐT nick: {stock.sdt or 'Không có'}\n",
                "📌 ĐỂ ĐĂNG NHẬP VÀO SHOPEE:",
                "👉 Cách 1 (Khuyên dùng): Khi Shopee yêu cầu xác minh, bấm 'Xác minh bằng phương thức khác' ➔ Chọn 'Xác nhận đăng nhập trên thiết bị khác' ➔ Rồi quay lại đây nhắn: XACMINH " + str(stock.id) + " (Bot sẽ tự động bấm Đồng ý!).",
                "👉 Cách 2: Đăng nhập bằng Cookie (SPC_F & SPC_ST) được cấp trong đơn hàng để vào thẳng không cần xác minh."
            ]
            send_zalo_message(zalo_user_id, "\n".join(no_pass_lines))
        else:
            not_found_lines = [
                f"ℹ️ CHƯA THẤY YÊU CẦU XÁC MINH MỚI CHO [#{stock.id}]\n",
                f"• Nick: {stock.account}",
                f"• Email nhận: {stock.assigned_email}\n",
                "📌 BẠN LÀM THEO 3 BƯỚC SAU NHÉ:",
                f"1️⃣ Mở app/web Shopee đăng nhập bằng nick: {stock.account}",
                "2️⃣ Khi Shopee yêu cầu xác minh ➔ Bấm chọn 'Xác minh qua Email'",
                f"3️⃣ Đợi 5-10 giây rồi quay lại đây soạn lại: XACMINH {stock.id}\n",
                "✨ Bot sẽ tự động bắt link và duyệt login cho bạn ngay!"
            ]
            send_zalo_message(zalo_user_id, "\n".join(not_found_lines))
        return

    # =========================================================================
    # LỆNH 7: TRA CỨU ĐƠN HÀNG / MÃ VẬN ĐƠN (TRACK <Mã vận đơn>)
    # =========================================================================
    if first_word == "TRACK":
        send_chat_action(zalo_user_id, "typing")
        if len(parts) < 2:
            send_zalo_message(
                zalo_user_id,
                "🚚 TRA CỨU VẬN ĐƠN SPX EXPRESS (FREE):\n"
                "• TRACK SPXVN061857044824\n"
                "• TRACK DH249985 (đơn nội bộ)\n"
                "✨ Cập nhật lộ trình bưu kiện tức thì!"
            )
            return

        query_code = parts[1].strip()
        from .tracking_service import format_tracking_result
        tracking_reply = format_tracking_result(db, query_code)
        send_zalo_message(zalo_user_id, tracking_reply)
        return

    # =========================================================================
    # LỆNH 8: HƯỚNG DẪN TỔNG HỢP (HELP)
    # =========================================================================
    if first_word in ["HELP", "?"]:
        send_chat_action(zalo_user_id, "typing")
        help_lines = [
            "📖 HƯỚNG DẪN SỬ DỤNG BOT 24/7\n",
            "🛒 MUA HÀNG & VÍ:",
            "• MENU — bảng giá | SODU — số dư",
            "• BUY <mã> <sl> — mua hàng (vd: BUY 1 2)",
            "• DONHANG — nick & pass đã mua\n",
            "🔐 QUẢN LÝ TÀI KHOẢN SHOPEE:",
            "• ADDMAIL <Mã TK> — gán email bảo mật",
            "• XACMINH <Mã TK> — duyệt login qua email\n",
            "🌐 PROXY RIÊNG:",
            "• ADDPROXY <ip:port> — thêm proxy",
            "• LISTPROXY — xem kho | CLEARPROXY — xóa\n",
            "🛠️ TIỆN ÍCH:",
            "• STOP — dừng tiến trình",
            "• CHECKSDT <SĐT> — check F02 Shopee",
            "• TRACK <mã SPX> — tra vận đơn",
            "• HELP — xem lại trang này",
        ]
        if is_admin_user(admin_check_id):
            help_lines.append("\n👑 QUẢN TRỊ: Nhắn 'ADMIN' để mở bảng quản lý Admin.")

        send_zalo_message(zalo_user_id, "\n".join(help_lines))
        return

    # =========================================================================
    # MẶC ĐỊNH: HIỂN THỊ MENU GEN Z BẮT MẮT
    # =========================================================================
    send_chat_action(zalo_user_id, "typing")
    target_name = (display_name or (user.display_name if user else "")) if not is_group_chat else ""
    menu_text = render_product_menu_text(db, target_name)
    send_zalo_message(zalo_user_id, menu_text)