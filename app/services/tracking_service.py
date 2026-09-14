"""
Dịch vụ tra cứu mã vận đơn (Tracking Order) - Miễn phí 100%, 0 credit.
Kết nối trực tiếp API thời gian thực của SPX Express (Shopee Xpress).
Khai thác toàn diện lộ trình 3 đầu & Lịch sử hành trình chuẩn xác.
"""

import re
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
from sqlalchemy.orm import Session
from app.models import Order


def clean_loc_address(addr: str) -> str:
    """Rút gọn địa chỉ bưu cục cho vừa vặn màn hình Zalo, tránh tràn dòng."""
    if not addr:
        return ""
    cleaned = re.sub(r"\s+", " ", addr).strip()
    if cleaned.startswith("VN "):
        cleaned = cleaned[3:].strip()
    return cleaned


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

        if not records:
            return {
                "ok": True,
                "found": False,
                "tracking_number": clean_code,
                "message": "Chưa có dữ liệu lộ trình cho mã vận đơn này."
            }

        return {
            "ok": True,
            "found": True,
            "tracking_number": clean_code,
            "sls_tn": sls_info.get("sls_tn") or "Chưa cập nhật",
            "records": records
        }
    except Exception as e:
        return {"ok": False, "found": False, "error": f"Lỗi kết nối tra cứu: {str(e)}"}


