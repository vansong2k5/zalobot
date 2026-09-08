"""
Dịch vụ xử lý tin nhắn và lệnh từ Khách hàng tương tác qua Zalo Bot.
Tập trung chuyên sâu vào BÁN HÀNG TỰ ĐỘNG & GIAO DỊCH QUA SEPAY:
1. MENU / Danh sách sản phẩm kèm giá & tồn kho trực tiếp.
2. Mua hàng siêu đơn giản: Khách chỉ cần nhắn số [Mã SP] hoặc [MUA <Mã>].
3. Tạo đơn hàng tự động & mã QR SePay chuyển khoản tự động.
4. Quản lý hoa hồng giới thiệu (SODU, REF, RUT).
"""

from decimal import Decimal
from sqlalchemy.orm import Session

from app.models import Product, ProductStock, Order, User
from .zalo_service import send_zalo_message, send_chat_action, send_zalo_photo, send_zalo_sticker
from .user_service import get_or_create_user, is_admin_user
from .order_service import generate_unique_order_code
from .sepay_service import generate_sepay_qr_url, format_payment_instructions
from .admin_bot_service import handle_admin_message
from .group_service import register_or_update_group



def handle_zalo_user_message(
    db: Session,
    zalo_user_id: str,
    display_name: str,
    message_text: str,
    sender_id: str = None,
    is_group: bool = False
):
    """
    Xử lý tương tác của khách hàng với Bot:
    - MENU: Xem danh sách sản phẩm và giá
    - BUY <Mã SP> [Số lượng] (hoặc nhắn số Mã SP): Đặt mua sản phẩm
    - HELP: Xem hướng dẫn sử dụng và danh sách các lệnh
    """
    text = message_text.strip()
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

    # Nếu người dùng nhắn lần đầu qua chat 1-1, gửi sticker chào mừng dễ thương!
    if is_first_time and not is_group_chat:
        send_zalo_sticker(zalo_user_id, "c963283f58a364beeb82")

    # 2. Kiểm tra lệnh Quản trị viên Admin trước (chỉ admin mới gọi được)
    admin_check_id = effective_user_id or zalo_user_id
    if handle_admin_message(db, admin_check_id, text):
        return

    parts = text.split()
    first_word = parts[0].upper()

    # Lệnh lưu/kích hoạt nhóm trực tiếp khi gõ trong Group: SETGROUP / THEMNHOM / /SETGROUP
    if is_group_chat and first_word in ["SETGROUP", "/SETGROUP", "THEMNHOM", "LUUNHOM", "CAPNHATNHOM"]:
        register_or_update_group(db, zalo_user_id)
        send_zalo_message(
            zalo_user_id,
            "✅ Nhóm Zalo này đã được lưu và kích hoạt nhận thông báo đơn hàng tự động thành công! 🎉"
        )
        return

    # Khách gõ nhanh 1 con số (ví dụ: '1') để mua sản phẩm mã 1
    is_quick_buy_number = len(parts) == 1 and parts[0].isdigit()

    # Nếu khách gõ BUY hoặc số mã sản phẩm trong Nhóm: Hướng dẫn nhắn riêng để bảo mật tài khoản
    if is_group_chat and (first_word == "BUY" or is_quick_buy_number):
        send_zalo_message(
            zalo_user_id,
            f"👉 Chào {display_name}! Để đảm bảo quyền riêng tư và bảo mật mật khẩu tài khoản của bạn, vui lòng nhắn tin trực tiếp riêng với Bot để mua hàng nhé! ✨"
        )
        return

    # =========================================================================
    # LỆNH 1: XEM MENU SẢN PHẨM (MENU)
    # =========================================================================
    if first_word == "MENU":
        send_chat_action(zalo_user_id, "typing")
        products = db.query(Product).filter(Product.status == "active").all()
        if not products:
            send_zalo_message(
                zalo_user_id,
                "🌟 Cửa hàng đang cập nhật thêm sản phẩm mới. Bạn vui lòng quay lại sau ít phút nhé!"
            )
            return

        lines = [
            "🛍️ DANH SÁCH DỊCH VỤ / TÀI KHOẢN\n",
        ]
        for p in products:
            stock_count = db.query(ProductStock).filter(
                ProductStock.product_id == p.id,
                ProductStock.status == "available"
            ).count()
            stock_str = f"Còn {stock_count} cái" if stock_count > 0 else "Hết hàng"
            lines.append(f"💎 Mã {p.id}: {p.product_name}")
            lines.append(f"💰 Giá: {int(p.price):,} VNĐ")
            lines.append(f"✅ Tình trạng: {stock_str}")

            if p.description:
                lines.append(f"📝 {p.description}")
            lines.append("")

        lines.append("⚡ CÁCH MUA HÀNG:")
        lines.append("👉 Nhắn số [Mã SP] (Ví dụ: 1)")
        lines.append("👉 Hoặc soạn: BUY <Mã SP> <Số lượng> (Ví dụ: BUY 1 2)")
        lines.append("👉 Soạn: HELP để xem hướng dẫn chi tiết")
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
                    "⚠️ Cú pháp mua hàng:\n"
                    "BUY <Mã SP> [Số lượng]\n\n"
                    "Ví dụ:\n"
                    "• BUY 1 (Mua 1 tài khoản mã 1)\n"
                    "• BUY 1 2 (Mua 2 tài khoản mã 1)\n"
                    "• Hoặc chỉ cần nhắn số: 1"
                )
                return

            try:
                product_id = int(parts[1])
            except ValueError:
                send_zalo_message(zalo_user_id, "⚠️ Mã sản phẩm phải là số. Ví dụ: BUY 1")
                return

            quantity = 1
            if len(parts) >= 3:
                try:
                    quantity = int(parts[2])
                    if quantity <= 0:
                        quantity = 1
                    if quantity > 10:
                        send_zalo_message(zalo_user_id, "⚠️ Mỗi lần đặt mua tối đa 10 tài khoản bạn nhé.")
                        return
                except ValueError:
                    quantity = 1

        # Tìm sản phẩm trong DB
        product = db.query(Product).filter(Product.id == product_id, Product.status == "active").first()
        if not product:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy sản phẩm mã [{product_id}]. Bạn soạn 'MENU' để xem danh sách nhé.")
            return

        # Kiểm tra tồn kho có đủ không
        available_stock = db.query(ProductStock).filter(
            ProductStock.product_id == product.id,
            ProductStock.status == "available"
        ).count()

        if available_stock < quantity:
            if available_stock == 0:
                send_zalo_message(zalo_user_id, f"❌ Sản phẩm '{product.product_name}' hiện tại đang tạm hết hàng trong kho. Bạn quay lại sau nhé!")
            else:
                send_zalo_message(
                    zalo_user_id,
                    f"❌ Sản phẩm '{product.product_name}' hiện chỉ còn {available_stock} tài khoản. "
                    f"Bạn chọn mua số lượng ít hơn nhé!"
                )
            return

        # Tính tiền và tạo đơn hàng
        total_price = product.price * quantity
        order_code = generate_unique_order_code(db)

        new_order = Order(
            order_code=order_code,
            user_id=user.id,
            product_id=product.id,
            quantity=quantity,
            price=total_price,
            status="pending"
        )
        db.add(new_order)
        db.commit()
        db.refresh(new_order)

        # Tạo mã QR SePay động và hướng dẫn chuyển khoản
        qr_url = generate_sepay_qr_url(amount=total_price, order_code=order_code)
        instructions = format_payment_instructions(new_order, product.product_name)

        # Gửi ảnh QR trực tiếp vào khung chat kèm hướng dẫn thanh toán
        photo_sent = send_zalo_photo(zalo_user_id, photo_url=qr_url, caption=instructions)
        if not photo_sent:
            # Dự phòng nếu gửi ảnh lỗi thì gửi tin nhắn text kèm link
            send_zalo_message(zalo_user_id, f"{instructions}\n\n🔗 Link ảnh QR: {qr_url}")
        return

    # =========================================================================
    # LỆNH 3: HƯỚNG DẪN SỬ DỤNG TỔNG QUAN (HELP)
    # =========================================================================
    if first_word == "HELP":
        send_chat_action(zalo_user_id, "typing")
        help_lines = [
            "📖 HƯỚNG DẪN SỬ DỤNG & CÁC LỆNH HỆ THỐNG\n",
            "1️⃣ LỆNH XEM SẢN PHẨM:",
            "• MENU : Hiển thị bảng giá & tồn kho tất cả sản phẩm.\n",
            "2️⃣ LỆNH MUA HÀNG:",
            "• BUY <Mã SP> : Đặt mua 1 tài khoản (Ví dụ: BUY 1)",
            "• BUY <Mã SP> <SL> : Đặt mua nhiều tài khoản (Ví dụ: BUY 1 2)",
            "• <Mã SP> : Mua nhanh chỉ bằng 1 con số (Ví dụ nhắn: 1)\n",
            "3️⃣ QUY TRÌNH THANH TOÁN & NHẬN HÀNG:",
            "• Sau khi chọn mua, Bot sẽ gửi ảnh mã QR thanh toán SePay.",
            "• Bạn dùng app ngân hàng quét mã QR để chuyển khoản chính xác.",
            "• Hệ thống tự động kiểm tra và gửi tài khoản/mật khẩu/cookie ngay lập tức sau 3 giây!\n",
            "4️⃣ LỆNH TRỢ GIÚP:",
            "• HELP : Mở lại bảng hướng dẫn này."
        ]
        if is_admin_user(zalo_user_id):
            help_lines.append("\n👑 LỆNH QUẢN TRỊ VIÊN:\n• ADMIN : Bảng điều khiển quản lý.")

        send_zalo_message(zalo_user_id, "\n".join(help_lines))
        return

    # =========================================================================
    # MẶC ĐỊNH: LỜI CHÀO & HƯỚNG DẪN MUA HÀNG BAN ĐẦU
    # =========================================================================
    send_chat_action(zalo_user_id, "typing")
    products = db.query(Product).filter(Product.status == "active").order_by(Product.id.asc()).all()
    prod_preview = []
    for p in products:
        stock = db.query(ProductStock).filter(
            ProductStock.product_id == p.id,
            ProductStock.status == "available"
        ).count()
        stock_str = f"Còn {stock} cái" if stock > 0 else "Tạm hết"
        prod_preview.append(f"• [Mã {p.id}] {p.product_name} - {int(p.price):,} VNĐ ({stock_str})")

    target_name = user.display_name if user else display_name or "bạn"
    welcome_lines = [
        f"👋 Xin chào {target_name}! Chào mừng bạn đến với Cửa Hàng Tự Động ✨\n",
        "🛒 BẢNG GIÁ SẢN PHẨM HIỆN CÓ:"
    ]
    if prod_preview:
        welcome_lines.extend(prod_preview)
    else:
        welcome_lines.append("• Hiện đang cập nhật thêm sản phẩm mới.")

    welcome_lines.extend([
        "\n⚡ CÁCH MUA HÀNG:",
        "👉 Nhắn số [Mã SP] : Đặt mua nhanh (Ví dụ: 1)",
        "👉 Soạn: BUY <Mã SP> [Số lượng] (Ví dụ: BUY 1 2)",
        "👉 Soạn: MENU : Xem danh sách chi tiết kèm mô tả",
        "👉 Soạn: HELP : Xem hướng dẫn chi tiết"
    ])

    if is_admin_user(admin_check_id):
        welcome_lines.append("\n👑 [QUẢN TRỊ VIÊN] Soạn 'ADMIN' để mở Bảng điều khiển quản lý.")

    send_zalo_message(zalo_user_id, "\n".join(welcome_lines))



