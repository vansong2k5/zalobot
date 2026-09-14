"""
Dịch vụ xử lý các lệnh quản trị viên bí mật qua Zalo Bot (Chỉ dành riêng cho Admin).
Hỗ trợ đầy đủ các tính năng quản trị bán hàng:
1. ADMIN: Bảng điều khiển lệnh
2. THONGKE / TK: Thống kê doanh thu, đơn hàng, khách hàng, tồn kho
3. THEMSP: Thêm sản phẩm mới trực tiếp qua chat Zalo
4. THEMKHO: Nạp thêm tài khoản vào kho trực tiếp qua chat Zalo
5. KHO: Xem chi tiết tồn kho từng sản phẩm
6. DONHANG: Xem các đơn hàng gần nhất
7. BROADCAST / TB: Gửi thông báo hàng loạt tới tất cả khách hàng
"""

from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models import User, Product, ProductStock, Order, BotGroup
from .zalo_service import send_zalo_message
from .user_service import is_admin_user
from .group_service import get_all_active_groups, register_or_update_group


def handle_admin_message(db: Session, zalo_user_id: str, text: str) -> bool:
    """
    Xử lý các lệnh đặc quyền dành riêng cho Admin.
    """
    if not is_admin_user(zalo_user_id):
        return False

    raw_text = text.strip()
    if not raw_text:
        return False

    parts = raw_text.split()
    cmd = parts[0].upper()

    # =========================================================================
    # 1. BẢNG ĐIỀU KHIỂN QUẢN TRỊ (ADMIN)
    # =========================================================================
    if cmd in ["ADMIN", "ADMINHELP", "HELPADMIN", "QT"]:
        admin_menu = (
            "👑 𝐁Ả𝐍𝐆 Đ𝐈Ề𝐔 𝐊𝐇𝐈Ể𝐍 𝐐𝐔Ả𝐍 𝐓𝐑Ị (𝐀𝐃𝐌𝐈𝐍) 👑\n\n"
            "📊 𝐁Á𝐎 𝐂Á𝐎 & 𝐓𝐇Ố𝐍𝐆 𝐊Ê:\n"
            "• TK (hoặc THONGKE) ➔ Doanh thu, đơn hàng & tồn kho\n"
            "• DONHANG (hoặc DH) ➔ Xem 5 đơn hàng mới nhất\n"
            "• KHO ➔ Xem chi tiết tồn kho từng sản phẩm\n\n"
            "📦 𝐐𝐔Ả𝐍 𝐋Ý 𝐒Ả𝐍 𝐏𝐇Ẩ𝐌 & 𝐍Ạ𝐏 𝐊𝐇𝐎:\n"
            "• THEMSP <Tên SP> | <Giá> [| <Mô tả>]\n"
            "  👉 VD: THEMSP Spotify 3 Tháng | 30000 | Bản quyền gia hạn chính chủ\n\n"
            "• THEMKHO <Mã SP> | <Tài khoản> | <Mật khẩu> [| SĐT] [| Cookie_F] [| Cookie_ST]\n"
            "  👉 VD: THEMKHO 1 | user01 | Pass123@ | 0912345678 | spc_f=xxx | spc_st=yyy\n\n"
            "💰 𝐐𝐔Ả𝐍 𝐋Ý 𝐕Í & 𝐇𝐎À𝐍 𝐓𝐈Ề𝐍:\n"
            "• HOANTIEN <Mã Đơn / Zalo ID> [Số tiền] [| Lý do]\n"
            "  👉 VD: HOANTIEN DH830595 15000 | Bù lỗi thuê sim\n"
            "• CONGTIEN <Zalo ID / User ID> <Số tiền>\n"
            "• CHECKVI <Zalo ID / Tên khách>\n\n"
            "📢 𝐓𝐇Ô𝐍𝐆 𝐁Á𝐎 𝐇À𝐍𝐆 𝐋𝐎Ạ𝐓:\n"
            "• TB <Nội dung> ➔ Gửi tới TẤT CẢ Nhóm Zalo\n"
            "• TBUSER <Nội dung> ➔ Gửi riêng tới từng Khách hàng cá nhân\n\n"
            "👥 𝐐𝐔Ả𝐍 𝐋Ý 𝐍𝐇Ó𝐌 𝐙𝐀𝐋𝐎:\n"
            "• GROUPS ➔ Xem danh sách nhóm bot đang tham gia\n"
            "• ADDGROUP <Mã Nhóm> [| Tên Nhóm] ➔ Thêm nhóm thủ công\n"
            "• DELGROUP <Mã Nhóm> ➔ Xóa nhóm nhận thông báo\n"
            "• TESTGROUP ➔ Bắn thử thông báo test vào tất cả nhóm\n\n"
            "🌐 𝐐𝐔Ả𝐍 𝐋Ý 𝐏𝐑𝐎𝐗𝐘 𝐒𝐇𝐎𝐏𝐄𝐄:\n"
            "• ADDPROXY <ip:port hoặc user:pass@ip:port> ➔ Thêm proxy mới\n"
            "• LISTPROXY (hoặc PROXIES) ➔ Xem danh sách & test proxy sống/chết\n"
            "• DELPROXY <Số thứ tự hoặc URL> ➔ Xóa proxy\n"
            "• CLEARPROXY ➔ Xóa hết, quay về proxy tự host VPS\n\n"
            "🛠️ 𝐐𝐔Ả𝐍 𝐋Ý 𝐃Ị𝐂𝐇 𝐕Ụ & 𝐓Í𝐍𝐇 𝐍Ă𝐍𝐆 (𝐁Ậ𝐓/𝐓Ắ𝐓):\n"
            "• DUNGSP <Mã SP> ➔ Tạm dừng 1 dịch vụ (vd: DUNGSP 1)\n"
            "• BATSP <Mã SP> ➔ Mở lại 1 dịch vụ (vd: BATSP 1)\n"
            "• DSSP ➔ Danh sách trạng thái tất cả dịch vụ\n"
            "• DSTN (hoặc FEATURES) ➔ Bảng trạng thái các tính năng Bot\n"
            "• DUNGTN <Mã TN> ➔ Dừng 1 tính năng (vd: DUNGTN CHECKSDT, DUNGTN QUICK_BUY)\n"
            "• BATTN <Mã TN> ➔ Bật lại 1 tính năng (vd: BATTN CHECKSDT)\n\n"
            "💡 Mẹo: Chạm giữ tin nhắn để sao chép cú pháp mẫu nhanh!"
        )
        send_zalo_message(zalo_user_id, admin_menu)
        return True


    # =========================================================================
    # 2. THỐNG KÊ DOANH THU & ĐƠN HÀNG (THONGKE / TK)
    # =========================================================================
    if cmd in ["THONGKE", "STATS", "TK"]:
        now = datetime.utcnow()
        start_of_today = datetime(now.year, now.month, now.day)

        total_users = db.query(User).count()
        total_orders = db.query(Order).count()
        completed_orders = db.query(Order).filter(Order.status == "completed").all()
        today_completed = db.query(Order).filter(
            Order.status == "completed",
            Order.completed_at >= start_of_today
        ).all()

        total_revenue = sum(o.price for o in completed_orders)
        today_revenue = sum(o.price for o in today_completed)

        total_stock = db.query(ProductStock).count()
        available_stock = db.query(ProductStock).filter(ProductStock.status == "available").count()
        sold_stock = db.query(ProductStock).filter(ProductStock.status == "sold").count()

        report = (
            "📊 𝐁Á𝐎 𝐂Á𝐎 𝐊𝐈𝐍𝐇 𝐃𝐎𝐀𝐍𝐇 & 𝐓Ồ𝐍 𝐊𝐇𝐎 📊\n\n"
            f"💰 Doanh thu hôm nay: {int(today_revenue):,} VNĐ ({len(today_completed)} đơn thành công)\n"
            f"💎 Tổng doanh thu toàn sàn: {int(total_revenue):,} VNĐ\n\n"
            f"🛒 Tổng đơn hàng đã tạo: {total_orders} đơn\n"
            f"✅ Đơn hoàn thành: {len(completed_orders)} đơn\n"
            f"👥 Tổng khách hàng: {total_users} người\n\n"
            "📦 TÌNH TRẠNG KHO VẬT LÝ (ProductStock):\n"
            f"• 🟢 Còn sẵn: {available_stock} tài khoản\n"
            f"• 🔴 Đã bán: {sold_stock} tài khoản\n"
            f"• 📥 Tổng đã nạp: {total_stock} tài khoản"
        )
        send_zalo_message(zalo_user_id, report)
        return True

    # =========================================================================
    # 3. XEM TỒN KHO CHI TIẾT (KHO)
    # =========================================================================
    if cmd in ["KHO", "STOCK", "TONKHO"]:
        products = db.query(Product).order_by(Product.id.asc()).all()
        if not products:
            send_zalo_message(zalo_user_id, "📦 Hiện chưa có sản phẩm nào trong hệ thống.")
            return True

        lines = [
            "📦 𝐂𝐇𝐈 𝐓𝐈Ế𝐓 𝐓Ồ𝐍 𝐊𝐇𝐎 𝐓Ừ𝐍𝐆 𝐒Ả𝐍 𝐏𝐇Ẩ𝐌\n"
        ]
        for p in products:
            p_lower = p.product_name.lower()
            is_auto = (p.id in [3, 5, 6, 7]) or ("drive" in p_lower) or ("thuê sim" in p_lower) or ("highlands" in p_lower) or ("otp" in p_lower)
            p_paused = (p.status != "active")
            pause_tag = " [🔴 TẠM DỪNG / BẢO TRÌ]" if p_paused else ""

            if is_auto:
                status_text = "🔴 Tạm dừng nhận đơn" if p_paused else "🟢 Sẵn sàng 24/7 (Cấp tự động)"
                lines.append(f"• [{p.id}] {p.product_name}{pause_tag} ➔ {int(p.price):,}đ")
                lines.append(f"  └ {status_text}\n")
            else:
                avail = db.query(ProductStock).filter(
                    ProductStock.product_id == p.id,
                    ProductStock.status == "available"
                ).count()
                sold = db.query(ProductStock).filter(
                    ProductStock.product_id == p.id,
                    ProductStock.status == "sold"
                ).count()
                status_icon = "🔴" if p_paused else ("🟢" if avail > 0 else "🔴")
                lines.append(f"• [{p.id}] {p.product_name}{pause_tag} ➔ {int(p.price):,}đ")
                lines.append(f"  └ {status_icon} Còn: {avail} acc | Đã bán: {sold} acc\n")

        lines.append("💡 Cú pháp thao tác:")
        lines.append("• Dừng/Bật dịch vụ: DUNGSP <Mã> / BATSP <Mã>")
        lines.append("• Nạp kho: THEMKHO <Mã SP> | <Nick> | <Pass>")
        send_zalo_message(zalo_user_id, "\n".join(lines))
        return True

    # =========================================================================
    # 4. XEM CÁC ĐƠN HÀNG MỚI NHẤT (DONHANG)
    # =========================================================================
    if cmd in ["DONHANG", "ORDERS", "DH"]:
        recent_orders = db.query(Order).order_by(Order.id.desc()).limit(5).all()
        if not recent_orders:
            send_zalo_message(zalo_user_id, "🛒 Chưa có đơn hàng nào được tạo.")
            return True

        lines = [
            "🛒 𝟓 ĐƠ𝐍 𝐇À𝐍𝐆 𝐌Ớ𝐈 𝐍𝐇Ấ𝐓\n"
        ]
        status_map = {
            "pending": "⏳ Chờ thanh toán",
            "completed": "✅ Hoàn tất",
            "expired": "⏱️ Hết hạn",
            "cancelled": "❌ Đã hủy"
        }
        for o in recent_orders:
            st = status_map.get(o.status, o.status)
            c_name = o.user.display_name if o.user else "Khách"
            p_name = o.product.product_name if o.product else "Sản phẩm"
            time_str = o.created_at.strftime("%H:%M %d/%m") if o.created_at else ""
            lines.append(f"• #{o.order_code} ({time_str}) ➔ {st}")
            lines.append(f"  └ {p_name} x{o.quantity} | {int(o.price):,}đ | Khách: {c_name}\n")

        send_zalo_message(zalo_user_id, "\n".join(lines))
        return True

    # =========================================================================
    # 5. THÊM SẢN PHẨM MỚI TRỰC TIẾP QUA CHAT (THEMSP)
    # Cú pháp: THEMSP <Tên SP> | <Giá> [| <Mô tả>]
    # =========================================================================
    if cmd in ["THEMSP", "ADSP", "ADDSP", "NEWSP"]:
        payload = raw_text[len(parts[0]):].strip()
        subparts = [p.strip() for p in payload.split("|")]
        if len(subparts) < 2:
            send_zalo_message(
                zalo_user_id,
                "⚠️ Cú pháp thêm sản phẩm:\n"
                "THEMSP <Tên sản phẩm> | <Giá tiền> [| <Mô tả>]\n\n"
                "Ví dụ:\n"
                "THEMSP Spotify Premium 1 Tháng | 25000 | Tài khoản nghe nhạc bản quyền"
            )
            return True

        prod_name = subparts[0]
        try:
            price_val = Decimal(subparts[1].replace(",", "").replace(".", "").replace("đ", "").replace("VND", "").strip())
        except Exception:
            send_zalo_message(zalo_user_id, "⚠️ Giá tiền không hợp lệ. Vui lòng nhập số (Ví dụ: 25000).")
            return True

        description_val = subparts[2] if len(subparts) >= 3 else ""

        new_prod = Product(
            product_name=prod_name,
            price=price_val,
            description=description_val,
            commission_rate=Decimal("10.0"),  # Mặc định hoa hồng 10%
            status="active",
            created_at=datetime.utcnow()
        )
        db.add(new_prod)
        db.commit()
        db.refresh(new_prod)

        send_zalo_message(
            zalo_user_id,
            f"✅ ĐÃ THÊM SẢN PHẨM MỚI THÀNH CÔNG!\n"
            f"💎 Mã sản phẩm: [{new_prod.id}]\n"
            f"📦 Tên sản phẩm: {new_prod.product_name}\n"
            f"💰 Giá bán: {int(new_prod.price):,} VNĐ\n"
            f"📝 Mô tả: {new_prod.description or 'Không có'}\n\n"
            f"👉 Bây giờ bạn có thể nạp tài khoản vào kho bằng lệnh:\n"
            f"THEMKHO {new_prod.id} | <Email> | <Mật khẩu>"
        )
        return True

    # =========================================================================
    # 6. NẠP TÀI KHOẢN VÀO KHO TRỰC TIẾP QUA CHAT (THEMKHO)
    # Cú pháp: THEMKHO <Mã SP> | <Tài khoản> | <Mật khẩu> [| SĐT]
    # =========================================================================
    if cmd in ["THEMKHO", "ADSTOCK", "ADDSTOCK", "NAPKHO"]:
        payload = raw_text[len(parts[0]):].strip()
        subparts = [p.strip() for p in payload.split("|")]
        if len(subparts) < 3:
            send_zalo_message(
                zalo_user_id,
                "⚠️ Cú pháp nạp tài khoản vào kho:\n"
                "THEMKHO <Mã SP> | <Tài khoản/Email> | <Mật khẩu> [| SĐT]\n\n"
                "Ví dụ:\n"
                "THEMKHO 1 | user@netflix.com | Pass123456"
            )
            return True

        try:
            target_prod_id = int(subparts[0])
        except ValueError:
            send_zalo_message(zalo_user_id, "⚠️ Mã sản phẩm phải là số. Ví dụ: THEMKHO 1 | ...")
            return True

        prod = db.query(Product).filter(Product.id == target_prod_id).first()
        if not prod:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy sản phẩm có mã [{target_prod_id}].")
            return True

        account_val = subparts[1]
        password_val = subparts[2]
        phone_val = subparts[3] if len(subparts) >= 4 and subparts[3] else None
        cookie_f_val = subparts[4] if len(subparts) >= 5 and subparts[4] else None
        cookie_st_val = subparts[5] if len(subparts) >= 6 and subparts[5] else None

        new_stock = ProductStock(
            product_id=prod.id,
            account=account_val,
            password=password_val,
            sdt=phone_val,
            cookie_spc_f=cookie_f_val,
            cookie_spc_st=cookie_st_val,
            status="available",
            created_at=datetime.utcnow()
        )
        db.add(new_stock)
        db.commit()

        # Đếm lại tồn kho hiện tại
        curr_stock = db.query(ProductStock).filter(
            ProductStock.product_id == prod.id,
            ProductStock.status == "available"
        ).count()

        extra_info = []
        if phone_val:
            extra_info.append(f"• SĐT: {phone_val}")
        if cookie_f_val:
            extra_info.append("• Đã nạp Cookie SPC_F")
        if cookie_st_val:
            extra_info.append("• Đã nạp Cookie SPC_ST")
        extra_str = ("\n" + "\n".join(extra_info)) if extra_info else ""

        send_zalo_message(
            zalo_user_id,
            f"✅ ĐÃ NẠP TÀI KHOẢN VÀO KHO THÀNH CÔNG!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Sản phẩm: [{prod.id}] {prod.product_name}\n"
            f"🔑 Tài khoản: {account_val}\n"
            f"🔒 Mật khẩu: {password_val}{extra_str}\n"
            f"📊 Tổng tồn kho hiện tại: {curr_stock} cái"
        )
        return True


    # =========================================================================
    # 7. GỬI THÔNG BÁO HÀNG LOẠT (BROADCAST / TB / TBGROUP / TBUSER)
    # =========================================================================
    if cmd == "TB":
        content_to_broadcast = raw_text[len(parts[0]):].strip()
        if not content_to_broadcast:
            send_zalo_message(
                zalo_user_id,
                "⚠️ Cú pháp gửi thông báo hàng loạt:\n"
                "• TB <Nội dung> : Chỉ gửi riêng tới các Nhóm Zalo\n"
                "• TBUSER <Nội dung> : Chỉ gửi riêng tới từng Khách hàng cá nhân\n\n"
                "Ví dụ:\n"
                "BROADCAST Kho vừa về thêm tài khoản Netflix giá siêu rẻ, mời bạn vào xem!"
            )
            return True

        send_to_groups = cmd in ["TB"]
        send_to_users = cmd in ["TBUSER"]

        formatted_msg = (
            "📢 THÔNG BÁO:\n"
            f"{content_to_broadcast}"
        )

        group_success = 0
        group_fail = 0
        target_group_ids = get_all_active_groups(db) if send_to_groups else []

        user_success = 0
        user_fail = 0
        users = db.query(User).filter(User.status == "active").all() if send_to_users else []

        status_prefix = []
        if send_to_groups:
            status_prefix.append(f"{len(target_group_ids)} nhóm")
        if send_to_users:
            status_prefix.append(f"{len(users)} khách hàng")

        send_zalo_message(zalo_user_id, f"🚀 Đang tiến hành gửi thông báo tới {' và '.join(status_prefix)}...")

        # 1. Gửi tới các Nhóm Zalo
        if send_to_groups:
            for gid in target_group_ids:
                ok = send_zalo_message(gid, formatted_msg)
                if ok:
                    group_success += 1
                else:
                    group_fail += 1

        # 2. Gửi tới từng Khách hàng cá nhân
        if send_to_users:
            for u in users:
                if u.user_id == zalo_user_id:
                    continue
                ok = send_zalo_message(u.user_id, formatted_msg)
                if ok:
                    user_success += 1
                else:
                    user_fail += 1

        result_lines = ["✅ HOÀN TẤT GỬI THÔNG BÁO HÀNG LOẠT!"]
        if send_to_groups:
            result_lines.append(f"👥 Nhóm Zalo: {group_success}/{len(target_group_ids)} nhóm thành công (Lỗi: {group_fail})")
        if send_to_users:
            total_target_users = max(0, len(users) - 1)  # Trừ admin ra
            result_lines.append(f"👤 Khách hàng cá nhân: {user_success}/{total_target_users} người thành công (Lỗi: {user_fail})")

        send_zalo_message(zalo_user_id, "\n".join(result_lines))
        return True

    # =========================================================================
    # 8. QUẢN LÝ NHÓM ZALO (GROUPS / NHOM)
    # =========================================================================
    if cmd in ["GROUPS", "NHOM", "DSNHOM"]:
        db_groups = db.query(BotGroup).order_by(BotGroup.id.asc()).all()
        lines = [
            "👥 DANH SÁCH NHÓM ZALO NHẬN THÔNG BÁO\n",
        ]
        if not db_groups:
            lines.append("Hiện tại chưa có nhóm nào được lưu trong CSDL.")
            lines.append("👉 Cách thêm nhóm:")
            lines.append("1. Thêm bot vào nhóm và gửi 1 tin nhắn bất kỳ trong nhóm (bot sẽ tự động nhận diện và lưu).")
            lines.append("2. Hoặc dùng lệnh: ADDGROUP <Mã Nhóm> [| Tên Nhóm]")
        else:
            for idx, g in enumerate(db_groups, 1):
                status_icon = "🟢" if g.status == "active" else "🔴"
                lines.append(f"{idx}. {status_icon} {g.group_name}")
                lines.append(f"   • ID: {g.group_id}")
                lines.append(f"   • Trạng thái: {g.status}")
                lines.append("")

        lines.append("👉 Lệnh hỗ trợ:")
        lines.append("• TESTGROUP : Gửi thử tin nhắn test vào tất cả các nhóm")
        lines.append("• DELGROUP <Mã Nhóm> : Xóa nhóm")
        send_zalo_message(zalo_user_id, "\n".join(lines))
        return True

    # =========================================================================
    # 9. THÊM NHÓM THỦ CÔNG (ADDGROUP)
    # =========================================================================
    if cmd in ["ADDGROUP", "THEMNHOM"]:
        raw_params = raw_text[len(parts[0]):].strip()
        sub_parts = [p.strip() for p in raw_params.split("|")]
        if not sub_parts or not sub_parts[0]:
            send_zalo_message(
                zalo_user_id,
                "⚠️ Cú pháp: ADDGROUP <Mã Nhóm> [| Tên Nhóm]\nVí dụ: ADDGROUP g_123456789 | Nhóm Khách Hàng VIP"
            )
            return True

        gid = sub_parts[0]
        gname = sub_parts[1] if len(sub_parts) > 1 else "Nhóm Zalo"
        register_or_update_group(db, gid, gname)
        send_zalo_message(zalo_user_id, f"✅ Đã thêm nhóm thành công!\n• Tên: {gname}\n• ID: {gid}")
        return True

    # =========================================================================
    # 10. XÓA NHÓM (DELGROUP)
    # =========================================================================
    if cmd in ["DELGROUP", "XOANHOM"]:
        if len(parts) < 2:
            send_zalo_message(zalo_user_id, "⚠️ Cú pháp: DELGROUP <Mã Nhóm>")
            return True

        gid = parts[1].strip()
        deleted = db.query(BotGroup).filter(BotGroup.group_id == gid).delete()
        db.commit()
        if deleted:
            send_zalo_message(zalo_user_id, f"✅ Đã xóa nhóm có ID '{gid}' khỏi danh sách thông báo.")
        else:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy nhóm có ID '{gid}' trong CSDL.")
        return True

    # =========================================================================
    # 11. TEST BẮN THÔNG BÁO TỚI CÁC NHÓM (TESTGROUP)
    # =========================================================================
    if cmd in ["TESTGROUP", "TESTNHOM"]:
        group_ids = get_all_active_groups(db)
        if not group_ids:
            send_zalo_message(
                zalo_user_id,
                "⚠️ Chưa có nhóm nào được đăng ký!\n"
                "👉 Hãy thêm Bot vào nhóm Zalo rồi nhắn 1 tin bất kỳ vào nhóm, hoặc dùng lệnh:\n"
                "ADDGROUP <Mã Nhóm>"
            )
            return True

        test_content = (
            "🎉KHÁCH HÀNG VỪA MUA HÀNG THÀNH CÔNG! 🛍️\n"
            "👤 Khách hàng: V*** S***\n"
            "📦 Sản phẩm: Shopee New (Cookie SPC_F + SPC_ST) x1\n"
            "💰 Giá trị: 6,000 VNĐ\n"
            "⚡ Trạng thái: Đã tự động xuất kho & bàn giao tài khoản tức thì!\n"
            "👉 Nhắn tin riêng cho Bot soạn 'MENU' để xem sản phẩm và mua tài khoản tự động 24/7 nhé! ✨"
        )
        send_zalo_message(zalo_user_id, f"🚀 Đang gửi tin nhắn test tới {len(group_ids)} nhóm...")

        success_cnt = 0
        for gid in group_ids:
            ok = send_zalo_message(gid, test_content)
            if ok:
                success_cnt += 1

        send_zalo_message(
            zalo_user_id,
            f"✅ KẾT QUẢ GỬI TEST:\n• Thành công: {success_cnt}/{len(group_ids)} nhóm"
        )
        return True

    # =========================================================================
    # 12. HOÀN TIỀN VÀO VÍ KHÁCH HÀNG (HOANTIEN <Mã Đơn / Zalo ID> [Số tiền] [| Lý do])
    # =========================================================================
    if cmd in ["HOANTIEN", "HOAN"]:
        sub_text = raw_text[len(parts[0]):].strip()
        reason = "Admin hoàn tiền đơn hàng"
        if "|" in sub_text:
            p_split = sub_text.split("|", 1)
            sub_text = p_split[0].strip()
            reason = p_split[1].strip()

        sub_parts = sub_text.split()
        if not sub_parts:
            send_zalo_message(zalo_user_id, "⚠️ Cú pháp: HOANTIEN <Mã Đơn / Zalo ID> [Số tiền] [| Lý do]\n👉 VD: HOANTIEN DH830595 15000 | Bù lỗi thuê sim")
            return True

        target_id = sub_parts[0].strip()
        target_user = None
        refund_amount = None

        # 1. Tìm theo mã đơn hàng
        order = db.query(Order).filter(Order.order_code == target_id.upper()).first()
        if order:
            target_user = order.user
            refund_amount = float(order.price)
            order.status = "cancelled"
            order.account_delivered = f"Admin hoàn tiền ({reason})"

        # 2. Nếu không tìm thấy theo đơn, tìm theo Zalo ID hoặc User ID
        if not target_user:
            if target_id.isdigit():
                target_user = db.query(User).filter(User.id == int(target_id)).first()
            if not target_user:
                target_user = db.query(User).filter(User.user_id == target_id).first()

        if not target_user:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy Đơn hàng hoặc Khách hàng nào khớp với '{target_id}'!")
            return True

        # Nếu có truyền số tiền cụ thể trong tham số
        if len(sub_parts) >= 2:
            try:
                refund_amount = float(sub_parts[1].replace(",", "").replace(".", "").strip())
            except ValueError:
                pass

        if not refund_amount or refund_amount <= 0:
            refund_amount = 5000.0

        old_balance = float(target_user.balance or 0.0)
        target_user.balance = old_balance + refund_amount
        db.commit()
        db.refresh(target_user)

        # Gửi thông báo cho Admin
        send_zalo_message(
            zalo_user_id,
            f"✅ HOÀN TIỀN THÀNH CÔNG! 🎉\n\n"
            f"👤 Khách hàng: {target_user.display_name} (ID: {target_user.id})\n"
            f"🔑 Zalo ID: {target_user.user_id}\n"
            f"💰 Số tiền hoàn: +{int(refund_amount):,} VNĐ\n"
            f"💼 Số dư ví mới: {int(target_user.balance):,} VNĐ\n"
            f"📝 Lý do: {reason}"
        )

        # Bắn tin nhắn Zalo gửi thẳng cho Khách
        customer_notice = (
            f"🎁 THÔNG BÁO HOÀN TIỀN VÀO VÍ TỪ ADMIN! 🎉\n\n"
            f"Chào bạn {target_user.display_name},\n"
            f"Admin vừa chuyển hoàn tiền vào ví số dư của bạn:\n\n"
            f"💰 Số tiền: +{int(refund_amount):,} VNĐ\n"
            f"📝 Lý do: {reason}\n"
            f"💼 Số dư ví hiện tại: {int(target_user.balance):,} VNĐ\n\n"
            f"👉 Bạn có thể soạn 'SODU' để kiểm tra ví, hoặc soạn lệnh mua để mua dịch vụ tự động bằng số dư ví nhé! ✨"
        )
        send_zalo_message(target_user.user_id, customer_notice)
        return True

    # =========================================================================
    # 13. CỘNG TIỀN / TRỪ TIỀN VÍ (CONGTIEN <ID> <Số tiền>)
    # =========================================================================
    if cmd in ["CONGTIEN", "NAPTIEN", "ADDMONEY"]:
        if len(parts) < 3:
            send_zalo_message(zalo_user_id, "⚠️ Cú pháp: CONGTIEN <Zalo ID / User ID> <Số tiền>")
            return True

        target_id = parts[1].strip()
        try:
            amt = float(parts[2].replace(",", "").replace(".", "").strip())
        except ValueError:
            send_zalo_message(zalo_user_id, "⚠️ Số tiền không hợp lệ!")
            return True

        target_user = None
        if target_id.isdigit():
            target_user = db.query(User).filter(User.id == int(target_id)).first()
        if not target_user:
            target_user = db.query(User).filter(User.user_id == target_id).first()

        if not target_user:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy user '{target_id}'!")
            return True

        target_user.balance = float(target_user.balance or 0.0) + amt
        db.commit()
        db.refresh(target_user)

        send_zalo_message(
            zalo_user_id,
            f"✅ Đã cộng {int(amt):,} VNĐ cho {target_user.display_name} (Zalo ID: {target_user.user_id}). Số dư mới: {int(target_user.balance):,} VNĐ."
        )

        send_zalo_message(
            target_user.user_id,
            f"💰 VÍ CỦA BẠN ĐÃ ĐƯỢC CỘNG TIỀN TỪ ADMIN! 💳\n"
            f"💵 Số tiền: +{int(amt):,} VNĐ\n"
            f"💼 Số dư ví hiện tại: {int(target_user.balance):,} VNĐ\n"
            f"👉 Soạn 'SODU' để xem chi tiết ví nhé!"
        )
        return True

    # =========================================================================
    # 14. QUẢN LÝ PROXY SHOPEE CHO ADMIN
    # =========================================================================
    from .proxy_service import (
        add_admin_proxy,
        load_admin_proxies,
        remove_admin_proxy,
        clear_admin_proxies,
        test_proxy_alive,
        get_active_proxy
    )

    if cmd in ["ADDPROXY", "THEMPROXY"]:
        if len(parts) < 2:
            send_zalo_message(
                zalo_user_id,
                "⚠️ Cú pháp: ADDPROXY <ip:port hoặc http://user:pass@ip:port>\n"
                "👉 Ví dụ 1: ADDPROXY 103.154.12.34:8080\n"
                "👉 Ví dụ 2: ADDPROXY user:pass@103.154.12.34:8080"
            )
            return True

        proxy_str = parts[1].strip()
        send_zalo_message(zalo_user_id, f"⏳ Đang kiểm tra kết nối proxy {proxy_str}...")
        is_alive, msg = test_proxy_alive(proxy_str, timeout=6.0)

        if not is_alive:
            send_zalo_message(
                zalo_user_id,
                f"❌ PROXY KHÔNG HOẠT ĐỘNG HOẶC ĐÃ HẾT HẠN!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 Proxy: {proxy_str}\n"
                f"⚠️ Chi tiết: {msg}\n\n"
                f"👉 Vui lòng kiểm tra lại địa chỉ IP, cổng và trạng thái gói proxy của bạn."
            )
            return True

        ok, added_p = add_admin_proxy(proxy_str)
        # Đồng bộ nạp luôn vào kho DB để phục vụ lệnh REG Shopee
        from .user_proxy_service import add_user_proxies
        add_user_proxies(zalo_user_id, proxy_str)

        if ok:
            send_zalo_message(
                zalo_user_id,
                f"✅ ĐÃ THÊM PROXY THÀNH CÔNG! 🎉\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🌐 Proxy: {added_p}\n"
                f"📶 Trạng thái: 🟢 Hoạt động tốt (IP: {msg})\n"
                f"📦 Đã kích hoạt cho cả hệ thống Admin & kho Reg Shopee!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 Soạn 'REG 1' để bắt đầu tạo tài khoản Shopee ngay! 🚀"
            )
        else:
            send_zalo_message(zalo_user_id, f"❌ Không thể thêm proxy: {added_p}")
        return True

    if cmd in ["LISTPROXY", "PROXIES", "CHECKPROXY", "DS_PROXY"]:
        send_zalo_message(zalo_user_id, "⏳ Đang kiểm tra trạng thái toàn bộ danh sách proxy...")
        proxies = load_admin_proxies()
        active_now = get_active_proxy()

        lines = ["🌐 𝐃𝐀𝐍𝐇 𝐒Á𝐂𝐇 𝐏𝐑𝐎𝐗𝐘 𝐇Ệ 𝐓𝐇Ố𝐍𝐆\n"]

        if not proxies:
            lines.append("ℹ️ Chưa có proxy riêng nào của Admin.")
            lines.append(f"👉 Đang dùng Fallback mặc định: Proxy VPS tự host ({active_now})\n")
        else:
            for idx, p in enumerate(proxies, 1):
                is_alive, info = test_proxy_alive(p, timeout=4.0)
                icon = "🟢" if is_alive else "🔴"
                current_tag = " (ĐANG DÙNG)" if p == active_now else ""
                lines.append(f"{idx}. {icon} {p}{current_tag}")
                lines.append(f"   └ Trạng thái: {info}\n")

        lines.extend([
            "👉 THAO TÁC NHANH:",
            "• Thêm proxy: ADDPROXY <ip:port>",
            "• Xóa proxy: DELPROXY <Số thứ tự>",
            "• Xóa hết: CLEARPROXY"
        ])
        send_zalo_message(zalo_user_id, "\n".join(lines))
        return True

    if cmd in ["DELPROXY", "XOAPROXY"]:
        if len(parts) < 2:
            send_zalo_message(zalo_user_id, "⚠️ Cú pháp: DELPROXY <Số thứ tự hoặc Chuỗi proxy>\nVí dụ: DELPROXY 1")
            return True

        target = parts[1].strip()
        ok, res = remove_admin_proxy(target)
        if ok:
            active_now = get_active_proxy()
            send_zalo_message(
                zalo_user_id,
                f"✅ ĐÃ XÓA PROXY THÀNH CÔNG!\n\n"
                f"🗑️ Đã xóa: {res}\n"
                f"🌐 Proxy đang kích hoạt hiện tại: {active_now}"
            )
        else:
            send_zalo_message(zalo_user_id, f"❌ Lỗi: {res}")
        return True

    if cmd in ["CLEARPROXY", "XOAHETPROXY"]:
        cnt = clear_admin_proxies()
        active_now = get_active_proxy()
        send_zalo_message(
            zalo_user_id,
            f"🧹 ĐÃ XÓA TOÀN BỘ {cnt} PROXY CỦA ADMIN!\n\n"
            f"👉 Hệ thống đã chuyển về sử dụng Proxy tự host của VPS ({active_now}) 🚀"
        )
        return True

    # =========================================================================
    # =========================================================================
    # 15. QUẢN LÝ DỪNG / BẢO TRÌ DỊCH VỤ & TÍNH NĂNG (BAOTRI / MO / DUNGSP / BATSP / DSSP / DSTN)
    # =========================================================================
    if cmd in ["BAOTRI", "DUNGSP", "DUNG", "PAUSESP", "DUNGTN"]:
        from .feature_service import SYSTEM_FEATURES, set_feature_status
        if len(parts) < 2:
            feat_list = ", ".join(SYSTEM_FEATURES.keys())
            send_zalo_message(
                zalo_user_id,
                f"🛠️ CÚ PHÁP TẠM DỪNG / BẢO TRÌ:\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 Dừng 1 dịch vụ (theo số mã):\n"
                f"   • BAOTRI <Mã SP> (Ví dụ: BAOTRI 6 để dừng Thuê SIM ViOTP)\n\n"
                f"👉 Dừng 1 tính năng toàn bot:\n"
                f"   • BAOTRI <Mã TN> (Ví dụ: BAOTRI OTP hoặc BAOTRI BUY)\n"
                f"   • Mã hỗ trợ: {feat_list}\n\n"
                f"📌 Xem danh sách SP: DSSP | Xem tính năng: DSTN"
            )
            return True

        target_arg = parts[1].strip()

        # Trường hợp 1: Bảo trì theo Mã Sản Phẩm (Số: 1, 2, 6, 7...)
        if target_arg.isdigit():
            p_id = int(target_arg)
            prod = db.query(Product).filter(Product.id == p_id).first()
            if not prod:
                send_zalo_message(zalo_user_id, f"❌ Không tìm thấy dịch vụ/sản phẩm có mã [{p_id}]!")
                return True

            prod.status = "paused"
            db.commit()
            db.refresh(prod)
            send_zalo_message(
                zalo_user_id,
                f"🛑 [ADMIN] ĐÃ CHUYỂN DỊCH VỤ SANG BẢO TRÌ!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Dịch vụ: [{prod.id}] {prod.product_name}\n"
                f"💰 Giá: {int(prod.price):,} VNĐ\n"
                f"⚠️ Trạng thái: 🔴 TẠM DỪNG (Khách bấm mua sẽ bị chặn và báo đang bảo trì)\n\n"
                f"👉 Mở lại dịch vụ bất kỳ lúc nào bằng lệnh: MO {prod.id} (hoặc BATSP {prod.id})"
            )
            return True

        # Trường hợp 2: Bảo trì theo Mã Tính Năng (BUY, OTP, CHECKSDT, DONHANG, PROXY, TRACK...)
        feat_code = target_arg.upper()
        if feat_code in SYSTEM_FEATURES:
            ok = set_feature_status(db, feat_code, is_active=False)
            feat_info = SYSTEM_FEATURES[feat_code]
            if ok:
                send_zalo_message(
                    zalo_user_id,
                    f"🛑 [ADMIN] ĐÃ TẠM DỪNG TÍNH NĂNG THÀNH CÔNG!\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚙️ Tính năng: [{feat_code}] {feat_info['name']}\n"
                    f"📝 Chi tiết: {feat_info['desc']}\n"
                    f"⚠️ Trạng thái: 🔴 TẠM DỪNG (Khách dùng tính năng này sẽ nhận thông báo bảo trì)\n\n"
                    f"👉 Mở lại tính năng bất kỳ lúc nào bằng lệnh: MO {feat_code} (hoặc BATTN {feat_code})"
                )
            else:
                send_zalo_message(zalo_user_id, f"❌ Lỗi khi cập nhật trạng thái tính năng {feat_code}!")
            return True

        send_zalo_message(zalo_user_id, f"❌ Mã '{target_arg}' không khớp với mã SP (số) hoặc mã tính năng nào! Nhắn DSSP hoặc DSTN để xem.")
        return True

    if cmd in ["MO", "BAT", "BATSP", "RESUMESP", "STARTDV", "MOSP", "BATTN", "BAT_TINHNANG", "RESUMETN", "STARTTN", "MOTN"]:
        from .feature_service import SYSTEM_FEATURES, set_feature_status
        if len(parts) < 2:
            feat_list = ", ".join(SYSTEM_FEATURES.keys())
            send_zalo_message(
                zalo_user_id,
                f"🟢 CÚ PHÁP MỞ LẠI DỊCH VỤ / TÍNH NĂNG:\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👉 Mở lại 1 dịch vụ (theo số mã):\n"
                f"   • MO <Mã SP> (Ví dụ: MO 6 để mở lại Thuê SIM ViOTP)\n\n"
                f"👉 Mở lại 1 tính năng toàn bot:\n"
                f"   • MO <Mã TN> (Ví dụ: MO OTP hoặc MO BUY)\n"
                f"   • Mã hỗ trợ: {feat_list}"
            )
            return True

        target_arg = parts[1].strip()

        # Trường hợp 1: Mở lại theo Mã Sản Phẩm (Số)
        if target_arg.isdigit():
            p_id = int(target_arg)
            prod = db.query(Product).filter(Product.id == p_id).first()
            if not prod:
                send_zalo_message(zalo_user_id, f"❌ Không tìm thấy dịch vụ/sản phẩm có mã [{p_id}]!")
                return True

            prod.status = "active"
            db.commit()
            db.refresh(prod)
            send_zalo_message(
                zalo_user_id,
                f"🟢 [ADMIN] ĐÃ MỞ LẠI DỊCH VỤ THÀNH CÔNG! 🎉\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Dịch vụ: [{prod.id}] {prod.product_name}\n"
                f"💰 Giá: {int(prod.price):,} VNĐ\n"
                f"📶 Trạng thái: 🟢 ĐANG HOẠT ĐỘNG (Khách hàng có thể đặt mua bình thường)"
            )
            return True

        # Trường hợp 2: Mở lại theo Mã Tính Năng
        feat_code = target_arg.upper()
        if feat_code in SYSTEM_FEATURES:
            ok = set_feature_status(db, feat_code, is_active=True)
            feat_info = SYSTEM_FEATURES[feat_code]
            if ok:
                send_zalo_message(
                    zalo_user_id,
                    f"🟢 [ADMIN] ĐÃ MỞ LẠI TÍNH NĂNG THÀNH CÔNG! 🎉\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚙️ Tính năng: [{feat_code}] {feat_info['name']}\n"
                    f"📝 Chi tiết: {feat_info['desc']}\n"
                    f"📶 Trạng thái: 🟢 HOẠT ĐỘNG BÌNH THƯỜNG (Khách hàng có thể sử dụng lại)"
                )
            else:
                send_zalo_message(zalo_user_id, f"❌ Lỗi khi cập nhật trạng thái tính năng {feat_code}!")
            return True

        send_zalo_message(zalo_user_id, f"❌ Mã '{target_arg}' không khớp với mã SP (số) hoặc mã tính năng nào!")
        return True

    if cmd in ["DSSP", "SANPHAM", "LISTSP"]:
        prods = db.query(Product).order_by(Product.id.asc()).all()
        if not prods:
            send_zalo_message(zalo_user_id, "📦 Hệ thống chưa có sản phẩm nào.")
            return True

        lines = ["📦 𝐃𝐀𝐍𝐇 𝐒Á𝐂𝐇 𝐓Ấ𝐓 𝐂Ả 𝐃Ị𝐂𝐇 𝐕Ụ / 𝐒Ả𝐍 𝐏𝐇Ẩ𝐌:"]
        for p in prods:
            st_icon = "🟢" if p.status == "active" else "🔴"
            st_label = "Hoạt động" if p.status == "active" else "TẠM DỪNG (Bảo trì)"
            lines.append(f"• [{p.id}] {st_icon} {p.product_name} — {int(p.price):,}đ ({st_label})")

        lines.extend([
            "",
            "👉 THAO TÁC QUẢN TRỊ:",
            "• Bảo trì dịch vụ: BAOTRI <Mã SP> (vd: BAOTRI 6)",
            "• Mở lại dịch vụ: MO <Mã SP> (vd: MO 6)"
        ])
        send_zalo_message(zalo_user_id, "\n".join(lines))
        return True

    if cmd in ["DSTN", "FEATURES", "TINHNANG", "FLAGS"]:
        from .feature_service import get_all_features_status
        feats = get_all_features_status(db)
        lines = [
            "⚙️ 𝐁Ả𝐍𝐆 Đ𝐈Ề𝐔 𝐊𝐇𝐈Ể𝐍 𝐓Í𝐍𝐇 𝐍Ă𝐍𝐆 𝐇Ệ 𝐓𝐇Ố𝐍𝐆 ⚙️\n"
        ]
        for code, f in feats.items():
            lines.append(f"• [{code}] {f['status_str']}")
            lines.append(f"  └ Tên: {f['name']}")
            lines.append(f"  └ Mô tả: {f['desc']}\n")

        lines.extend([
            "👉 THAO TÁC NHANH:",
            "• Bảo trì tính năng: BAOTRI <Mã TN> (vd: BAOTRI OTP)",
            "• Mở lại tính năng: MO <Mã TN> (vd: MO OTP)",
            "• Bảo trì dịch vụ: BAOTRI <Mã SP> (vd: BAOTRI 6)",
            "• Mở lại dịch vụ: MO <Mã SP> (vd: MO 6)"
        ])
        send_zalo_message(zalo_user_id, "\n".join(lines))
        return True

    return False

