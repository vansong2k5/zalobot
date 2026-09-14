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
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import Session

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


def format_price_tag(amount: Decimal | float | int) -> str:
    """Format giá tiền theo phong cách Gen Z ngắn gọn (ví dụ 7,000 -> 7k, 60,000 -> 60k)."""
    val = int(amount)
    if val >= 1000 and val % 1000 == 0:
        return f"{val // 1000:,}k"
    return f"{val:,}đ"


def render_product_menu_text(db: Session, target_name: str = "") -> str:
    """Tạo bảng menu sản phẩm phong cách Gen Z trực quan, bắt mắt."""
    products = db.query(Product).filter(Product.status == "active").order_by(Product.id.asc()).all()
    greeting = f"👋 Hé lô {target_name}! Chúc bạn săn deal vui vẻ 🚀\n" if target_name else ""

    lines = [
        "✨ 𝐇Ệ 𝐓𝐇Ố𝐍𝐆 𝐓Ự ĐỘ𝐍𝐆 𝟐𝟒/𝟕 ✨",
        greeting,
        "🛒 𝐁Ả𝐍𝐆 𝐆𝐈Á 𝐃Ị𝐂𝐇 𝐕Ụ 𝐒Ẵ𝐍 𝐇À𝐍𝐆:",
    ]

    if not products:
        lines.append("• Hiện kho đang cập nhật thêm dịch vụ mới...")
    else:
        # Icon sinh động theo từng loại sản phẩm
        ICONS = {1: "💎", 2: "🎬", 3: "🤖", 4: "🎨", 5: "📚", 6: "📱", 7: "☕"}
        for p in products:
            p_name_lower = p.product_name.lower()
            if p.id in [3, 5] or "drive" in p_name_lower:
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
        "👉 Mua 1 cái: Nhắn số [Mã] (Ví dụ: 1 hoặc 6)",
        "👉 Mua nhiều: BUY <Mã> <SL> (Ví dụ: BUY 1 2)",
        "👉 Check SĐT Shopee: CHECKSDT <SĐT> (hoặc gửi SĐT)",
        "👉 Lấy mã OTP: Nhắn OTP",
        "👉 Xem số dư ví: Nhắn SODU",
        "👉 Lấy nick đã mua: Nhắn DONHANG",
        "👉 Tra cứu SPX: CO <Mã vận đơn>",
        "👉 Hướng dẫn từ A-Z: Nhắn HELP",
    ])
    return "\n".join(lines).strip()


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
    is_quick_buy_number = len(parts) == 1 and parts[0].isdigit() and len(parts[0]) <= 3 and not extract_phone_number(parts[0])

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
            f"• Soạn 'NAP <số_tiền>' để lấy mã QR nạp thêm tiền vào ví.\n\n"
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
    # LỆNH 1.2: NẠP TIỀN VÀO VÍ TỰ ĐỘNG (NAP [Số tiền])
    # =========================================================================
    if first_word == "NAP":
        send_chat_action(zalo_user_id, "typing")
        if not user:
            send_zalo_message(zalo_user_id, "⚠️ Không tìm thấy thông tin tài khoản của bạn.")
            return

        # Hệ thống chỉ hỗ trợ đúng 3 mốc nạp: 10k, 50k, 100k
        deposit_amount = 10000  # Mặc định là 10k nếu chỉ gõ NAP
        if len(parts) >= 2:
            raw_amt = parts[1].strip().lower()
            amt_digits = re.sub(r"\D", "", raw_amt)
            if amt_digits:
                val = int(amt_digits)
                if "k" in raw_amt or (0 < val < 1000):
                    val = val * 1000
                if val <= 20000:
                    deposit_amount = 10000
                elif val <= 70000:
                    deposit_amount = 50000
                else:
                    deposit_amount = 100000

        # Lấy hoặc tạo sản phẩm 'Nạp tiền vào ví' để tránh lỗi ForeignKeyViolation
        deposit_prod = db.query(Product).filter(Product.product_name.ilike("%Nạp tiền%")).first()
        if not deposit_prod:
            deposit_prod = Product(
                product_name="Nạp tiền vào ví",
                price=0,
                description="Đơn nạp tiền tự động vào ví qua SePay",
                status="active"
            )
            db.add(deposit_prod)
            db.commit()
            db.refresh(deposit_prod)

        order_code = generate_unique_order_code(db)
        dep_order = Order(
            order_code=order_code,
            user_id=user.id,
            product_id=deposit_prod.id,
            quantity=1,
            price=deposit_amount,
            status="pending",
            platform="zalo",
            platform_channel_id=str(zalo_user_id)
        )
        db.add(dep_order)
        db.commit()

        qr_url = generate_sepay_qr_url(amount=deposit_amount, order_code=order_code)
        from app.config import SEPAY_BANK, SEPAY_ACCOUNT_NO, SEPAY_ACCOUNT_NAME
        caption = (
            f"💳 NẠP TIỀN VÀO VÍ BOT (TỰ ĐỘNG 24/7)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Số tiền nạp: {deposit_amount:,.0f} VNĐ\n"
            f"🏦 Ngân hàng: {SEPAY_BANK}\n"
            f"🔢 Số tài khoản: {SEPAY_ACCOUNT_NO}\n"
            f"👤 Chủ tài khoản: {SEPAY_ACCOUNT_NAME}\n"
            f"✍️ Nội dung CK: {order_code}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ Hệ thống hỗ trợ 3 mốc nạp: 10k, 50k, 100k.\n"
            f"⚠️ BẮT BUỘC ghi đúng nội dung '{order_code}' để hệ thống tự động cộng tiền sau 3 giây!\n"
            f"💡 Soạn 'SODU' để kiểm tra số dư ví bất cứ lúc nào."
        )
        sent = send_zalo_photo(zalo_user_id, qr_url, caption=caption)
        if not sent:
            send_zalo_message(zalo_user_id, f"{caption}\n\n👉 Link mã QR SePay: {qr_url}")
        return

    # =========================================================================
    # LỆNH 1.2: TRA CỨU & NHẬN MÃ OTP TỨC THÌ (OTP / LAY OTP / CHECK OTP)
    # =========================================================================
    raw_text_clean = re.sub(r"\s+", " ", text.upper().strip())
    if first_word in ["OTP", "LAYOTP", "CHECKOTP", "MAOTP"] or raw_text_clean in [
        "OTP", "LAY OTP", "LẤY OTP", "MÃ OTP", "MA OTP", "CHECK OTP", "XEM OTP", "XEMOTP", "XIN OTP", "XIN MA"
    ]:
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
        phone_to_check = extract_phone_number(text) or (parts[1] if len(parts) >= 2 else None)
        if not phone_to_check:
            send_zalo_message(
                zalo_user_id,
                "📱 𝐇ƯỚ𝐍𝐆 𝐃Ẫ𝐍 𝐊𝐈Ể𝐌 𝐓𝐑𝐀 𝐒Ố Đ𝐈Ệ𝐍 𝐓𝐇𝐎Ạ𝐈 𝐒𝐇𝐎𝐏𝐄𝐄 (𝐅𝐑𝐄𝐄):\n\n"
                "👉 Soạn: CHECKSDT <Số điện thoại>\n"
                "• Hoặc gửi trực tiếp số điện thoại (Ví dụ: 0987654321)\n\n"
                "✨ Kiểm tra tức thì đầu số sạch, đã đăng ký hay có thể BACK số hay không! 🚀"
            )
            return
    elif first_word not in COMMAND_KEYWORDS and not is_quick_buy_number:
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
                "🌐 𝐇ƯỚ𝐍𝐆 𝐃Ẫ𝐍 𝐓𝐇Ê𝐌 𝐏𝐑𝐎𝐗𝐘:\n\n"
                "👉 Cú pháp:\n"
                "ADDPROXY <danh sách proxy>\n\n"
                "💡 Ví dụ thêm 1 proxy:\n"
                "ADDPROXY 103.152.118.25:8080\n\n"
                "💡 Ví dụ có user/pass:\n"
                "ADDPROXY user:pass@104.28.1.1:8080\n\n"
                "✨ Bot sẽ tự động test tốc độ và kiểm tra IP sạch trước khi lưu! 🚀"
            )
            return

        from app.services.user_service import is_admin_user
        from app.services.proxy_validator import parse_and_normalize_proxy, validate_proxy_connection
        from app.services.proxy_service import load_admin_proxies, save_admin_proxies

        is_admin = is_admin_user(zalo_user_id) or (sender_id and is_admin_user(sender_id))

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

        lines = [
            f"📦 𝐊𝐇𝐎 𝐏𝐑𝐎𝐗𝐘 𝐂Ủ𝐀 𝐁Ạ𝐍 ({stats['total']} proxy khả dụng):",
            "━━━━━━━━━━━━━━━━━━━━"
        ]
        for idx, p in enumerate(stats["proxies"][:10], 1):
            lines.append(f"{idx}. {p['url']} ({p['latency_ms']}ms) - Nạp: {p['created_at']}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("👉 Soạn 'REG <số lượng>' để tạo tài khoản tự động (6k/acc)!")
        lines.append("👉 Soạn 'REGSDT <SĐT>' để tạo bằng số của bạn (1k/acc)!")
        lines.append("👉 Soạn 'CLEARPROXY' nếu muốn xóa toàn bộ kho proxy.")
        send_zalo_message(zalo_user_id, "\n".join(lines))
        return

    if first_word == "CLEARPROXY":
        send_chat_action(zalo_user_id, "typing")
        from .user_proxy_service import clear_user_proxies
        deleted = clear_user_proxies(user_task_key)
        send_zalo_message(zalo_user_id, f"🗑️ Đã xóa sạch toàn bộ {deleted} proxy trong kho của bạn!")
        return

    # =========================================================================
    # LỆNH 1.4.3: ĐĂNG KÝ SHOPEE FULL STACK (REG <SL> - 6,000đ/ACC)
    # VÀ ĐĂNG KÝ BẰNG SĐT CỦA BẠN (REGSDT <SĐT> - 1,000đ/ACC)
    # =========================================================================
    if first_word in ["REG", "REGSDT"]:
        send_chat_action(zalo_user_id, "typing")

        # Kiểm tra quy tắc 1 tiến trình/user
        busy, active_t = is_user_busy(user_task_key)
        if busy:
            send_zalo_message(
                zalo_user_id,
                f"⚠️ 𝐁Ạ𝐍 Đ𝐀𝐍𝐆 𝐂Ó 𝟏 𝐓𝐈Ế𝐍 𝐓𝐑Ì𝐍𝐇 Đ𝐀𝐍𝐆 𝐂𝐇Ạ𝐘!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 Nhiệm vụ: {active_t.get('task_name')}\n"
                f"📍 Tiến độ: {active_t.get('current_step')}\n\n"
                f"👉 Hệ thống chỉ cho phép 1 tiến trình / người dùng tại một thời điểm.\n"
                f"👉 Để dừng tiến trình hiện tại, vui lòng soạn: STOP"
            )
            return

        arg_text = " ".join(parts[1:]).strip()
        detected_phone = extract_phone_number(arg_text) if arg_text else None

        if first_word == "REG":
            # Nếu khách gõ 'REG 0912345678' nhầm sang số điện thoại
            if detected_phone:
                send_zalo_message(
                    zalo_user_id,
                    f"💡 𝐁Ạ𝐍 Đ𝐀𝐍𝐆 𝐍𝐇Ậ𝐏 𝐒Ố Đ𝐈Ệ𝐍 𝐓𝐇𝐎Ạ𝐈 𝐑𝐈Ê𝐍𝐆 ({detected_phone})!\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👉 Vui lòng dùng lệnh: REGSDT {detected_phone}\n"
                    f"💰 Giá ưu đãi: Chỉ 1,000đ / tài khoản!\n"
                    f"📌 Quy trình: Bot mở Shopee qua Proxy & giải Captcha ➔ Shopee gửi mã OTP về máy bạn ➔ Bạn nhập 'OTP <mã>' để hoàn tất.\n\n"
                    f"💡 Còn lệnh 'REG <số_lượng>' là cấp SIM tự động từ A-Z (6,000đ/acc). Ví dụ: REG 1"
                )
                return

            # Luồng cấp SIM tự động Full Stack (6,000đ / acc)
            is_own_phone = False
            custom_phone = None
            if arg_text.isdigit():
                req_quantity = max(1, min(int(arg_text), 10))
            else:
                req_quantity = 1
            fee_per_acc = 6000
            total_fee = fee_per_acc * req_quantity
            service_label = f"Cấp SIM tự động ({req_quantity} tài khoản)"

        elif first_word == "REGSDT":
            # Luồng dùng SĐT của khách (1,000đ / acc)
            if not detected_phone:
                send_zalo_message(
                    zalo_user_id,
                    "⚠️ 𝐕𝐔𝐈 𝐋Ò𝐍𝐆 𝐍𝐇Ậ𝐏 𝐒Ố Đ𝐈Ệ𝐍 𝐓𝐇𝐎Ạ𝐈 𝐂Ủ𝐀 𝐁Ạ𝐍!\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "👉 Cú pháp: REGSDT <số_điện_thoại>\n"
                    "💡 Ví dụ: REGSDT 0987654321\n\n"
                    "💰 Chi phí: 1,000đ / tài khoản\n"
                    "📌 Quy trình thực hiện:\n"
                    "1️⃣ Bot mở Shopee qua Proxy riêng của bạn & tự giải Captcha bằng AI.\n"
                    "2️⃣ Shopee gửi tin nhắn chứa mã OTP về máy bạn.\n"
                    "3️⃣ Bạn chỉ cần soạn: OTP <mã> (Ví dụ: OTP 123456 hoặc gõ 123456) trong 90s để bot hoàn tất tạo nick!"
                )
                return

            is_own_phone = True
            custom_phone = detected_phone
            req_quantity = 1
            fee_per_acc = 1000
            total_fee = 1000
            service_label = f"SĐT cá nhân ({custom_phone})"

        # BƯỚC 1: Kiểm tra số dư ví của khách
        user_bal = float(user.balance or 0.0) if user else 0.0
        if user_bal < total_fee:
            send_zalo_message(
                zalo_user_id,
                f"⚠️ 𝐒Ố 𝐃Ư 𝐓À𝐈 𝐊𝐇𝐎Ả𝐍 𝐊𝐇Ô𝐍𝐆 ĐỦ!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🛒 Dịch vụ: {service_label}\n"
                f"💰 Cần thanh toán: {total_fee:,.0f} VNĐ\n"
                f"💵 Số dư ví hiện tại: {user_bal:,.0f} VNĐ\n"
                f"📌 Còn thiếu: {float(total_fee - user_bal):,.0f} VNĐ\n\n"
                f"💡 𝐁Ả𝐍𝐆 𝐆𝐈Á 𝐃Ị𝐂𝐇 𝐕Ụ REG SHOPEE:\n"
                f"• Thuê SIM tự động: 6,000đ / tài khoản (Soạn: REG <số_lượng>)\n"
                f"• SĐT của chính bạn: 1,000đ / tài khoản (Soạn: REG <SĐT_của_bạn>)\n\n"
                f"👉 Vui lòng soạn 'NAP' để lấy mã QR nạp tiền vào ví tức thì nhé! ✨"
            )
            return

        from .user_proxy_service import get_user_verified_proxies, deactivate_dead_proxy
        from app.services.proxy_service import load_admin_proxies, save_admin_proxies
        from app.services.proxy_validator import validate_proxy_connection
        from app.config import DEFAULT_PROXY

        # BƯỚC 2: Cấp Proxy theo thứ tự ưu tiên:
        # 1. Proxy riêng của User (nếu user có nạp proxy riêng)
        # 2. Proxy Hệ Thống do Admin cấu hình (sử dụng đến khi die)
        # 3. IP mặc định của hệ thống (DEFAULT_PROXY hoặc Direct)
        available_proxies = []

        # 1. Thử tìm proxy riêng của user
        user_proxies, dead_proxies = get_user_verified_proxies(zalo_user_id, count=req_quantity)
        if dead_proxies:
            send_zalo_message(
                zalo_user_id,
                f"🧹 ĐÃ DỌN DẸP KHO PROXY:\n"
                f"⚠️ Phát hiện {len(dead_proxies)} proxy riêng đã hết hạn hoặc mất kết nối và đã được loại bỏ."
            )
        if user_proxies:
            available_proxies = user_proxies

        # 2. Nếu user chưa có proxy riêng -> Lấy Proxy Hệ Thống của Admin (dùng đến khi die)
        if not available_proxies:
            admin_proxies = load_admin_proxies()
            valid_admin_proxies = []
            for a_p in admin_proxies:
                is_alive, msg, _ = validate_proxy_connection(a_p, timeout=5)
                if is_alive:
                    valid_admin_proxies.append(a_p)
                else:
                    logger.warning(f"Admin proxy {a_p} đã die ({msg}), tự động loại bỏ.")

            # Cập nhật danh sách admin proxy nếu có con bị die
            if len(valid_admin_proxies) != len(admin_proxies):
                save_admin_proxies(valid_admin_proxies)

            if valid_admin_proxies:
                sys_p = valid_admin_proxies[0]
                available_proxies = [sys_p] * req_quantity

        # 3. Nếu không có cả 2 -> Dùng IP mặc định hệ thống như trước giờ (DEFAULT_PROXY hoặc direct)
        if not available_proxies:
            def_p = DEFAULT_PROXY or "direct"
            available_proxies = [def_p] * req_quantity

        total_to_reg = req_quantity
        actual_total_cost = fee_per_acc * total_to_reg

        # BƯỚC 3: Trừ tiền ví của khách trước khi khởi chạy
        try:
            user.balance = float(user.balance or 0.0) - actual_total_cost
            db.commit()
            db.refresh(user)
        except Exception as e:
            db.rollback()
            send_zalo_message(zalo_user_id, f"❌ Lỗi hệ thống khi thanh toán ví: {str(e)}")
            return

        # Khởi tạo khóa tiến trình độc quyền cho User
        task_title = f"Reg {service_label}"
        task_started = start_user_task(user_task_key, task_title)
        if not task_started:
            # Hoàn tiền nếu không start được task
            user.balance = float(user.balance or 0.0) + actual_total_cost
            db.commit()
            send_zalo_message(zalo_user_id, "⚠️ Không thể bắt đầu tiến trình mới. Vui lòng thử lại hoặc soạn STOP!")
            return

        send_zalo_message(
            zalo_user_id,
            f"🚀 [1/4] Đang khởi động đăng ký {total_to_reg} tài khoản Shopee...\n"
            f"💰 Đã trừ ví: {actual_total_cost:,.0f}đ (Số dư: {float(user.balance):,.0f}đ)\n"
            f"ℹ️ Bot sẽ tự động thông báo khi tạo xong acc và hoàn tiền 100% nếu không thành công!\n"
            f"👉 Soạn 'STOP' nếu muốn hủy tiến trình."
        )

        try:
            from .log_notifier_service import send_log_message
            c_name = display_name or (user.display_name if user else "Khách hàng")
            send_log_message(
                f"🚀 [BẮT ĐẦU ĐĂNG KÝ ACC]\n"
                f"• Khách: {c_name} (ID: ...{str(user_task_key)[-6:]})\n"
                f"• Dịch vụ: {service_label}\n"
                f"• Thanh toán: {actual_total_cost:,.0f}đ"
            )
        except Exception:
            pass

        import threading
        import asyncio

        def run_batch_reg_worker():
            from .shopee_reg_service import ShopeeRegService
            reg_srv = ShopeeRegService()

            def notify_sync(status_txt: str):
                send_zalo_message(zalo_user_id, status_txt)

            def refund_single_acc(amount: float) -> float:
                """Hoàn lại tiền vào ví của khách hàng trong CSDL."""
                db_ref = SessionLocal()
                new_b = 0.0
                try:
                    u_ref = db_ref.query(User).filter(User.user_id == str(user_task_key)).first()
                    if u_ref:
                        u_ref.balance = float(u_ref.balance or 0.0) + amount
                        db_ref.commit()
                        new_b = float(u_ref.balance)
                except Exception as ex:
                    db_ref.rollback()
                finally:
                    db_ref.close()
                return new_b

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            accumulated_success = []
            refunded_count = 0

            try:
                for idx, proxy_url in enumerate(available_proxies[:total_to_reg], 1):
                    # Kiểm tra xem user có yêu cầu STOP giữa chừng không
                    if is_stop_requested(user_task_key):
                        unprocessed = total_to_reg - (idx - 1)
                        if unprocessed > 0:
                            refund_amount = unprocessed * fee_per_acc
                            new_bal = refund_single_acc(refund_amount)
                            notify_sync(
                                f"🛑 [LỆNH STOP] Đã dừng chuỗi đăng ký theo yêu cầu!\n"
                                f"💰 Đã tự động hoàn lại {refund_amount:,.0f}đ ({unprocessed} tài khoản chưa chạy) vào ví.\n"
                                f"💵 Số dư ví hiện tại: {new_bal:,.0f}đ"
                            )
                        break

                    acc_tag = f"[Acc {idx}/{total_to_reg}]" if total_to_reg > 1 else ""
                    update_user_task_progress(user_task_key, f"{acc_tag} Đang xử lý...".strip())

                    try:
                        from app.config import VIOTP_SERVICE_ID
                        res = loop.run_until_complete(
                            reg_srv.register_account(
                                user_proxy=proxy_url,
                                zalo_user_id=user_task_key,
                                service_id=VIOTP_SERVICE_ID,
                                provided_phone=custom_phone,
                                acc_prefix=acc_tag,
                                progress_callback=notify_sync
                            )
                        )
                    except Exception as loop_ex:
                        res = {"ok": False, "step": "runtime_ex", "error": str(loop_ex), "should_refund": True}

                    # Kiểm tra nếu bị dừng trong khi chạy
                    if res.get("step") == "stopped" or is_stop_requested(user_task_key):
                        is_rented, r_phone = is_task_phone_rented(user_task_key)
                        if is_rented and not custom_phone:
                            notify_sync(f"🛑 {acc_tag} Đã dừng tiến trình. Tài khoản này đã thuê số thành công ({r_phone}) nên không hoàn phí.")
                        else:
                            new_bal = refund_single_acc(fee_per_acc)
                            notify_sync(f"🛑 {acc_tag} Đã dừng tiến trình. Đã hoàn {fee_per_acc:,.0f}đ vào ví (Số dư: {new_bal:,.0f}đ).")
                        break

                    if res.get("ok"):
                        accumulated_success.append(res)
                        p_phone = res.get("phone")
                        p_pwd = res.get("password")
                        p_spc_st = res.get("spc_st") or ""

                        success_msg = (
                            f"🎉 {acc_tag} ĐĂNG KÝ THÀNH CÔNG!\n"
                            f"📱 SĐT: {p_phone}\n"
                            f"🔑 Pass: {p_pwd}\n"
                            f"🍪 SPC_ST: {p_spc_st}\n"
                            f"📋 Copy: {p_phone}|{p_pwd}|{p_spc_st}"
                        )
                        notify_sync(success_msg.strip())
                    else:
                        # Thất bại -> TỰ ĐỘNG HOÀN TIỀN
                        refunded_count += 1
                        new_bal = refund_single_acc(fee_per_acc)
                        err_msg = res.get("error", "Đăng ký không thành công.")
                        if res.get("step") == "proxy":
                            deactivate_dead_proxy(zalo_user_id, proxy_url)

                        notify_sync(
                            f"⚠️ {acc_tag} Thất bại: {err_msg}\n"
                            f"💰 Đã hoàn {fee_per_acc:,.0f}đ vào ví (Số dư: {new_bal:,.0f}đ)."
                        )

                    # Kiểm tra lại lệnh STOP trước khi chuyển sang acc tiếp theo
                    if is_stop_requested(user_task_key):
                        unprocessed = total_to_reg - idx
                        if unprocessed > 0:
                            refund_amount = unprocessed * fee_per_acc
                            new_bal = refund_single_acc(refund_amount)
                            notify_sync(
                                f"🛑 [LỆNH STOP] Đã dừng chuỗi đăng ký!\n"
                                f"💰 Đã hoàn lại {refund_amount:,.0f}đ ({unprocessed} tài khoản) vào ví (Số dư: {new_bal:,.0f}đ)."
                            )
                        break

                    # Nếu còn tài khoản tiếp theo, nghỉ nhẹ 2 giây
                    if idx < total_to_reg:
                        update_user_task_progress(user_task_key, f"{acc_tag} Chuyển tiếp...")
                        time.sleep(2)

                # Tổng kết nếu reg từ 2 acc trở lên
                c_name = display_name or (user.display_name if user else "Khách hàng")
                if total_to_reg > 1 and accumulated_success:
                    export_lines = [f"{a['phone']}|{a['password']}|{a['spc_st']}" for a in accumulated_success]
                    summary_msg = (
                        f"🏆 Hoàn tất {len(accumulated_success)}/{total_to_reg} tài khoản:\n"
                        f"{chr(10).join(export_lines)}\n"
                        "👉 Soạn REGLOG để xem lại lịch sử."
                    )
                    notify_sync(summary_msg)

                    try:
                        from .log_notifier_service import send_log_message
                        send_log_message(
                            f"🏆 [HOÀN TẤT ĐĂNG KÝ ACC]\n"
                            f"• Khách: {c_name} (ID: ...{str(user_task_key)[-6:]})\n"
                            f"• Tạo thành công: {len(accumulated_success)}/{total_to_reg} tài khoản Shopee\n"
                            f"• Hoàn tiền: {refunded_count} tài khoản"
                        )
                    except Exception:
                        pass

                elif not is_stop_requested(user_task_key):
                    notify_sync("⚠️ Hoàn tất tiến trình nhưng không tạo được tài khoản nào thành công. Toàn bộ tiền đã được hoàn trả đầy đủ vào ví của bạn!")
                    try:
                        from .log_notifier_service import send_log_message
                        send_log_message(
                            f"⚠️ [REG ACC KHÔNG THÀNH CÔNG]\n"
                            f"• Khách: {c_name} (ID: ...{str(user_task_key)[-6:]})\n"
                            f"• Yêu cầu: {total_to_reg} tài khoản\n"
                            f"• Kết quả: 0/{total_to_reg} thành công (Đã hoàn tiền 100%)"
                        )
                    except Exception:
                        pass

            except Exception as ex:
                notify_sync(f"❌ Lỗi hệ thống trong quá trình đăng ký: {str(ex)}")
            finally:
                loop.close()
                finish_user_task(user_task_key)

        threading.Thread(target=run_batch_reg_worker, daemon=True).start()
        return

    # =========================================================================
    # LỆNH 1.5: XEM LỊCH SỬ ĐĂNG KÝ TÀI KHOẢN (REGLOG)
    # =========================================================================
    if first_word == "REGLOG":
        send_chat_action(zalo_user_id, "typing")
        logs = db.query(ShopeeRegLog).order_by(ShopeeRegLog.id.desc()).limit(5).all()
        if not logs:
            send_zalo_message(zalo_user_id, "ℹ️ Hệ thống chưa có lịch sử đăng ký tài khoản nào.")
            return

        lines = [
            "📜 𝐋Ị𝐂𝐇 𝐒Ử ĐĂNG KÝ TÀI KHOẢN GẦN ĐÂY:",
            "━━━━━━━━━━━━━━━━━━━━"
        ]
        for item in logs:
            stt_icon = "🟢 Thành công" if item.status == "success" else ("🔴 Thất bại" if item.status == "failed" else "⏳ Đang chạy")
            rec_tag = "🔄 Reclaim" if item.is_reclaimed else "✨ Mới tinh"
            lines.append(
                f"#{item.id} | SĐT: {item.phone_number or 'Chưa cấp'} ({rec_tag})\n"
                f"• Trạng thái: {stt_icon}\n"
                f"• User: {item.username or 'N/A'}\n"
                f"• IP Proxy: {item.external_ip or 'N/A'}\n"
                f"• Thời gian: {item.created_at.strftime('%H:%M %d/%m/%Y') if item.created_at else ''}"
            )
            if item.status == "failed" and item.error_message:
                lines.append(f"• Lý do: {item.error_message[:60]}...")
            lines.append("────────────────────")

        send_zalo_message(zalo_user_id, "\n".join(lines))
        return

    # =========================================================================
    # LỆNH 2: ĐẶT MUA SẢN PHẨM (BUY <Mã> [SL] hoặc gõ số [Mã])
    # =========================================================================
    if first_word == "BUY" or is_quick_buy_number:
        send_chat_action(zalo_user_id, "typing")

        if is_quick_buy_number:
            product_id = int(parts[0])
            quantity = 1
        else:
            if len(parts) < 2:
                send_zalo_message(
                    zalo_user_id,
                    "⚠️ 𝐇ƯỚ𝐍𝐆 𝐃Ẫ𝐍 𝐌𝐔𝐀 𝐇À𝐍𝐆 𝐒𝐈Ê𝐔 𝐓Ố𝐂:\n\n"
                    "👉 Mua 1 cái: Gõ số [Mã] (Ví dụ: 1 hoặc 6)\n"
                    "👉 Mua nhiều cái: BUY <Mã> <Số lượng> (Ví dụ: BUY 1 2)\n\n"
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
        product = db.query(Product).filter(Product.id == product_id, Product.status == "active").first()
        if not product:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy sản phẩm mã [{product_id}]. Nhắn 'MENU' để xem danh sách nhé!")
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
        send_chat_action(zalo_user_id, "typing")
        if not user:
            send_zalo_message(zalo_user_id, "⚠️ Không tìm thấy thông tin tài khoản của bạn.")
            return

        purchased_stocks = db.query(ProductStock).join(Order).filter(
            Order.user_id == user.id,
            Order.status == "completed",
            ProductStock.status == "sold"
        ).order_by(ProductStock.id.desc()).limit(10).all()

        # Lấy thêm các đơn hàng số đã hoàn thành (Highlands, SIM OTP, Drive...)
        digital_orders = db.query(Order).filter(
            Order.user_id == user.id,
            Order.status == "completed",
            Order.account_delivered.isnot(None)
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

        lines = [
            "📦 𝐋Ị𝐂𝐇 𝐒Ử ĐƠ𝐍 𝐇À𝐍𝐆 & 𝐓À𝐈 𝐊𝐇𝐎Ả𝐍 ĐÃ 𝐌𝐔𝐀\n"
        ]

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
            "⚡ 𝐓𝐇𝐀𝐎 𝐓Á𝐂 𝐍𝐇𝐀𝐍𝐇:",
            "• Gán mail Shopee: ADDMAIL <Mã TK>",
            "• Duyệt login Shopee: XACMINH <Mã TK>",
            "• Lấy mã OTP SIM: Nhắn OTP",
            "• Mua thêm dịch vụ: Nhắn MENU"
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
        from app.services.proxy_service import load_admin_proxies, save_admin_proxies
        from app.services.proxy_validator import validate_proxy_connection
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
                "🚚 𝐓𝐑𝐀 𝐂Ứ𝐔 𝐕Ậ𝐍 ĐƠ𝐍 𝐒𝐇𝐎𝐏𝐄𝐄 𝐄𝐗𝐏𝐑𝐄𝐒𝐒 (𝐅𝐑𝐄𝐄):\n\n"
                "👉 Soạn: TRACK <Mã vận đơn>\n"
                "• Ví dụ SPX: TRACK SPXVN061857044824\n"
                "• Ví dụ đơn shop: TRACK DH249985\n\n"
                "✨ Tự động cập nhật lộ trình bưu kiện tức thì 24/7! 🚀"
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
            "📖 𝐁Ả𝐍𝐆 𝐋Ệ𝐍𝐇 & 𝐇ƯỚ𝐍𝐆 𝐃Ẫ𝐍 𝐒Ử 𝐃Ụ𝐍𝐆 𝐁𝐎𝐓 𝟐𝟒/𝟕 ✨",
            "━━━━━━━━━━━━━━━━━━━━",
            "📌 Mỗi chức năng đại diện bằng ĐÚNG 1 COMMAND duy nhất!\n",
            "🛒 𝟏. 𝐌𝐔𝐀 𝐇À𝐍𝐆 & 𝐕Í 𝐒Ố 𝐃Ư:",
            "• MENU : Xem bảng giá & số lượng sẵn kho 24/7",
            "• BUY <Mã> <SL> : Mua tài khoản số lượng (VD: BUY 1 2)",
            "  (Hoặc nhắn nhanh số mã món hàng, ví dụ: 1)",
            "• SODU : Xem số dư ví khả dụng của bạn",
            "• NAP <Số tiền> : Lấy mã QR nạp tiền vào ví tự động (VD: NAP 50000)\n",
            "🚀 𝟐. ĐĂNG KÝ SHOPEE TỰ ĐỘNG (CẦN PROXY RIÊNG):",
            "• REG <Số lượng> : Cấp SIM tự động Full Stack từ A-Z (6,000đ/nick)",
            "  (Hệ thống tự cấp SIM, vượt xác thực an toàn, nhận OTP & xuất nick)",
            "• REGSDT <SĐT> : Đăng ký bằng SĐT của bạn (1,000đ/nick)",
            "  (Bot giải captcha ➔ Shopee gửi OTP về máy bạn ➔ Bạn nhập 'OTP <mã>')",
            "• OTP <Mã> : Nhập mã xác nhận khi reg nick (VD: OTP 123456 hoặc gõ 123456)",
            "• REGLOG : Xem lịch sử các nick Shopee đã đăng ký\n",
            "🔐 𝟑. QUẢN LÝ TÀI KHOẢN & EMAIL:",
            "• ADDMAIL <Mã TK> : Gán hòm thư bảo mật tự động (Bắt buộc qua Proxy riêng)",
            "• XACMINH <Mã TK> : Tự động duyệt login Shopee qua Email trong 3s",
            "• DONHANG : Xem lại toàn bộ nick & mật khẩu đã mua\n",
            "🌐 𝟒. QUẢN LÝ PROXY RIÊNG (1 USER = 1 LUỒNG = 1 PROXY):",
            "• ADDPROXY <Proxy> : Nạp proxy của bạn (VD: ADDPROXY 116.96.177.251:16863)",
            "• LISTPROXY : Xem danh sách proxy khả dụng trong kho",
            "• CLEARPROXY : Xóa toàn bộ kho proxy\n",
            "🛠️ 𝟓. TIỆN ÍCH & ĐIỀU KHIỂN:",
            "• STOP : Dừng ngay tiến trình đang chạy & tự động hoàn tiền",
            "• CHECKSDT <SĐT> : Kiểm tra số điện thoại sạch / đã đăng ký Shopee (Free)",
            "• TRACK <Mã SPX> : Tra cứu bưu kiện Shopee Express (Free)",
            "• HELP : Xem lại bảng hướng dẫn này",
            "━━━━━━━━━━━━━━━━━━━━",
            "💡 Soạn 'MENU' để bắt đầu trải nghiệm ngay! 🚀"
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