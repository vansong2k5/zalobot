"""
Dịch vụ Quản lý Tính năng Hệ thống (Feature Toggle Service).
Cho phép Admin bật/tắt (dừng/mở) động từng tính năng của Bot thông qua tin nhắn Zalo.
Sử dụng cache in-memory có cơ chế reload để đảm bảo truy vấn O(1) tốc độ cực cao, không làm nghẽn DB.
"""

import time
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models import SystemSetting

logger = logging.getLogger("FeatureService")

# Danh mục các tính năng hệ thống được hỗ trợ
SYSTEM_FEATURES = {
    "BUY": {
        "name": "Đặt mua dịch vụ",
        "desc": "Tạo đơn hàng & thanh toán SePay / ví cho toàn bộ sản phẩm",
        "default": "active"
    },
    "QUICK_BUY": {
        "name": "Mua nhanh bằng số đơn lẻ",
        "desc": "Cho phép gõ phím số (vd: '1') để kích hoạt đơn mua",
        "default": "active"
    },
    "CHECKSDT": {
        "name": "Kiểm tra SĐT Shopee",
        "desc": "Tra cứu đầu số sạch, tình trạng liên kết tài khoản Shopee",
        "default": "active"
    },
    "OTP": {
        "name": "Tra cứu & Cấp mã OTP",
        "desc": "Lấy OTP ViOTP tự động từ nhà mạng khi khách thuê SIM",
        "default": "active"
    },
    "PROXY": {
        "name": "Kho Proxy cá nhân",
        "desc": "Lệnh ADDPROXY, LISTPROXY, CLEARPROXY cho người dùng",
        "default": "active"
    },
    "DONHANG": {
        "name": "Tra cứu đơn hàng đã mua",
        "desc": "Xem lại tài khoản, mật khẩu, cookie các đơn hàng đã mua",
        "default": "active"
    },
    "TRACK": {
        "name": "Tra cứu vận đơn SPX Express",
        "desc": "Theo dõi lộ trình giao hàng Shopee Express tự động",
        "default": "active"
    },
}

# Cache in-memory: { feature_code: "active"|"paused" }
_FEATURE_CACHE: Dict[str, str] = {}
_LAST_CACHE_SYNC: float = 0
_CACHE_TTL: float = 30.0  # Tự động refresh cache từ DB sau mỗi 30 giây


def _sync_cache_from_db(db: Session, force: bool = False):
    """Đồng bộ trạng thái toàn bộ feature flags từ DB vào RAM."""
    global _FEATURE_CACHE, _LAST_CACHE_SYNC
    now = time.time()
    if not force and _FEATURE_CACHE and (now - _LAST_CACHE_SYNC < _CACHE_TTL):
        return

    try:
        settings = db.query(SystemSetting).filter(SystemSetting.key.like("feature:%")).all()
        cache = {}
        for s in settings:
            feat_code = s.key.replace("feature:", "").upper()
            cache[feat_code] = s.value.strip().lower()
        _FEATURE_CACHE = cache
        _LAST_CACHE_SYNC = now
    except Exception as e:
        logger.error("Lỗi đồng bộ Feature Cache từ DB: %s", e)


def is_feature_active(db: Session, feature_code: str) -> bool:
    """
    Kiểm tra xem một tính năng có đang BẬT (active) hay không.
    Mặc định trả về True nếu tính năng chưa từng bị tắt.
    """
    clean_code = str(feature_code).strip().upper()
    _sync_cache_from_db(db)

    # Đọc từ cache
    cached_val = _FEATURE_CACHE.get(clean_code)
    if cached_val is not None:
        return cached_val == "active"

    # Nếu chưa có trong cache, truy vấn DB
    try:
        setting = db.query(SystemSetting).filter(SystemSetting.key == f"feature:{clean_code}").first()
        if setting:
            _FEATURE_CACHE[clean_code] = setting.value.strip().lower()
            return setting.value.strip().lower() == "active"
    except Exception as e:
        logger.error("Lỗi đọc trạng thái tính năng %s: %s", clean_code, e)

    # Mặc định là active nếu không có bản ghi
    return True


def set_feature_status(db: Session, feature_code: str, is_active: bool) -> bool:
    """
    Bật hoặc Tắt một tính năng hệ thống (Lưu vào DB và cập nhật cache tức thì).
    """
    clean_code = str(feature_code).strip().upper()
    new_val = "active" if is_active else "paused"

    try:
        setting = db.query(SystemSetting).filter(SystemSetting.key == f"feature:{clean_code}").first()
        desc = SYSTEM_FEATURES.get(clean_code, {}).get("desc", f"Tính năng {clean_code}")
        if not setting:
            setting = SystemSetting(
                key=f"feature:{clean_code}",
                value=new_val,
                description=desc
            )
            db.add(setting)
        else:
            setting.value = new_val
            setting.description = desc

        db.commit()
        _FEATURE_CACHE[clean_code] = new_val
        return True
    except Exception as e:
        db.rollback()
        logger.error("Lỗi cập nhật tính năng %s: %s", clean_code, e)
        return False


def get_all_features_status(db: Session) -> Dict[str, Dict[str, Any]]:
    """
    Lấy danh sách toàn bộ tính năng và trạng thái bật/tắt hiện tại.
    """
    _sync_cache_from_db(db, force=True)
    res = {}
    for code, info in SYSTEM_FEATURES.items():
        val = _FEATURE_CACHE.get(code, info["default"])
        res[code] = {
            "name": info["name"],
            "desc": info["desc"],
            "is_active": (val == "active"),
            "status_str": "🟢 Đang hoạt động" if val == "active" else "🔴 Đang tạm dừng (Bảo trì)"
        }
    return res