def extract_3_route_points(records: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Trích xuất chính xác 3 đầu vận chuyển của bưu kiện SPX:
    1. first_mile: Kho gửi / nơi lấy hàng đầu tiên
    2. transit_hub: Kho trung chuyển SOC
    3. last_mile: Trạm giao hàng / bưu cục đích
    """
    first_mile = None
    transit_hub = None
    last_mile = None

    # 1. Tìm đầu gửi (First Mile): quét từ sự kiện cũ nhất đến mới
    for r in reversed(records):
        cur_loc = r.get("current_location") or {}
        l_name = cur_loc.get("location_name")
        t_code = r.get("tracking_code", "")
        if l_name and ("Hub" in l_name or t_code.startswith("F4") or t_code == "F100"):
            first_mile = l_name
            break
    if not first_mile:
        for r in reversed(records):
            l_name = (r.get("current_location") or {}).get("location_name")
            if l_name:
                first_mile = l_name
                break

    # 2. Tìm trạm đích (Last Mile Hub)
    for r in records:
        t_code = r.get("tracking_code", "")
        l_name = (r.get("current_location") or {}).get("location_name")
        if t_code in ["F599", "F600", "F650", "F980"] and l_name:
            last_mile = l_name
            break
    if not last_mile:
        for r in records:
            n_name = (r.get("next_location") or {}).get("location_name")
            if n_name and ("Hub" in n_name or "Trạm" in n_name) and "SOC" not in n_name:
                last_mile = n_name
                break
    if not last_mile:
        for r in records:
            n_name = (r.get("next_location") or {}).get("location_name")
            if n_name and n_name != first_mile:
                last_mile = n_name
                break

    # 3. Tìm kho trung chuyển (Transit SOC)
    for r in records:
        l_name = (r.get("current_location") or {}).get("location_name")
        if l_name and "SOC" in l_name:
            transit_hub = l_name
            break

    return first_mile, transit_hub, last_mile


def format_curated_history(records: List[Dict[str, Any]], max_events: int = 5) -> str:
    """
    Format lịch sử giao hàng chọn lọc:
    - Loại bỏ mốc trùng lặp
    - Dùng đường kẻ '─ ─ ─ ─ ─ ─ ─ ─ ─ ─' (tuyệt đối không dùng '----')
    - Gọn gàng, hiển thị đầy đủ trên màn hình Zalo
    """
    important_events = []
    seen = set()

    for r in records:
        c = r.get("tracking_code", "")
        if c not in seen or c in ["F980", "F668"]:
            seen.add(c)
            important_events.append(r)
        if len(important_events) >= max_events:
            break

    events_sorted = list(reversed(important_events))
    h_blocks = []
    for ev in events_sorted:
        t = ev.get("actual_time")
        t_s = datetime.fromtimestamp(t).strftime("%Hh:%M %d/%m/%Y") if t else ""
        d_s = (ev.get("description") or ev.get("buyer_description") or "").strip().upper()
        icon = "📦" if any(k in d_s for k in ["CHUẨN BỊ", "LẤY HÀNG", "KHỞI TẠO"]) else "🚚"
        if any(k in d_s for k in ["THÀNH CÔNG", "GIAO HÀNG THÀNH CÔNG"]):
            icon = "✅"
        elif any(k in d_s for k in ["HOÀN TRẢ", "HỦY", "KHÔNG THÀNH CÔNG"]):
            icon = "⛔"

        c_l = ev.get("current_location") or {}
        n_l = ev.get("next_location") or {}

        b_lines = [f"⏰ {t_s}", f"{icon} {d_s}"]
        if c_l.get("location_name"):
            b_lines.append(f"📍 {c_l.get('location_name')}: {clean_loc_address(c_l.get('full_address'))}")
        if n_l.get("location_name") and n_l.get("location_name") != c_l.get("location_name"):
            b_lines.append(f"➡️ TIẾP THEO: {n_l.get('location_name')}: {clean_loc_address(n_l.get('full_address'))}")

        h_blocks.append("\n".join(b_lines))

    return "\n\n".join(h_blocks)


def format_tracking_result(db: Session, query_code: str) -> str:
    """
    Định dạng kết quả tra cứu hoàn chỉnh theo đúng chuẩn SPX Express.
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
                f"MÃ ĐƠN HÀNG: #{order.order_code}",
                f"DỊCH VỤ: {order.product.name if hasattr(order, 'product') and order.product else 'Tài khoản'}",
                "━━━━━━━━━━━━━━━━━━━━",
                "📌 TRẠNG THÁI MỚI NHẤT:",
                f"⏰ {created_time}",
                f"🚚 {stt_label.upper()}",
                f"💰 Tổng tiền: {int(order.price):,} VNĐ | SL: {order.quantity}",
                "━━━━━━━━━━━━━━━━━━━━",
                "👉 Nhắn 'DONHANG' để xem chi tiết tài khoản!"
            ]
            return "\n".join(lines)

    # 2. Tra cứu mã vận đơn SPX Express
    spx_res = query_spx_tracking(code)
    if not spx_res.get("found"):
        return (
            f"🔍 KẾT QUẢ TRA CỨU: {code}\n\n"
            f"⚠️ Chưa tìm thấy thông tin cho mã '{code}' trên hệ thống SPX Express.\n\n"
            "👉 Lưu ý:\n"
            "• Nếu đơn vừa tạo, bên vận chuyển có thể chưa kịp quét nhập bưu cục.\n"
            "• Hãy kiểm tra lại đúng mã vận đơn (Ví dụ dạng: SPXVN...).\n"
            "• Bạn thử tra cứu lại sau ít phút nhé! ❤️"
        )

    records: List[Dict[str, Any]] = spx_res.get("records", [])
    sls_tn = spx_res.get("sls_tn", "Chưa cập nhật")

    # 1. Trích xuất thông tin 3 đầu
    first_mile, transit_hub, last_mile = extract_3_route_points(records)

    # 2. Phân loại trạng thái chính xác
    latest = records[0]
    lat_code = latest.get("tracking_code", "")
    lat_mile = (latest.get("milestone_name") or "").lower()
    lat_desc = (latest.get("description") or "").lower()

    # Kiểm tra xem có mốc hoàn trả/hủy không
    has_return = False
    return_event = None
    for rec in records:
        c = rec.get("tracking_code", "")
        m = (rec.get("milestone_name") or "").lower()
        d = (rec.get("description") or "").lower()
        if c in ["F668", "F585", "F671", "F677"] or "unsuccessful" in m or "hoàn trả" in d:
            has_return = True
            if not return_event and ("hoàn trả" in d or c in ["F668", "F585"]):
                return_event = rec

    # Xác định banner & mốc hiển thị trọng tâm
    if lat_code in ["F980", "F700"] or "delivered" in lat_mile or "giao hàng thành công" in lat_desc:
        banner = "✅ ĐƠN HÀNG ĐÃ GIAO THÀNH CÔNG!"
        target_event = latest
    elif has_return and lat_code not in ["F980", "F700"]:
        banner = "⛔ ĐƠN HÀNG ĐÃ BỊ HỦY/HOÀN TRẢ!"
        target_event = return_event if return_event else latest
    elif lat_code in ["F600", "F598"] or "out for delivery" in lat_mile:
        banner = "🛵 SHIPPER ĐANG ĐI GIAO HÀNG!"
        target_event = latest
    else:
        banner = "🚚 ĐƠN HÀNG ĐANG TRUNG CHUYỂN BÌNH THƯỜNG!"
        target_event = latest

    # Format thời gian & mô tả mốc
    ts = target_event.get("actual_time")
    time_str = datetime.fromtimestamp(ts).strftime("%Hh:%M %d/%m/%Y") if ts else ""
    event_desc = (target_event.get("description") or target_event.get("buyer_description") or "").strip().upper()

    # Vị trí bưu cục mốc hiển thị
    loc_obj = target_event.get("current_location") or {}
    loc_name = loc_obj.get("location_name") or ""
    loc_addr = clean_loc_address(loc_obj.get("full_address"))
    loc_line = ""
    if loc_name and loc_addr:
        loc_line = f"📌 {loc_name}: {loc_addr}"
    elif loc_name:
        loc_line = f"📌 {loc_name}"
    elif loc_addr:
        loc_line = f"📌 {loc_addr}"

    lines = [
        f"MÃ VẬN ĐƠN: {code}",
        f"MÃ TRACKING NỘI BỘ: {sls_tn}",
        "━━━━━━━━━━━━━━━━━━━━",
        "📍 LỘ TRÌNH 3 ĐẦU VẬN CHUYỂN:"
    ]

    # Hiển thị 3 đầu: Đầu gửi -> Trung chuyển -> Đầu nhận
    if first_mile:
        lines.append(f"📦 ĐẦU GỬI: {first_mile}")
    if transit_hub:
        lines.append(f"🏢 TRUNG CHUYỂN: {transit_hub}")
    if last_mile:
        lines.append(f"🎯 ĐẦU NHẬN: {last_mile}")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        "📌 TRẠNG THÁI MỚI NHẤT:",
        f"⏰ {time_str}",
        f"🚚 {event_desc}"
    ])
    if loc_line:
        lines.append(loc_line)

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(banner)

    # 3. Lịch sử giao hàng chọn lọc (max 5 mốc, phân cách ─ ─ ─ ─ ─ ─ ─ ─ ─ ─)
    history_body = format_curated_history(records, max_events=5)
    if history_body:
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━━",
            "📜 LỊCH SỬ GIAO HÀNG:",
            history_body
        ])

    return "\n".join(lines)

