"""
Module User Proxy Service — Quản lý kho Proxy riêng biệt của từng khách hàng (Đa Kênh: Zalo, Telegram, Discord, Web):
- ADDPROXY: Thêm proxy riêng của khách, tự động test kết nối và lưu vào DB với user_id tương ứng.
- Phân lập hoàn toàn kho proxy của từng user để tránh WAF và tránh dùng chung IP.
- LISTPROXY: Xem danh sách proxy riêng của user.
- CLEARPROXY: Xóa proxy của user.
- Tự động test sống/chết và lọc proxy trước khi đưa vào luồng tự động hóa.
"""

import re
from datetime import datetime
from typing import List, Tuple, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.db import SessionLocal
from app.models import UserProxy, User, UserIdentity
from app.services.proxy_service import normalize_proxy_url as parse_and_normalize_proxy, validate_proxy_connection


def _resolve_core_user_id(db: Session, platform_user_id: str, platform: str = "zalo") -> Optional[int]:
    """Tìm core user_id từ Star Schema."""
    if not platform_user_id:
        return None
    ident = db.query(UserIdentity).filter(
        UserIdentity.platform == platform,
        UserIdentity.platform_user_id == str(platform_user_id)
    ).first()
    if ident:
        return ident.user_id
    if platform == "zalo":
        u = db.query(User).filter(User.user_id == str(platform_user_id)).first()
        if u:
            return u.id
    return None


def add_user_proxies(platform_user_id: str, raw_input: str, platform: str = "zalo") -> Dict[str, Any]:
    """
    Thêm một hoặc nhiều proxy riêng cho người dùng trên bất kỳ nền tảng nào (Zalo, Telegram, Discord).
    Tự động kiểm tra từng proxy trước khi lưu và gắn với user_id cốt lõi.
    """
    if not raw_input:
        return {
            "ok": False,
            "added_count": 0,
            "failed_count": 0,
            "message": "Vui lòng cung cấp ít nhất 1 địa chỉ Proxy hợp lệ!"
        }

    raw_lines = re.split(r"[\r\n;,]+", raw_input.strip())
    candidates = [line.strip() for line in raw_lines if line.strip()]

    if not candidates:
        return {
            "ok": False,
            "added_count": 0,
            "failed_count": 0,
            "message": "Không tìm thấy địa chỉ Proxy nào trong nội dung bạn gửi."
        }

    db: Session = SessionLocal()
    added_list = []
    failed_list = []

    try:
        core_user_id = _resolve_core_user_id(db, platform_user_id, platform)

        for raw in candidates:
            cleaned = re.sub(r"^(ADDPROXY|THEMPROXY|PROXY)\s*", "", raw, flags=re.IGNORECASE).strip()
            if not cleaned:
                continue

            normalized = parse_and_normalize_proxy(cleaned)
            if not normalized:
                failed_list.append(f"{cleaned} (Định dạng không đúng)")
                continue

            # Kiểm tra xem proxy này đã có trong kho của user chưa
            filter_cond = [UserProxy.proxy_url == normalized, UserProxy.status == "active"]
            if core_user_id:
                filter_cond.append(or_(UserProxy.user_id == core_user_id, UserProxy.zalo_user_id == str(platform_user_id)))
            else:
                filter_cond.append(UserProxy.zalo_user_id == str(platform_user_id))

            exists = db.query(UserProxy).filter(*filter_cond).first()
            if exists:
                failed_list.append(f"{cleaned} (Đã có sẵn trong kho)")
                continue

            # Test kết nối thực tế tới Shopee
            is_valid, msg, info = validate_proxy_connection(normalized, timeout=6)
            if not is_valid:
                failed_list.append(f"{cleaned} ({msg})")
                continue

            # Lưu vào CSDL với core_user_id và platform
            new_p = UserProxy(
                user_id=core_user_id,
                platform=platform,
                zalo_user_id=str(platform_user_id) if platform == "zalo" else "",
                proxy_url=normalized,
                status="active",
                latency_ms=info.get("latency_ms", 0),
                last_checked_at=datetime.utcnow(),
                created_at=datetime.utcnow()
            )
            db.add(new_p)
            added_list.append(f"{info.get('ip')} ({info.get('latency_ms')}ms)")

        db.commit()

        # Tổng số proxy active hiện tại của user
        count_cond = [UserProxy.status == "active"]
        if core_user_id:
            count_cond.append(or_(UserProxy.user_id == core_user_id, UserProxy.zalo_user_id == str(platform_user_id)))
        else:
            count_cond.append(UserProxy.zalo_user_id == str(platform_user_id))

        total_active = db.query(UserProxy).filter(*count_cond).count()

    except Exception as e:
        db.rollback()
        return {
            "ok": False,
            "added_count": 0,
            "failed_count": len(candidates),
            "message": f"Lỗi hệ thống khi lưu proxy: {str(e)}"
        }
    finally:
        db.close()

    return {
        "ok": len(added_list) > 0,
        "added_count": len(added_list),
        "failed_count": len(failed_list),
        "added_details": added_list,
        "failed_details": failed_list,
        "total_active": total_active
    }


