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
            "👑 𝐁Ả𝐍𝐆 Đ𝐈Ề𝐔 𝐊𝐇𝐈Ể𝐍 𝐐𝐔Ả𝐍 𝐓𝐑Ị (𝐀𝐃𝐌𝐈𝐍) 👑\n"
            "───────────────────────\n"
            "📊 𝐁Á𝐎 𝐂Á𝐎 & 𝐓𝐇Ố𝐍𝐆 𝐊Ê:\n"
            "• TK (hoặc THONGKE) ➔ Doanh thu, đơn hàng & tồn kho\n"
            "• DONHANG (hoặc DH) ➔ Xem 5 đơn hàng mới nhất\n"
            "• KHO ➔ Xem chi tiết tồn kho từng sản phẩm\n\n"
            "📦 𝐐𝐔Ả𝐍 𝐋Ý 𝐒Ả𝐍 𝐏𝐇Ẩ𝐌 & 𝐍Ạ𝐏 𝐊𝐇𝐎:\n"
            "• THEMSP <Tên SP> | <Giá> [| <Mô tả>]\n"
            "  👉 VD: THEMSP Spotify 3 Tháng | 30000 | Bản quyền gia hạn chính chủ\n\n"
            "• THEMKHO <Mã SP> | <Tài khoản> | <Mật khẩu> [| SĐT] [| Cookie_F] [| Cookie_ST]\n"
            "  👉 VD: THEMKHO 1 | user01 | Pass123@ | 0912345678 | spc_f=xxx | spc_st=yyy\n\n"
            "💳 𝐐𝐔Ả𝐍 𝐋Ý 𝐕Í & 𝐍Ạ𝐏 𝐓𝐈Ề𝐍 𝐊𝐇Á𝐂𝐇 𝐇À𝐍𝐆:\n"
            "• NAPTIEN <Mã Đơn hoặc Zalo ID> <Số tiền> [| Lý do]\n"
            "  👉 Nạp tiền ví cho khách & tự động báo Zalo\n"
            "  👉 VD: NAPTIEN 708a33... 50000 | Khách chuyển khoản nạp ví\n"
            "  👉 VD: NAPTIEN DH100201 20000\n\n"
            "• HOANTIEN <Mã Đơn hoặc Zalo ID> [Số tiền] [| Lý do]\n"
            "  👉 Hoàn tiền lỗi đơn (Mặc định 10.000đ nếu bỏ trống số tiền)\n"
            "  👉 VD: HOANTIEN DH100201 10000 | Hoàn tiền lỗi sim\n\n"
            "• TRUTIEN <Mã Đơn hoặc Zalo ID> <Số tiền> [| Lý do]\n"
            "  👉 Khấu trừ tiền ví của khách\n\n"
            "• CHECKVI <Mã Đơn hoặc Zalo ID>\n"
            "  👉 Xem nhanh số dư ví & thông tin khách hàng\n\n"
            "📢 𝐓𝐇Ô𝐍𝐆 𝐁Á𝐎 𝐇À𝐍𝐆 𝐋𝐎Ạ𝐓:\n"
            "• TB <Nội dung> ➔ Gửi tới TẤT CẢ Nhóm Zalo\n"
            "• TBUSER <Nội dung> ➔ Gửi riêng tới từng Khách hàng cá nhân\n\n"
            "👥 𝐐𝐔Ả𝐍 𝐋Ý 𝐍𝐇Ó𝐌 𝐙𝐀𝐋𝐎:\n"
            "• GROUPS ➔ Xem danh sách nhóm bot đang tham gia\n"
            "• ADDGROUP <Mã Nhóm> [| Tên Nhóm] ➔ Thêm nhóm thủ công\n"
            "• DELGROUP <Mã Nhóm> ➔ Xóa nhóm nhận thông báo\n"
            "• TESTGROUP ➔ Bắn thử thông báo test vào tất cả nhóm\n"
            "───────────────────────\n"
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
            "📊 𝐁Á𝐎 𝐂Á𝐎 𝐊𝐈𝐍𝐇 𝐃𝐎𝐀𝐍𝐇 & 𝐓Ồ𝐍 𝐊𝐇𝐎 📊\n"
            "───────────────────────\n"
            f"💰 Doanh thu hôm nay: {int(today_revenue):,} VNĐ ({len(today_completed)} đơn thành công)\n"
            f"💎 Tổng doanh thu toàn sàn: {int(total_revenue):,} VNĐ\n"
            "───────────────────────\n"
            f"🛒 Tổng đơn hàng đã tạo: {total_orders} đơn\n"
            f"✅ Đơn hoàn thành: {len(completed_orders)} đơn\n"
            f"👥 Tổng khách hàng: {total_users} người\n"
            "───────────────────────\n"
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
            "───────────────────────"
        ]
        for p in products:
            p_lower = p.product_name.lower()
            is_auto = (p.id in [3, 5, 6, 7]) or ("drive" in p_lower) or ("thuê sim" in p_lower) or ("highlands" in p_lower) or ("otp" in p_lower)

            if is_auto:
                status_text = "🟢 Sẵn sàng 24/7 (Cấp tự động)"
                lines.append(f"• [{p.id}] {p.product_name} ➔ {int(p.price):,}đ")
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
                status_icon = "🟢" if avail > 0 else "🔴"
                lines.append(f"• [{p.id}] {p.product_name} ➔ {int(p.price):,}đ")
                lines.append(f"  └ {status_icon} Còn: {avail} acc | Đã bán: {sold} acc\n")

        lines.append("───────────────────────")
        lines.append("💡 Cú pháp nạp kho nhanh:")
        lines.append("THEMKHO <Mã SP> | <Nick> | <Pass>")
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
            "───────────────────────"
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

        lines.append("────────────────────────────")
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
    # 12. NẠP TIỀN / CỘNG TIỀN VÍ KHÁCH HÀNG (NAPTIEN / CONGTIEN / TOPUP)
    # =========================================================================
    if cmd in ["NAPTIEN", "CONGTIEN", "TOPUP"]:
        if len(parts) < 3:
            send_zalo_message(
                zalo_user_id,
                "⚠️ Cú pháp nạp tiền ví:\n"
                "• NAPTIEN <Zalo ID hoặc Mã Đơn> <Số tiền> [| Lý do]\n"
                "  👉 VD: NAPTIEN 708a33... 50000 | Khách chuyển khoản nạp ví\n"
                "  👉 VD: NAPTIEN DH100201 20000"
            )
            return True

        target_ref = parts[1].strip()
        try:
            amount = float(parts[2].replace(",", "").replace(".", ""))
        except ValueError:
            send_zalo_message(zalo_user_id, "❌ Số tiền nạp không hợp lệ! Phải là số, ví dụ: 50000")
            return True

        # Tách lý do nếu có
        reason = "Nạp tiền ví Zalo"
        if "|" in raw_text:
            reason = raw_text.split("|", 1)[1].strip() or reason
        elif len(parts) >= 4:
            reason = " ".join(parts[3:]).strip()

        target_user = None
        clean_code = target_ref.upper()
        order = db.query(Order).filter(Order.order_code == clean_code).first()
        if order and order.user:
            target_user = order.user
        else:
            target_user = db.query(User).filter(User.user_id == target_ref).first()

        if not target_user:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy khách hàng hoặc đơn hàng: '{target_ref}'")
            return True

        old_bal = float(target_user.balance or 0.0)
        target_user.balance = old_bal + amount
        db.commit()

        # Gửi tin nhắn Zalo chúc mừng nạp tiền thành công cho khách
        customer_msg = (
            f"💳 BẠN ĐÃ ĐƯỢC NẠP TIỀN VÀO VÍ THÀNH CÔNG! ✨🎉\n"
            f"───────────────────────\n"
            f"💵 Số tiền nạp: +{int(amount):,} VNĐ\n"
            f"📌 Nội dung: {reason}\n"
            f"💼 Số dư ví hiện tại: {int(target_user.balance):,} VNĐ\n"
            f"───────────────────────\n"
            f"⚡ TIỆN ÍCH DÀNH CHO BẠN:\n"
            f"• Bạn có thể dùng số dư này mua bất kỳ dịch vụ nào trên bot.\n"
            f"• Khi soạn lệnh mua (Ví dụ: 'BUY 6' hoặc số '1'), hệ thống sẽ TỰ ĐỘNG TRỪ VÍ và nhả số/acc tức thì!\n\n"
            f"👉 Soạn 'MENU' để xem danh sách dịch vụ sẵn hàng nhé! ✨"
        )
        send_zalo_message(target_user.user_id, customer_msg)

        send_zalo_message(
            zalo_user_id,
            f"✅ ĐÃ NẠP TIỀN VÍ THÀNH CÔNG!\n"
            f"───────────────────────\n"
            f"👤 Khách hàng: {target_user.display_name} ({target_user.user_id})\n"
            f"💰 Số tiền nạp: +{int(amount):,} VNĐ\n"
            f"💼 Số dư cũ: {int(old_bal):,}đ ➔ Số dư mới: {int(target_user.balance):,}đ\n"
            f"📩 Đã gửi tin nhắn thông báo tự động tới Zalo khách hàng!"
        )
        return True

    # =========================================================================
    # 13. HOÀN TIỀN LỖI ĐƠN HÀNG (HOANTIEN / REFUND)
    # =========================================================================
    if cmd in ["HOANTIEN", "REFUND"]:
        if len(parts) < 2:
            send_zalo_message(
                zalo_user_id,
                "⚠️ Cú pháp hoàn tiền:\n"
                "• HOANTIEN <Mã Đơn hoặc Zalo ID> [Số tiền] [| Lý do]\n"
                "  👉 VD: HOANTIEN DH100201 10000 | Hoàn tiền lỗi thuê sim\n"
                "  (Mặc định 10.000đ nếu không nhập số tiền)"
            )
            return True

        target_ref = parts[1].strip()
        amount = 10000.0
        if len(parts) >= 3 and not parts[2].startswith("|"):
            try:
                amount = float(parts[2].replace(",", "").replace(".", ""))
            except ValueError:
                amount = 10000.0

        reason = "Hoàn tiền dịch vụ Thuê SIM OTP"
        if "|" in raw_text:
            reason = raw_text.split("|", 1)[1].strip() or reason

        target_user = None
        clean_code = target_ref.upper()
        order = db.query(Order).filter(Order.order_code == clean_code).first()
        if order and order.user:
            target_user = order.user
        else:
            target_user = db.query(User).filter(User.user_id == target_ref).first()

        if not target_user:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy khách hàng hoặc đơn hàng: '{target_ref}'")
            return True

        old_bal = float(target_user.balance or 0.0)
        target_user.balance = old_bal + amount
        db.commit()

        customer_msg = (
            f"🎁 BẠN ĐÃ ĐƯỢC HOÀN TIỀN VÀO VÍ! 💰✨\n"
            f"───────────────────────\n"
            f"💵 Số tiền hoàn: +{int(amount):,} VNĐ\n"
            f"📌 Lý do: {reason}\n"
            f"💼 Số dư ví hiện tại: {int(target_user.balance):,} VNĐ\n"
            f"───────────────────────\n"
            f"👉 Bạn có thể soạn 'BUY 6' để thuê số Shopee ngay tức thì bằng số dư ví mà không cần quét mã QR nữa nhé! ✨"
        )
        send_zalo_message(target_user.user_id, customer_msg)

        send_zalo_message(
            zalo_user_id,
            f"✅ ĐÃ HOÀN TIỀN THÀNH CÔNG!\n"
            f"───────────────────────\n"
            f"👤 Khách hàng: {target_user.display_name} ({target_user.user_id})\n"
            f"💰 Số tiền hoàn: +{int(amount):,} VNĐ\n"
            f"💼 Số dư cũ: {int(old_bal):,}đ ➔ Số dư mới: {int(target_user.balance):,}đ\n"
            f"📩 Đã gửi tin nhắn thông báo tự động tới Zalo khách hàng!"
        )
        return True

    # =========================================================================
    # 14. KHẤU TRỪ TIỀN VÍ KHÁCH HÀNG (TRUTIEN)
    # =========================================================================
    if cmd in ["TRUTIEN", "DEDUCT"]:
        if len(parts) < 3:
            send_zalo_message(zalo_user_id, "⚠️ Cú pháp: TRUTIEN <Mã Đơn hoặc Zalo ID> <Số tiền> [| Lý do]")
            return True

        target_ref = parts[1].strip()
        try:
            amount = float(parts[2].replace(",", "").replace(".", ""))
        except ValueError:
            send_zalo_message(zalo_user_id, "❌ Số tiền trừ không hợp lệ!")
            return True

        reason = "Khấu trừ số dư ví"
        if "|" in raw_text:
            reason = raw_text.split("|", 1)[1].strip() or reason

        target_user = None
        clean_code = target_ref.upper()
        order = db.query(Order).filter(Order.order_code == clean_code).first()
        if order and order.user:
            target_user = order.user
        else:
            target_user = db.query(User).filter(User.user_id == target_ref).first()

        if not target_user:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy người dùng: '{target_ref}'")
            return True

        old_bal = float(target_user.balance or 0.0)
        target_user.balance = max(0.0, old_bal - amount)
        db.commit()

        send_zalo_message(
            zalo_user_id,
            f"✅ ĐÃ KHẤU TRỪ TIỀN VÍ THÀNH CÔNG!\n"
            f"───────────────────────\n"
            f"👤 Khách: {target_user.display_name} ({target_user.user_id})\n"
            f"💸 Số tiền trừ: -{int(amount):,} VNĐ\n"
            f"💼 Số dư cũ: {int(old_bal):,}đ ➔ Số dư mới: {int(target_user.balance):,}đ"
        )
        return True

    # =========================================================================
    # 15. TRA CỨU SỐ DƯ VÍ CỦA KHÁCH HÀNG (CHECKVI)
    # =========================================================================
    if cmd in ["CHECKVI", "VIUSER", "XEMVI"]:
        if len(parts) < 2:
            send_zalo_message(zalo_user_id, "⚠️ Cú pháp: CHECKVI <Mã Đơn hoặc Zalo ID>")
            return True

        target_ref = parts[1].strip()
        target_user = None
        clean_code = target_ref.upper()
        order = db.query(Order).filter(Order.order_code == clean_code).first()
        if order and order.user:
            target_user = order.user
        else:
            target_user = db.query(User).filter(User.user_id == target_ref).first()

        if not target_user:
            send_zalo_message(zalo_user_id, f"❌ Không tìm thấy thông tin khách hàng: '{target_ref}'")
            return True

        orders_cnt = db.query(Order).filter(Order.user_id == target_user.id).count()
        completed_cnt = db.query(Order).filter(Order.user_id == target_user.id, Order.status == "completed").count()

        send_zalo_message(
            zalo_user_id,
            f"💼 THÔNG TIN VÍ KHÁCH HÀNG:\n"
            f"───────────────────────\n"
            f"👤 Tên hiển thị: {target_user.display_name}\n"
            f"🆔 Zalo ID: {target_user.user_id}\n"
            f"💰 Số dư ví khả dụng: {int(target_user.balance or 0):,} VNĐ\n"
            f"🛒 Đơn đã mua: {completed_cnt}/{orders_cnt} đơn thành công\n"
            f"⏰ Tham gia: {target_user.created_at.strftime('%H:%M %d/%m/%Y')}"
        )
        return True

    return False


