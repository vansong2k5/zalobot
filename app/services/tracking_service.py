"""
Dịch vụ tra cứu mã vận đơn (Tracking Order) - Miễn phí 100%, 0 credit.
Kết nối trực tiếp API thời gian thực của SPX Express (Shopee Xpress).
Hỗ trợ:
1. Mã vận đơn SPX Express (SPXVN...)
2. Đơn hàng nội bộ shop (Mã DHxxxxxx)
"""

from datetime import datetime
from typing import Dict, Any
from sqlalchemy.orm import Session
from app.models import Order


def query_spx_tracking(tracking_number: str) -> Dict[str, Any]:
    """
    Tra cứu mã vận đơn qua API chính thức của SPX Express.
    Endpoint: https://spx.vn/shipment/order/open/order/get_order_info
    0 Credit, Public, cập nhật realtime.
    """
    clean_code = tracking_number.strip().upper()
    url = f"https://spx.vn/shipment/order/open/order/get_order_info?spx_tn={clean_code}&language_code=vi"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": f"https://spx.vn/track?{clean_code}",
        "Accept": "application/json, text/plain, */*"
    }

    try:
        try:
            import requests
            res = requests.get(url, headers=headers, timeout=8)
            if res.status_code != 200:
                return {"ok": False, "found": False, "error": f"Lỗi máy chủ SPX ({res.status_code})"}
            data = res.json()
        except ImportError:
            import httpx
            with httpx.Client(timeout=8) as client:
                res = client.get(url, headers=headers)
                if res.status_code != 200:
                    return {"ok": False, "found": False, "error": f"Lỗi máy chủ SPX ({res.status_code})"}
                data = res.json()

        payload = data.get("data") or {}
        sls_info = payload.get("sls_tracking_info") or {}
        records = sls_info.get("records") or []

        # Nếu không có lịch sử vận chuyển nào
        if not records:
            return {
                "ok": True,
                "found": False,
                "tracking_number": clean_code,
                "message": "Chưa có dữ liệu lộ trình cho mã vận đơn này."
            }

        # Trạng thái hiện tại là mốc mới nhất
        latest_event = records[0]
        current_status = latest_event.get("description", "").strip() or latest_event.get("buyer_description", "").strip() or "Đang vận chuyển"
        milestone = latest_event.get("milestone_name") or "In transit"

        # Lấy tối đa 5 mốc gần nhất
        events = []
        for r in records[:5]:
            ts = r.get("actual_time")
            if ts:
                try:
                    time_str = datetime.fromtimestamp(int(ts)).strftime("%H:%M:%S • %d/%m/%Y")
                except Exception:
                    time_str = str(ts)
            else:
                time_str = ""

            desc = r.get("description", "").strip() or r.get("buyer_description", "").strip()
            loc_curr = r.get("current_location", {}).get("location_name") if isinstance(r.get("current_location"), dict) else ""
            loc_next = r.get("next_location", {}).get("location_name") if isinstance(r.get("next_location"), dict) else ""

            loc_str = ""
            if loc_curr:
                loc_str = f" ({loc_curr})"
            elif loc_next:
                loc_str = f" (➔ {loc_next})"

            if desc:
                event_line = f"🕒 [{time_str}]\n👉 {desc}{loc_str}"
                events.append(event_line)

        return {
            "ok": True,
            "found": True,
            "tracking_number": clean_code,
            "sls_tn": sls_info.get("sls_tn") or clean_code,
            "status": current_status,
            "milestone": milestone,
            "events": events
        }
    except Exception as e:
        return {"ok": False, "found": False, "error": f"Lỗi kết nối tra cứu: {str(e)}"}


def format_tracking_result(db: Session, query_code: str) -> str:
    """
    Format kết quả tra cứu phong cách Gen Z trực quan, bắt mắt.
    """
    code = query_code.strip().upper()

    # 1. Tra cứu đơn hàng nội bộ của Shop nếu là mã DHxxxxxx
    if code.startswith("DH"):
        order = db.query(Order).filter(Order.order_code == code).first()
        if order:
            stt_map = {
                "pending": "⏳ Chờ thanh toán chuyển khoản",
                "completed": "🟢 Giao dịch hoàn tất (Đã bàn giao nick)",
                "cancelled": "🔴 Đã hủy",
                "expired": "⚫ Đã hết hạn"
            }
            stt_label = stt_map.get(order.status, order.status)
            created_time = order.created_at.strftime("%H:%M %d/%m/%Y") if order.created_at else ""
            lines = [
                f"📦 THÔNG TIN ĐƠN HÀNG: #{order.order_code} ⚡",
                "───────────────────────",
                f"💎 Dịch vụ: {order.product.product_name if order.product else 'Tài khoản'}",
                f"🔢 Số lượng: {order.quantity} tài khoản",
                f"💰 Tổng tiền: {int(order.price):,} VNĐ",
                f"📍 Trạng thái: {stt_label}",
                f"⏰ Thời gian tạo: {created_time}",
                "───────────────────────",
                "👉 Nhắn 'DONHANG' để xem chi tiết nick & lấy OTP!"
            ]
            return "\n".join(lines)

    # 2. Tra cứu mã vận đơn SPX Express
    spx_res = query_spx_tracking(code)
    if spx_res.get("found"):
        lines = [
            f"🚚 HÀNH TRÌNH VẬN ĐƠN SPX: {code} 📦",
            "───────────────────────",
            f"📍 Hiện tại: 🔥 {spx_res.get('status')}",
            "🏢 Đơn vị: SPX Express (Shopee Xpress)",
            "───────────────────────",
            "📋 CÁC MỐC HÀNH TRÌNH MỚI NHẤT:\n"
        ]
        events = spx_res.get("events", [])
        if events:
            lines.append("\n\n".join(events))
        else:
            lines.append("• Đang cập nhật thêm lộ trình...")

        lines.extend([
            "\n───────────────────────",
            "✨ Tra cứu tự động 24/7 (0 credit, hoàn toàn miễn phí)!"
        ])
        return "\n".join(lines)

    # 3. Không tìm thấy
    return (
        f"🔍 KẾT QUẢ TRA CỨU: {code}\n"
        "───────────────────────\n"
        f"⚠️ Chưa tìm thấy thông tin cho mã '{code}' trên hệ thống SPX Express.\n\n"
        "👉 Lưu ý:\n"
        "• Nếu đơn vừa tạo, bên vận chuyển có thể chưa kịp quét nhập bưu cục.\n"
        "• Hãy kiểm tra lại đúng mã vận đơn (Ví dụ dạng: SPXVN...).\n"
        "• Bạn thử tra cứu lại sau ít phút nhé! ❤️"
    )
