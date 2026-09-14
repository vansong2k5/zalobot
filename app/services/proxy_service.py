"""
Dịch vụ Quản lý Proxy toàn hệ thống.
- Chuẩn hóa, xác thực và test kết nối proxy.
- Quản lý danh sách proxy Admin (lưu file JSON).
- Fallback tự động về Proxy VPS tự host nếu không có proxy Admin.
"""

import os
import re
import json
import time
import logging
from typing import List, Dict, Optional, Tuple, Any

import httpx
import requests

from app.config import DEFAULT_PROXY, PROXY_LIST

logger = logging.getLogger("ProxyService")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
PROXIES_FILE = os.path.join(DATA_DIR, "admin_proxies.json")


def _ensure_data_file():
    """Đảm bảo thư mục data và file lưu proxy tồn tại."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(PROXIES_FILE):
        with open(PROXIES_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


# ==============================================================================
# 1. CHUẨN HÓA PROXY URL
# ==============================================================================

def normalize_proxy_url(proxy_str: str) -> Optional[str]:
    """
    Chuẩn hóa chuỗi proxy nhập vào thành chuẩn URL:
    - ip:port               -> http://ip:port
    - ip:port:user:pass     -> http://user:pass@ip:port
    - user:pass@ip:port     -> http://user:pass@ip:port
    - http://...            -> giữ nguyên
    - socks5://...          -> giữ nguyên
    """
    if not proxy_str:
        return None
    raw = proxy_str.strip()
    if not raw:
        return None

    if raw.startswith(("http://", "https://", "socks5://")):
        return raw

    # Dạng IP:PORT:USER:PASS (regex chặt hơn)
    match_4 = re.match(r"^([a-zA-Z0-9.\-]+):(\d+):([^:@]+):([^:@]+)$", raw)
    if match_4:
        host, port, user, pwd = match_4.groups()
        return f"http://{user}:{pwd}@{host}:{port}"

    # Dạng user:pass@ip:port
    match_up = re.match(r"^([^:@]+):([^:@]+)@([a-zA-Z0-9.\-]+):(\d+)$", raw)
    if match_up:
        return f"http://{raw}"

    # Dạng thông thường ip:port
    if re.match(r"^[a-zA-Z0-9.\-]+:\d+$", raw):
        return f"http://{raw}"

    return None


# ==============================================================================
# 2. KIỂM TRA KẾT NỐI PROXY
# ==============================================================================

def test_proxy_alive(proxy_url: str, timeout: float = 5.0) -> Tuple[bool, str]:
    """Kiểm tra proxy có kết nối Internet được không. Trả về (ok, external_ip_or_error)."""
    p_norm = normalize_proxy_url(proxy_url)
    if not p_norm:
        return False, "URL proxy không hợp lệ"
    try:
        with httpx.Client(proxy=p_norm, timeout=timeout) as client:
            resp = client.get("https://api.ipify.org?format=json")
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    ip = data.get("ip") if isinstance(data, dict) else None
                    return True, ip or "OK"
                except Exception:
                    return False, "Proxy trả về phản hồi không hợp lệ"
            return False, f"HTTP {resp.status_code}"
    except httpx.ProxyError as pe:
        return False, f"Proxy từ chối kết nối / sai xác thực ({str(pe)[:40]})"
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException):
        return False, "Phản hồi quá chậm (Timeout)"
    except httpx.ConnectError:
        return False, "Hết hạn / Mất kết nối (Connection Refused)"
    except Exception as ex:
        err_str = str(ex)
        if "Connection refused" in err_str or "111" in err_str:
            return False, "Hết hạn / Mất kết nối (Connection Refused)"
        elif "timeout" in err_str.lower():
            return False, "Phản hồi quá chậm (Timeout)"
        return False, f"Lỗi ({err_str[:50]})"


def validate_proxy_connection(
    proxy_url: str, timeout: int = 8
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Kiểm tra proxy đầy đủ 2 bước:
    1. Check IP ra Internet qua ipify.org
    2. Test kết nối trực tiếp đến Shopee Gateway (WAF check)
    Trả về (is_valid, message, info_dict)
    """
    normalized = normalize_proxy_url(proxy_url)
    if not normalized:
        return False, "Định dạng Proxy không hợp lệ! Nhập dạng 'ip:port' hoặc 'user:pass@ip:port'", None

    proxies = {"http": normalized, "https": normalized}
    t0 = time.time()
    external_ip = None

    try:
        r1 = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=timeout)
        if r1.status_code == 200:
            external_ip = r1.json().get("ip")
        latency_ms = int((time.time() - t0) * 1000)
    except Exception as e:
        err_str = str(e)
        if "Connection refused" in err_str or "111" in err_str:
            return False, "Proxy đã hết hạn hoặc mất kết nối (Connection Refused)", None
        elif "timed out" in err_str.lower() or "timeout" in err_str.lower():
            return False, "Proxy phản hồi quá chậm (Timeout > 8s)", None
        elif "proxy authentication" in err_str.lower() or "407" in err_str:
            return False, "Sai tài khoản/mật khẩu Proxy (407 Auth Required)", None
        return False, f"Lỗi kết nối Proxy ({err_str[:60]}...)", None

    if not external_ip:
        return False, "Proxy không phản hồi dữ liệu IP ra Internet.", None

    # Bước 2: Test kết nối tới Shopee
    try:
        shopee_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        r2 = requests.get(
            "https://shopee.vn/api/v4/client/healthz",
            headers=shopee_headers, proxies=proxies, timeout=timeout
        )
        if r2.status_code in [403, 429]:
            return False, (
                f"Proxy (IP: {external_ip}) đã bị Shopee chặn WAF ({r2.status_code}). "
                "Hãy dùng proxy sạch hơn!"
            ), None
    except Exception as e:
        err_str = str(e)
        if "timed out" in err_str.lower() or "timeout" in err_str.lower():
            return False, "Proxy bị Shopee chặn hoặc timeout kết nối tới Shopee.", None
        return False, f"Không truy cập được Shopee qua Proxy ({err_str[:50]}...)", None

    return True, f"Proxy hoạt động tốt! IP: {external_ip} | Latency: {latency_ms}ms", {
        "proxy_url": normalized,
        "ip": external_ip,
        "latency_ms": latency_ms
    }


