"""
Module Task Manager - Quản lý tiến trình độc quyền của người dùng Zalo:
1. Đảm bảo quy tắc: CHỈ CHO PHÉP 1 TIẾN TRÌNH / USER TẠI MỘT THỜI ĐIỂM.
2. Hỗ trợ lệnh STOP / HUY để dừng tiến trình đang chạy an toàn.
3. Cập nhật tiến độ giai đoạn theo thời gian thực.
"""

import threading
from datetime import datetime
from typing import Dict, Any, Tuple, Optional

_lock = threading.Lock()
_active_user_tasks: Dict[str, Dict[str, Any]] = {}


def is_user_busy(zalo_user_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Kiểm tra xem người dùng có đang chạy tiến trình nào không.
    Trả về (is_busy, task_info)
    """
    with _lock:
        task = _active_user_tasks.get(zalo_user_id)
        if task:
            return True, task
        return False, None


def start_user_task(zalo_user_id: str, task_name: str) -> bool:
    """
    Khởi tạo tiến trình mới cho người dùng.
    Trả về False nếu người dùng đã có tiến trình khác đang chạy.
    """
    with _lock:
        if zalo_user_id in _active_user_tasks:
            return False
        _active_user_tasks[zalo_user_id] = {
            "task_name": task_name,
            "started_at": datetime.now(),
            "current_step": "Khởi động...",
            "stop_requested": False
        }
        return True


def update_user_task_progress(zalo_user_id: str, step_desc: str):
    """Cập nhật giai đoạn đang chạy của tiến trình."""
    with _lock:
        if zalo_user_id in _active_user_tasks:
            _active_user_tasks[zalo_user_id]["current_step"] = step_desc


def request_stop_user_task(zalo_user_id: str) -> bool:
    """Yêu cầu dừng tiến trình đang chạy của người dùng."""
    with _lock:
        if zalo_user_id in _active_user_tasks:
            _active_user_tasks[zalo_user_id]["stop_requested"] = True
            return True
        return False


def is_stop_requested(zalo_user_id: str) -> bool:
    """Worker kiểm tra xem người dùng đã bấm STOP/HUY hay chưa."""
    with _lock:
        task = _active_user_tasks.get(zalo_user_id)
        if task:
            return task.get("stop_requested", False)
        return False


def finish_user_task(zalo_user_id: str):
    """Giải phóng tiến trình khi hoàn thành hoặc bị dừng."""
    with _lock:
        if zalo_user_id in _active_user_tasks:
            del _active_user_tasks[zalo_user_id]
        _otp_events.pop(zalo_user_id, None)
        _otp_data.pop(zalo_user_id, None)


# =========================================================================
# QUẢN LÝ NHẬN MÃ OTP TỪ NGƯỜI DÙNG KHI REG BẰNG SĐT RIÊNG
# =========================================================================
_otp_events: Dict[str, threading.Event] = {}
_otp_data: Dict[str, Optional[str]] = {}


def request_user_otp(zalo_user_id: str):
    """Khởi tạo trạng thái chờ người dùng nhập mã OTP."""
    with _lock:
        ev = threading.Event()
        _otp_events[zalo_user_id] = ev
        _otp_data[zalo_user_id] = None


def submit_user_otp(zalo_user_id: str, otp_code: str) -> bool:
    """Khi người dùng gửi tin nhắn chứa mã OTP qua Zalo."""
    with _lock:
        if zalo_user_id in _otp_events:
            _otp_data[zalo_user_id] = otp_code.strip()
            _otp_events[zalo_user_id].set()
            return True
        return False


def wait_for_user_otp(zalo_user_id: str, timeout: int = 90) -> Optional[str]:
    """Worker đợi người dùng gửi mã OTP trong tối đa timeout giây."""
    ev = None
    with _lock:
        ev = _otp_events.get(zalo_user_id)
    if not ev:
        return None

    signaled = ev.wait(timeout=timeout)
    with _lock:
        res = _otp_data.get(zalo_user_id)
        _otp_events.pop(zalo_user_id, None)
        _otp_data.pop(zalo_user_id, None)
        return res if signaled else None


def is_waiting_for_otp(zalo_user_id: str) -> bool:
    """Kiểm tra xem người dùng có đang ở bước cần nhập mã OTP hay không."""
    with _lock:
        return zalo_user_id in _otp_events
