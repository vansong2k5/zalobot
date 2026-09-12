"""
Dịch vụ xử lý tin nhắn và lệnh từ Khách hàng tương tác qua Zalo Bot.
Giao diện Gen Z hiện đại, trực quan, hỗ trợ tương tác mượt mà trong Nhóm (Group) & Chat 1-1.
Tập trung chuyên sâu vào BÁN HÀNG TỰ ĐỘNG & GIAO DỊCH QUA SEPAY:
1. MENU / Danh sách sản phẩm kèm giá & tồn kho trực tiếp (🟢/🔴).
2. Mua hàng siêu tốc: Nhắn số [Mã SP] hoặc [BUY <Mã> <SL>].
3. Tạo đơn hàng tự động & gửi QR SePay chuyển khoản tức thì.
4. Quản lý email, tự lấy OTP và xác minh đăng nhập Shopee hoàn toàn tự động.
"""

from decimal import Decimal
from sqlalchemy.orm import Session

from app.models import Product, ProductStock, Order
from .zalo_service import send_zalo_message, send_chat_action, send_zalo_photo, send_zalo_sticker
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


COMMAND_KEYWORDS = {
    "MENU", "BUY", "HELP", "DONHANG", "MYACC", "TAIKHOAN", "LICHSU",
    "SODU", "VI", "WALLET", "TIEN", "CHECKSD", "XEMSD", "BALANCE",
    "ADDMAIL", "THEMMAIL", "GANMAIL",
    "XACMINH", "DUYET", "CONFIRM", "DUYETMAIL",
    "ADMIN", "TK", "THONGKE", "STATS", "THEMSP", "THEMKHO", "KHO",
    "NAPTIEN", "HOANTIEN", "CONGTIEN", "TRUTIEN", "CHECKVI", "VIUSER",
    "MYID", "ID", "WHOAMI", "SETADMIN", "ACTIVATEADMIN",
    "SETGROUP", "THEMNHOM", "LUUNHOM",
    "CO", "CHECK", "TRA", "TRACK",
    "OTP", "DOCMAIL", "MAIL"
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
        if cleaned_tok in COMMAND_KEYWORDS or cleaned_tok.isdigit():
            command_idx = i
            break

    if command_idx != -1:
        return " ".join(tokens[command_idx:]).strip()

    return "MENU"


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
        "───────────────────────",
        "⚡ 𝐋Ố𝐈 𝐓Ắ𝐂 𝐌𝐔𝐀 𝐒𝐈Ê𝐔 𝐓Ố𝐂:",
        "👉 Mua 1 cái: Nhắn số [Mã] (Ví dụ: 1 hoặc 7)",
        "👉 Mua nhiều: BUY <Mã> <SL> (Ví dụ: BUY 1 2)",
        "👉 Lấy nick & OTP: Nhắn DONHANG",
        "👉 Xem số dư ví: Nhắn SODU",
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

    parts = text.split()
    first_word = parts[0].upper()

    # Lệnh lưu/kích hoạt nhóm trực tiếp khi gõ trong Group
    if is_group_chat and first_word in ["SETGROUP", "/SETGROUP", "THEMNHOM", "LUUNHOM", "CAPNHATNHOM"]:
        register_or_update_group(db, zalo_user_id)
        send_zalo_message(
            zalo_user_id,
            "✅ Nhóm Zalo này đã được kết nối tự động thành công! 🎉"
        )
        return

    # Lệnh tra cứu Zalo ID cá nhân (MYID / ID / WHOAMI)
    if first_word in ["MYID", "ID", "WHOAMI"]:
        send_chat_action(zalo_user_id, "typing")
        is_adm = is_admin_user(admin_check_id)
        c_name = display_name or (user.display_name if user else "Khách")
        id_info = (
            f"🆔 THÔNG TIN ĐỊNH DANH ZALO CỦA BẠN:\n"
            f"───────────────────────\n"
            f"👤 Tên hiển thị: {c_name}\n"
            f"🔑 Zalo User ID: {admin_check_id}\n"
            f"👑 Quyền Quản trị: {'✅ ĐÃ KÍCH HOẠT (ADMIN)' if is_adm else '❌ Chưa có quyền'}\n"
            f"───────────────────────\n"
            f"💡 Kích hoạt quyền Admin nhanh: Nhắn 'SETADMIN <khóa_bí_mật>'!"
        )
        send_zalo_message(zalo_user_id, id_info)
        return

    # Lệnh kích hoạt quyền Admin tức thì qua mã bí mật (SETADMIN <admin_key>)
    if first_word in ["SETADMIN", "ACTIVATEADMIN", "ADMINKEY"]:
        from app.config import ADMIN_API_KEY, ADMIN_ZALO_IDS
        if len(parts) >= 2 and parts[1].strip() == ADMIN_API_KEY:
            clean_check_id = str(admin_check_id).strip().lower()
            if clean_check_id not in [x.lower() for x in ADMIN_ZALO_IDS]:
                ADMIN_ZALO_IDS.append(clean_check_id)
            send_zalo_message(
                zalo_user_id,
                f"👑 KÍCH HOẠT QUYỀN ADMIN THÀNH CÔNG! 🎉\n"
                f"───────────────────────\n"
                f"👤 Admin: {display_name}\n"
                f"🔑 Zalo ID: {admin_check_id}\n"
                f"✅ Bạn đã được cấp toàn quyền quản trị bot!\n"
                f"👉 Soạn 'ADMIN' để mở bảng điều khiển quản lý ngay nhé! ✨"
            )
        else:
            send_zalo_message(zalo_user_id, "❌ Khóa bí mật Admin không chính xác!")
        return

    # Khách gõ nhanh 1 con số (ví dụ: '1') để mua sản phẩm mã 1
    is_quick_buy_number = len(parts) == 1 and parts[0].isdigit()

    # =========================================================================
    # LỆNH 1: XEM MENU SẢN PHẨM (MENU)
    # =========================================================================
    if first_word in ["MENU", "GIA", "BANGGIA", "DS"]:
        send_chat_action(zalo_user_id, "typing")
        target_name = (display_name or (user.display_name if user else "")) if not is_group_chat else ""
        menu_text = render_product_menu_text(db, target_name)
        send_zalo_message(zalo_user_id, menu_text)
        return

    # =========================================================================
    # LỆNH 1.1: XEM SỐ DƯ VÍ (SODU / VI / WALLET / TIEN / CHECKSD)
    # =========================================================================
    if first_word in ["SODU", "VI", "WALLET", "TIEN", "CHECKSD", "XEMSD", "BAL", "BALANCE"]:
        send_chat_action(zalo_user_id, "typing")
        user_bal = float(user.balance or 0.0) if user else 0.0
        c_name = display_name or (user.display_name if user else "Khách hàng")
        target_chat_id = effective_user_id if is_group_chat and effective_user_id else zalo_user_id

        wallet_msg = (
            f"💼 THÔNG TIN VÍ & SỐ DƯ CỦA BẠN 💳\n"
            f"───────────────────────\n"
            f"👤 Khách hàng: {c_name}\n"
            f"💰 Số dư khả dụng: {int(user_bal):,} VNĐ\n"
            f"───────────────────────\n"
            f"⚡ TIỆN ÍCH DÀNH CHO BẠN:\n"
            f"• Số dư ví dùng để mua trực tiếp mọi tài khoản & dịch vụ trên bot.\n"
            f"• Khi có đủ số dư, bạn chỉ cần soạn mua (Ví dụ: '6' hoặc 'BUY 6 1'), hệ thống sẽ TỰ ĐỘNG TRỪ VÍ và bàn giao dịch vụ tức thì trong 1 giây mà không cần chuyển khoản!\n\n"
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
                    "⚠️ 𝐇ƯỚ𝐍𝐆 𝐃Ẫ𝐍 𝐌𝐔𝐀 𝐇À𝐍𝐆 𝐒𝐈Ê𝐔 𝐓Ố𝐂:\n"
                    "───────────────────────\n"
                    "👉 Mua 1 cái: Gõ số [Mã] (Ví dụ: 1 hoặc 7)\n"
                    "👉 Mua nhiều cái: BUY <Mã> <Số lượng> (Ví dụ: BUY 1 2)\n"
                    "───────────────────────\n"
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
                f"💳 TỰ ĐỘNG TRỪ SỐ DƯ VÍ THÀNH CÔNG! [Đơn #{order_code}]\n"
                f"───────────────────────\n"
                f"💰 Số tiền: -{int(total_price):,} VNĐ\n"
                f"💼 Số dư ví còn lại: {int(user.balance):,} VNĐ\n"
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
        if product.id == 7 or "highlands" in prod_lower:
            extra_note = (
                "\n\n📌 ĐIỀU KIỆN BẮT BUỘC ĐỂ NHẬN VOUCHER 29K:\n"
                "• BẮT BUỘC: XÓA APP Highlands cũ ➔ Tải lại từ App Store! (Không xóa app tải lại sẽ KHÔNG CÓ voucher).\n"
                "• Bỏ qua bước nhập mã giới thiệu ➔ Vào app đợi 2 phút là voucher tự xuất hiện!\n"
                "⚠️ BẢO HÀNH: Tối ưu cho iOS (iPhone/iPad). Máy ANDROID vui lòng nhắn Admin trước! Tự ý mua trên Android lỗi không có mã Admin KHÔNG BẢO HÀNH!"
            )

        caption = (
            f"⚡ ĐƠN HÀNG MỚI: #{order_code}{buyer_label} ⚡\n"
            f"───────────────────────\n"
            f"📦 Dịch vụ: {product.product_name}\n"
            f"🔢 Số lượng: {quantity} tài khoản\n"
            f"💰 Cần thanh toán: {int(total_price):,} VNĐ\n"
            f"───────────────────────\n"
            f"🏦 Ngân hàng: {SEPAY_BANK}\n"
            f"💳 Số tài khoản: {SEPAY_ACCOUNT_NO}\n"
            f"👤 Chủ TK: {SEPAY_ACCOUNT_NAME}\n"
            f"✍️ Nội dung CK: {order_code} (Bắt buộc)\n"
            f"───────────────────────\n"
            f"🚀 Quét QR chuyển khoản nhận mã tự động trong 3 giây!{extra_note}"
        )

        photo_sent = send_zalo_photo(zalo_user_id, photo_url=qr_url, caption=caption)
        if not photo_sent:
            send_zalo_message(zalo_user_id, f"{caption}\n\n🔗 Quét mã QR tại: {qr_url}")
        return

    # =========================================================================
    # LỆNH 3: XEM DANH SÁCH TÀI KHOẢN ĐÃ MUA (DONHANG / LICHSU / MYACC / TAIKHOAN)
    # =========================================================================
    if first_word in ["DONHANG", "LICHSU", "MYACC", "TAIKHOAN", "DSACC"]:
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
            "───────────────────────"
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
                lines.append(f"👉 Duyệt login Shopee: XACMINH {s.id}")
            else:
                lines.append("• 📧 Mail: [Chưa gắn]")
                lines.append(f"👉 Thêm mail free: ADDMAIL {s.id}")

            lines.append("───────────────────────")

        # 2. Hiển thị đơn hàng số (Highlands, Thuê SIM OTP, Drive...)
        for d in digital_orders:
            # Nếu đơn này đã được hiển thị qua stock thì bỏ qua để tránh trùng
            if any(s.order_id == d.id for s in purchased_stocks):
                continue
            d_title = d.product.product_name if d.product else "Dịch vụ"
            d_time = d.completed_at.strftime("%H:%M %d/%m") if d.completed_at else ""
            lines.append(f"⚡ [ĐƠN #{d.order_code}] ➔ {d_title} ({d_time})")
            lines.append("• Trạng thái: ✅ Hoàn tất")
            lines.append(f"• Thông tin bàn giao:\n{d.account_delivered}")
            lines.append("───────────────────────")

        lines.extend([
            "⚡ 𝐓𝐇𝐀𝐎 𝐓Á𝐂 𝐍𝐇𝐀𝐍𝐇:",
            "• Gán mail Shopee: ADDMAIL <Mã TK>",
            "• Duyệt login Shopee: XACMINH <Mã TK>",
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
    # =========================================================================
    if first_word in ["ADDMAIL", "THEMMAIL", "GANMAIL", "ADDEMAIL"]:
        send_chat_action(zalo_user_id, "typing")
        if not user:
            send_zalo_message(zalo_user_id, "⚠️ Không tìm thấy thông tin tài khoản của bạn.")
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

        send_zalo_message(
            zalo_user_id,
            f"⏳ Đang xử lý gán email cho tài khoản [#{stock.id}]...\nChờ xíu nhé!"
        )

        result = add_email_to_shopee_account(db, stock, custom_email=custom_email)

        if result.get("ok"):
            mail_assigned = result.get("email") or stock.assigned_email
            if result.get("already_linked"):
                resp_lines = [
                    f"ℹ️ TÀI KHOẢN [#{stock.id}] ĐÃ CÓ SẴN EMAIL LIÊN KẾT 📧\n",
                    f"• Nick: {stock.account}",
                    f"• Email: {mail_assigned}",
                    "───────────────────────",
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
                    f"• Email bảo mật: {mail_assigned}",
                    "───────────────────────",
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
        return

    # =========================================================================
    # LỆNH 5: HƯỚNG DẪN CHUYỂN ĐỔI KHI KHÁCH GÕ LỆNH OTP
    # =========================================================================
    if first_word in ["OTP", "GETOTP", "DOCMAIL", "MAIL", "CHECKMAIL"]:
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
    if first_word in ["XACMINH", "DUYET", "CONFIRM", "DUYETMAIL"]:
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
                f"• SĐT nick: {stock.sdt or 'Không có'}",
                "───────────────────────",
                "📌 ĐỂ ĐĂNG NHẬP VÀO SHOPEE:",
                "👉 Cách 1 (Khuyên dùng): Khi Shopee yêu cầu xác minh, bấm 'Xác minh bằng phương thức khác' ➔ Chọn 'Xác nhận đăng nhập trên thiết bị khác' ➔ Rồi quay lại đây nhắn: XACMINH " + str(stock.id) + " (Bot sẽ tự động bấm Đồng ý!).",
                "👉 Cách 2: Đăng nhập bằng Cookie (SPC_F & SPC_ST) được cấp trong đơn hàng để vào thẳng không cần xác minh."
            ]
            send_zalo_message(zalo_user_id, "\n".join(no_pass_lines))
        else:
            not_found_lines = [
                f"ℹ️ CHƯA THẤY YÊU CẦU XÁC MINH MỚI CHO [#{stock.id}]\n",
                f"• Nick: {stock.account}",
                f"• Email nhận: {stock.assigned_email}",
                "───────────────────────",
                "📌 BẠN LÀM THEO 3 BƯỚC SAU NHÉ:",
                f"1️⃣ Mở app/web Shopee đăng nhập bằng nick: {stock.account}",
                "2️⃣ Khi Shopee yêu cầu xác minh ➔ Bấm chọn 'Xác minh qua Email'",
                f"3️⃣ Đợi 5-10 giây rồi quay lại đây soạn lại: XACMINH {stock.id}\n",
                "✨ Bot sẽ tự động bắt link và duyệt login cho bạn ngay!"
            ]
            send_zalo_message(zalo_user_id, "\n".join(not_found_lines))
        return

    # =========================================================================
    # LỆNH 7: TRA CỨU ĐƠN HÀNG / MÃ VẬN ĐƠN (CO <Mã vận đơn>)
    # =========================================================================
    if first_word in ["CO", "CHECK", "TRA", "TRACK"]:
        send_chat_action(zalo_user_id, "typing")
        if len(parts) < 2:
            send_zalo_message(
                zalo_user_id,
                "🚚 𝐓𝐑𝐀 𝐂Ứ𝐔 𝐕Ậ𝐍 ĐƠ𝐍 𝐒𝐇𝐎𝐏𝐄𝐄 𝐄𝐗𝐏𝐑𝐄𝐒𝐒 (𝐅𝐑𝐄𝐄):\n"
                "───────────────────────\n"
                "👉 Soạn: CO <Mã vận đơn>\n"
                "• Ví dụ SPX: CO SPXVN061857044824\n"
                "• Ví dụ đơn shop: CO DH249985\n\n"
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
    if first_word == "HELP":
        send_chat_action(zalo_user_id, "typing")
        help_lines = [
            "📖 𝐂Ẩ𝐌 𝐍𝐀𝐍𝐆 𝐇ƯỚ𝐍𝐆 𝐃Ẫ𝐍 𝐒Ử 𝐃Ụ𝐍𝐆 𝐁𝐎𝐓 𝟐𝟒/𝟕 ✨\n",
            "🛒 𝟏. 𝐌𝐔𝐀 𝐇À𝐍𝐆 & 𝐓𝐇𝐀𝐍𝐇 𝐓𝐎Á𝐍:",
            "• MENU : Xem bảng giá & số lượng sẵn kho 24/7",
            "• Nhắn số [Mã] : Mua nhanh 1 món (Ví dụ: 1 hoặc 6)",
            "• BUY <Mã> <SL> : Mua nhiều tài khoản (Ví dụ: BUY 1 2)",
            "➔ Quét mã QR SePay, nhận nick/mã tự động trong 3 giây!\n",
            "💳 𝟐. 𝐕Í 𝐒Ố 𝐃Ư & 𝐇𝐎À𝐍 𝐓𝐈Ề𝐍 𝐓Ự ĐỘ𝐍𝐆:",
            "• SODU (hoặc VI) : Xem số dư ví hiện có của bạn.",
            "• TỰ ĐỘNG TRỪ VÍ : Khi ví có đủ tiền, bạn chỉ cần soạn mua (VD: '6' hoặc 'BUY 6'), bot sẽ tự trừ ví & nhả số tức thì không cần chuyển khoản!",
            "• TỰ ĐỘNG HOÀN TIỀN : Quá 5 phút không nhận được OTP hoặc hệ thống hết số, tiền tự động hoàn 100% vào ví bạn ngay lập tức.\n",
            "☕ 𝟑. 𝐂À 𝐏𝐇𝐄̂ 𝐇𝐈𝐆𝐇𝐋𝐀𝐍𝐃𝐒 & 𝐒𝐈𝐌 𝐎𝐓𝐏:",
            "• [7] Highlands Sữa Đá 29k (Giá 7k): BẮT BUỘC xóa app tải lại ➔ Nhập SĐT bot cấp ➔ Bỏ qua mã GT ➔ Đợi 2 phút có mã! (Tối ưu iOS, Android liên hệ Admin).",
            "• [6] Thuê SIM OTP Shopee (Giá 5k): Nhận SĐT sạch ➔ Nhận mã OTP tức thì trong 5 phút. Quá 5p tự hoàn tiền 100% vào ví!\n",
            "📦 𝟒. 𝐐𝐔Ả𝐍 𝐋Ý 𝐍𝐈𝐂𝐊 𝐒𝐇𝐎𝐏𝐄𝐄 ĐÃ 𝐌𝐔𝐀:",
            "• DONHANG : Xem lại toàn bộ nick & mật khẩu đã mua",
            "• ADDMAIL <Mã TK> : Gán email bảo mật tự động",
            "• XACMINH <Mã TK> : Tự động duyệt đăng nhập thiết bị mới\n",
            "🚚 𝟓. 𝐓𝐑𝐀 𝐂Ứ𝐔 ĐƠ𝐍 𝐇À𝐍𝐆 (𝐅𝐑𝐄𝐄 𝟏𝟎𝟎%):",
            "• CO <Mã SPX> : Tra cứu lộ trình bưu kiện Shopee Express",
            "  (Ví dụ: CO SPXVN061857044824)\n",
            "👉 Nhắn 'MENU' để bắt đầu săn deal ngay nào! 🚀"
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