# ==============================================================================
# 3. QUẢN LÝ PROXY ADMIN (FILE-BASED)
# ==============================================================================

def load_admin_proxies() -> List[str]:
    """Đọc danh sách proxy admin đã lưu."""
    _ensure_data_file()
    try:
        with open(PROXIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                # Filter None: normalize_proxy_url có thể trả về None nếu input rỗng/sai
                return [n for p in data if p for n in [normalize_proxy_url(p)] if n]
    except Exception as ex:
        logger.error("Lỗi đọc file admin_proxies.json: %s", ex)
    return []


def save_admin_proxies(proxies: List[str]):
    """Ghi danh sách proxy admin vào file."""
    _ensure_data_file()
    try:
        with open(PROXIES_FILE, "w", encoding="utf-8") as f:
            json.dump(proxies, f, ensure_ascii=False, indent=2)
    except Exception as ex:
        logger.error("Lỗi ghi file admin_proxies.json: %s", ex)


def get_active_proxy() -> str:
    """
    Lấy proxy đang hoạt động theo thứ tự ưu tiên:
    1. Proxy Admin cấu hình thêm
    2. PROXY_LIST trong .env
    3. Fallback về Proxy VPS tự host
    """
    admin_proxies = load_admin_proxies()
    if admin_proxies:
        return admin_proxies[0]
    if PROXY_LIST:
        return PROXY_LIST[0]
    return DEFAULT_PROXY or "http://163.61.183.185:38888"


def add_admin_proxy(proxy_str: str) -> Tuple[bool, str]:
    """Thêm proxy mới và đặt làm proxy ưu tiên số 1."""
    p_norm = normalize_proxy_url(proxy_str)
    if not p_norm:
        return False, "Chuỗi proxy không hợp lệ"
    proxies = load_admin_proxies()
    if p_norm in proxies:
        proxies.remove(p_norm)
    proxies.insert(0, p_norm)
    save_admin_proxies(proxies)
    return True, p_norm


def remove_admin_proxy(identifier: str) -> Tuple[bool, str]:
    """Xóa proxy theo số thứ tự (1-based) hoặc theo URL."""
    proxies = load_admin_proxies()
    if not proxies:
        return False, "Danh sách proxy hiện đang rỗng"
    target = None
    if identifier.isdigit():
        idx = int(identifier) - 1
        if 0 <= idx < len(proxies):
            target = proxies.pop(idx)
        else:
            return False, f"Số thứ tự #{identifier} không tồn tại"
    else:
        p_norm = normalize_proxy_url(identifier)
        for p in proxies:
            if identifier in p or p_norm == p:
                target = p
                proxies.remove(p)
                break
    if target:
        save_admin_proxies(proxies)
        return True, target
    return False, f"Không tìm thấy proxy khớp với '{identifier}'"


def clear_admin_proxies() -> int:
    """Xóa toàn bộ proxy admin, quay về proxy tự host VPS."""
    proxies = load_admin_proxies()
    count = len(proxies)
    save_admin_proxies([])
    return count
