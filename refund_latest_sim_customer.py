"""
Script hoàn 10.000 VNĐ vào ví cho khách hàng vừa thuê số / chuyển khoản gần nhất.
Chạy trực tiếp trên VPS:
    python refund_latest_sim_customer.py
"""

import sys
import os

# Thêm thư mục gốc vào path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.db import SessionLocal
from app.models import Order, User, Product
from app.services.zalo_service import send_zalo_message


def main():
    print("==================================================")
    print("   CÔNG CỤ HOÀN TIỀN VÍ CHO KHÁCH HÀNG THUÊ SỐ   ")
    print("==================================================")

    db = SessionLocal()
    try:
        # Lấy 10 đơn hàng mới nhất
        recent_orders = db.query(Order).order_by(Order.id.desc()).limit(10).all()

        if not recent_orders:
            print("❌ Chưa có đơn hàng nào trong hệ thống CSDL.")
            return

        print("\nDanh sách 10 đơn hàng gần nhất:")
        print(f"{'STT':<4} | {'Mã Đơn':<10} | {'Sản Phẩm':<25} | {'Giá':<10} | {'Trạng thái':<10} | {'Khách hàng':<20}")
        print("-" * 90)

        target_order = None
        for idx, o in enumerate(recent_orders, 1):
            p_name = o.product.product_name if o.product else "N/A"
            u_name = o.user.display_name if o.user else "N/A"
            u_zalo = o.user.user_id if o.user else "N/A"
            print(f"{idx:<4} | {o.order_code:<10} | {p_name[:24]:<25} | {int(o.price):<10} | {o.status:<10} | {u_name} ({u_zalo})")

            # Tự động chọn đơn thuê sim / thuê số gần nhất nếu chưa chọn
            if target_order is None:
                p_lower = p_name.lower()
                if o.product_id == 6 or any(k in p_lower for k in ["thuê sim", "thue sim", "thuê số", "thue so", "otp", "sim"]):
                    target_order = o

        # Nếu không có đơn tên thuê số, lấy luôn đơn số 1 (đơn mới nhất)
        if target_order is None and recent_orders:
            target_order = recent_orders[0]

        print("-" * 90)
        user = target_order.user
        if not user:
            print(f"❌ Đơn #{target_order.order_code} không có thông tin khách hàng.")
            return

        print(f"\n👉 ĐÃ XÁC ĐỊNH KHÁCH HÀNG CẦN HOÀN TIỀN:")
        print(f"   • Đơn hàng: #{target_order.order_code} ({target_order.product.product_name if target_order.product else ''})")
        print(f"   • Khách hàng: {user.display_name} (Zalo ID: {user.user_id})")
        old_balance = float(user.balance or 0.0)
        print(f"   • Số dư ví hiện tại: {int(old_balance):,} VNĐ")

        REFUND_AMOUNT = 10000.0
        new_balance = old_balance + REFUND_AMOUNT

        # Cập nhật số dư vào DB
        user.balance = new_balance
        db.commit()
        print(f"\n✅ ĐÃ CỘNG +{int(REFUND_AMOUNT):,} VNĐ VÀO VÍ CỦA KHÁCH HÀNG!")
        print(f"   • Số dư ví mới: {int(new_balance):,} VNĐ")

        # Bắn tin nhắn Zalo cho khách hàng
        notify_msg = (
            f"🎁 BẠN ĐÃ ĐƯỢC HOÀN 10,000 VNĐ VÀO VÍ! 🎉\n"
            f"───────────────────────\n"
            f"💵 Số tiền hoàn: +10,000 VNĐ\n"
            f"📌 Lý do: Hoàn tiền hỗ trợ giao dịch thuê số OTP [Đơn #{target_order.order_code}].\n"
            f"💼 Số dư ví hiện tại của bạn: {int(new_balance):,} VNĐ\n"
            f"───────────────────────\n"
            f"⚡ TIỆN ÍCH DÀNH CHO BẠN:\n"
            f"• Bạn có thể soạn 'BUY 6' để thuê số mới ngay lập tức bằng số dư ví mà không cần chuyển khoản nữa nhé! ✨\n"
            f"• Soạn 'SODU' để kiểm tra số dư ví bất kỳ lúc nào."
        )

        sent = send_zalo_message(user.user_id, notify_msg)
        if sent:
            print(f"✅ Đã gửi tin nhắn Zalo thông báo thành công tới khách hàng ({user.user_id})!")
        else:
            print(f"⚠️ Gửi tin nhắn Zalo thất bại (có thể user chưa từng chat với Bot hoặc API Zalo đang bận).")

    except Exception as e:
        print(f"❌ Có lỗi xảy ra: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