def get_user_available_proxies(platform_user_id: str, count: int = 1, platform: str = "zalo") -> List[str]:
    """Lấy danh sách proxy riêng của user để phục vụ luồng tự động hóa."""
    db: Session = SessionLocal()
    try:
        core_user_id = _resolve_core_user_id(db, platform_user_id, platform)
        cond = [UserProxy.status == "active"]
        if core_user_id:
            cond.append(or_(UserProxy.user_id == core_user_id, UserProxy.zalo_user_id == str(platform_user_id)))
        else:
            cond.append(UserProxy.zalo_user_id == str(platform_user_id))

        proxies = db.query(UserProxy).filter(*cond).order_by(UserProxy.id.asc()).limit(count).all()
        return [p.proxy_url for p in proxies]
    finally:
        db.close()


def get_user_proxy_stats(platform_user_id: str, platform: str = "zalo") -> Dict[str, Any]:
    """Lấy thống kê và danh sách proxy riêng của user."""
    db: Session = SessionLocal()
    try:
        core_user_id = _resolve_core_user_id(db, platform_user_id, platform)
        cond = [UserProxy.status == "active"]
        if core_user_id:
            cond.append(or_(UserProxy.user_id == core_user_id, UserProxy.zalo_user_id == str(platform_user_id)))
        else:
            cond.append(UserProxy.zalo_user_id == str(platform_user_id))

        proxies = db.query(UserProxy).filter(*cond).all()

        return {
            "total": len(proxies),
            "proxies": [
                {
                    "id": p.id,
                    "url": p.proxy_url,
                    "latency_ms": p.latency_ms,
                    "created_at": p.created_at.strftime("%H:%M %d/%m") if p.created_at else ""
                }
                for p in proxies
            ]
        }
    finally:
        db.close()


def clear_user_proxies(platform_user_id: str, platform: str = "zalo") -> int:
    """Xóa sạch toàn bộ proxy riêng của user."""
    db: Session = SessionLocal()
    try:
        core_user_id = _resolve_core_user_id(db, platform_user_id, platform)
        cond = []
        if core_user_id:
            cond.append(or_(UserProxy.user_id == core_user_id, UserProxy.zalo_user_id == str(platform_user_id)))
        else:
            cond.append(UserProxy.zalo_user_id == str(platform_user_id))

        deleted = db.query(UserProxy).filter(*cond).delete(synchronize_session=False)
        db.commit()
        return deleted
    finally:
        db.close()


def deactivate_dead_proxy(platform_user_id: str, proxy_url: str, reason: str = "dead", platform: str = "zalo") -> bool:
    """Đánh dấu proxy đã chết để không tái sử dụng."""
    db: Session = SessionLocal()
    try:
        core_user_id = _resolve_core_user_id(db, platform_user_id, platform)
        cond = [UserProxy.proxy_url == proxy_url]
        if core_user_id:
            cond.append(or_(UserProxy.user_id == core_user_id, UserProxy.zalo_user_id == str(platform_user_id)))
        else:
            cond.append(UserProxy.zalo_user_id == str(platform_user_id))

        db.query(UserProxy).filter(*cond).update({"status": "dead"}, synchronize_session=False)
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        db.close()


def get_user_verified_proxies(platform_user_id: str, count: int = 1, platform: str = "zalo") -> Tuple[List[str], List[str]]:
    """Lấy danh sách proxy khả dụng và tự động test sống/chết trước khi cấp."""
    db: Session = SessionLocal()
    live_proxies = []
    dead_proxies = []

    try:
        core_user_id = _resolve_core_user_id(db, platform_user_id, platform)
        cond = [UserProxy.status == "active"]
        if core_user_id:
            cond.append(or_(UserProxy.user_id == core_user_id, UserProxy.zalo_user_id == str(platform_user_id)))
        else:
            cond.append(UserProxy.zalo_user_id == str(platform_user_id))

        all_active = db.query(UserProxy).filter(*cond).order_by(UserProxy.id.asc()).all()

        for p in all_active:
            is_valid, msg, _ = validate_proxy_connection(p.proxy_url, timeout=5)
            if is_valid:
                live_proxies.append(p.proxy_url)
                if len(live_proxies) >= count:
                    break
            else:
                p.status = "dead"
                dead_proxies.append(p.proxy_url)

        if dead_proxies:
            db.commit()

        return live_proxies, dead_proxies
    finally:
        db.close()
