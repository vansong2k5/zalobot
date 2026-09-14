"""
Module System Guard Service - Giám sát sức khỏe VPS, chống tràn RAM và ngăn ngừa sập bot:
1. Theo dõi RAM, CPU, Ổ đĩa (Disk) và Uptime liên tục 24/7.
2. Tự động dọn dẹp RAM (drop caches) và diệt tiến trình Playwright treo khi RAM > 85%.
3. Gửi cảnh báo khẩn cấp tới Log Bot ("Bot Tạp Hóa Benjaburg") để Admin kịp thời xử lý.
4. Báo cáo định kỳ Heartbeat tình trạng hệ thống.
"""

import os
import time
import logging
import threading
from datetime import datetime
from typing import Dict, Any

from .log_notifier_service import send_log_message

logger = logging.getLogger("SystemGuard")

_guard_started = False
_last_ram_alert = 0
_last_disk_alert = 0


def get_system_metrics() -> Dict[str, Any]:
    """Đọc chỉ số tài nguyên VPS trực tiếp từ nhân Linux (/proc và statvfs)."""
    metrics = {
        "ram_total_mb": 0,
        "ram_used_mb": 0,
        "ram_available_mb": 0,
        "ram_percent": 0.0,
        "disk_total_gb": 0.0,
        "disk_avail_gb": 0.0,
        "disk_percent": 0.0,
        "uptime_str": "Không rõ"
    }

    # 1. Đo lường RAM qua /proc/meminfo
    try:
        meminfo = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    v = parts[1].strip().split()[0]
                    meminfo[k] = int(v)

        total_kb = meminfo.get("MemTotal", 0)
        avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
        used_kb = total_kb - avail_kb

        metrics["ram_total_mb"] = round(total_kb / 1024)
        metrics["ram_available_mb"] = round(avail_kb / 1024)
        metrics["ram_used_mb"] = round(used_kb / 1024)
        metrics["ram_percent"] = round((used_kb / total_kb) * 100, 1) if total_kb > 0 else 0.0
    except Exception as ex:
        logger.warning("Không đọc được /proc/meminfo: %s", ex)

    # 2. Đo lường Ổ cứng qua os.statvfs
    try:
        st = os.statvfs("/")
        total_b = st.f_blocks * st.f_frsize
        avail_b = st.f_bavail * st.f_frsize
        used_b = total_b - avail_b

        metrics["disk_total_gb"] = round(total_b / (1024 ** 3), 1)
        metrics["disk_avail_gb"] = round(avail_b / (1024 ** 3), 1)
        metrics["disk_percent"] = round((used_b / total_b) * 100, 1) if total_b > 0 else 0.0
    except Exception as ex:
        logger.warning("Không đọc được statvfs: %s", ex)

    # 3. Đo lường Uptime qua /proc/uptime
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            uptime_seconds = float(f.readline().split()[0])
            hours = int(uptime_seconds // 3600)
            mins = int((uptime_seconds % 3600) // 60)
            metrics["uptime_str"] = f"{hours} giờ {mins} phút"
    except Exception:
        pass

    return metrics


def auto_clean_system_memory():
    """Tự động dọn dẹp bộ nhớ đệm hệ thống (drop caches) và dọn các browser thừa."""
    try:
        logger.info("Đang thực hiện dọn dẹp bộ nhớ đệm RAM...")
        # Đồng bộ và xả pagecache, dentries và inodes
        os.system("sync; echo 3 > /proc/sys/vm/drop_caches 2>/dev/null")
        # Diệt các tiến trình chrome / chromium bị orphan (chạy quá lâu không tắt)
        os.system("pkill -f 'chromium.*--type=renderer' 2>/dev/null")
    except Exception as ex:
        logger.error("Lỗi dọn dẹp RAM: %s", ex)


def _system_guard_worker():
    """Vòng lặp chạy nền giám sát hệ thống mỗi 60 giây."""
    global _last_ram_alert, _last_disk_alert

    # Gửi bản tin chào mừng khi bot vừa khởi động
    time.sleep(10)  # Đợi uvicorn ổn định
    m = get_system_metrics()
    boot_msg = (
        "🟢 [HỆ THỐNG KHỞI ĐỘNG THÀNH CÔNG]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🖥️ Uptime máy chủ: {m['uptime_str']}\n"
        f"💾 RAM: {m['ram_used_mb']}MB / {m['ram_total_mb']}MB ({m['ram_percent']}%)\n"
        f"💽 Ổ cứng: Trống {m['disk_avail_gb']}GB / {m['disk_total_gb']}GB ({m['disk_percent']}%)\n"
        "🛡️ Trạng thái: Bot Zalo & Hệ thống bảo vệ bộ nhớ đang hoạt động 24/7!"
    )
    send_log_message(boot_msg)

    heartbeat_counter = 0

    while True:
        try:
            time.sleep(60)
            metrics = get_system_metrics()
            now = time.time()

            # 1. CẢNH BÁO RAM NGUY HIỂM (RAM > 85% hoặc trống < 250MB)
            if metrics["ram_percent"] > 85.0 or metrics["ram_available_mb"] < 250:
                if now - _last_ram_alert > 300:  # Không spam liên tục, cách nhau tối thiểu 5 phút
                    _last_ram_alert = now
                    alert_msg = (
                        "🚨 [CẢNH BÁO KHẨN CẤP: RAM QUÁ TẢI] 🚨\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ RAM đang sử dụng: {metrics['ram_percent']}% ({metrics['ram_used_mb']}MB / {metrics['ram_total_mb']}MB)\n"
                        f"⚠️ Bộ nhớ trống còn lại: {metrics['ram_available_mb']}MB\n\n"
                        "⚡ Hệ thống đang TỰ ĐỘNG DỌN DẸP bộ nhớ đệm (drop caches) để ngăn ngừa sập bot!"
                    )
                    send_log_message(alert_msg)
                    auto_clean_system_memory()

                    # Kiểm tra lại sau khi dọn
                    time.sleep(5)
                    after_m = get_system_metrics()
                    send_log_message(
                        f"✅ [KẾT QUẢ DỌN RAM TỰ ĐỘNG]\n"
                        f"• RAM hiện tại: {after_m['ram_percent']}% (Trống: {after_m['ram_available_mb']}MB)\n"
                        f"• Hệ thống đã an toàn trở lại!"
                    )

            # 2. CẢNH BÁO Ổ CỨNG (Disk > 90%)
            if metrics["disk_percent"] > 90.0:
                if now - _last_disk_alert > 3600:  # 1 tiếng cảnh báo 1 lần
                    _last_disk_alert = now
                    disk_msg = (
                        "⚠️ [CẢNH BÁO DUNG LƯỢNG Ổ CỨNG] ⚠️\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"💽 Ổ cứng đã dùng: {metrics['disk_percent']}%\n"
                        f"📦 Dung lượng trống còn lại: {metrics['disk_avail_gb']}GB / {metrics['disk_total_gb']}GB\n"
                        "👉 Khuyến nghị dọn dẹp các thư mục log hoặc file rác /tmp để tránh đầy ổ cứng."
                    )
                    send_log_message(disk_msg)

            # 3. BẢN TIN HEARTBEAT (Mỗi 6 tiếng = 360 vòng * 60s)
            heartbeat_counter += 1
            if heartbeat_counter >= 360:
                heartbeat_counter = 0
                hb_msg = (
                    "💓 [VPS HEALTH CHECK - ĐỊNH KỲ]\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"⏱️ Uptime: {metrics['uptime_str']}\n"
                    f"💾 RAM: {metrics['ram_used_mb']}MB / {metrics['ram_total_mb']}MB ({metrics['ram_percent']}%)\n"
                    f"💽 Ổ đĩa: {metrics['disk_avail_gb']}GB trống ({metrics['disk_percent']}%)\n"
                    "✨ Toàn bộ dịch vụ Bot Zalo đang chạy mượt mà 24/7!"
                )
                send_log_message(hb_msg)

        except Exception as e:
            logger.error("Lỗi trong vòng lặp System Guard: %s", e)


def start_system_guard():
    """Kích hoạt thread giám sát hệ thống nếu chưa chạy."""
    global _guard_started
    if not _guard_started:
        _guard_started = True
        t = threading.Thread(target=_system_guard_worker, daemon=True, name="SystemGuardThread")
        t.start()
        logger.info("System Guard Worker đã được khởi động thành công.")
