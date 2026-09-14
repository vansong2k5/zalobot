"""
Dịch vụ Quản lý Email & Xác thực Tài khoản Shopee Tự Động.
Xử lý Gán Hòm Thư Bảo Mật (Add Mail), Đọc Mã OTP và Xác Minh Đăng Nhập cho từng tài khoản đã mua.
"""

import re
import logging
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

import httpx
from sqlalchemy.orm import Session

from app.config import VUBEL_API_BASE, VUBEL_API_KEY, DEFAULT_PROXY, PROXY_LIST
from app.models import ProductStock, Order

logger = logging.getLogger("ShopeeEmailService")


def call_shopee_core_api(endpoint: str, method: str = "POST", json_data: dict = None, params: dict = None) -> dict:
    """
    Gọi API lõi xử lý tài khoản Shopee & Hòm thư xác thực.
    """
    url = f"{VUBEL_API_BASE}{endpoint}"
    headers = {
        "x-api-key": VUBEL_API_KEY,
        "Content-Type": "application/json; charset=utf-8"
    }

    try:
        with httpx.Client(timeout=50.0) as client:
            if method.upper() == "POST":
                resp = client.post(url, headers=headers, json=json_data or {})
            else:
                resp = client.get(url, headers=headers, params=params or {})

            try:
                return resp.json()
            except Exception:
                return {
                    "ok": False,
                    "error": f"Máy chủ phản hồi mã HTTP {resp.status_code}: {resp.text[:200]}"
                }
    except Exception as ex:
        logger.error("Lỗi kết nối máy chủ xác thực (%s): %s", url, ex)
        return {"ok": False, "error": f"Lỗi kết nối máy chủ dịch vụ: {str(ex)}"}


def format_stock_account_input(stock: ProductStock) -> str:
    """
    Chuẩn hóa dữ liệu tài khoản trong ProductStock thành định dạng input xử lý:
    - Nếu có Cookie SPC_ST: "SPC_ST=..."
    - Nếu có User + Pass + SPC_F: "user|pass|SPC_F=..."
    - Nếu tài khoản đã ở dạng chuỗi gộp: giữ nguyên
    """
    if stock.cookie_spc_st and stock.cookie_spc_st.strip():
        c = stock.cookie_spc_st.strip()
        return c if c.startswith("SPC_ST=") else f"SPC_ST={c}"

    username = (stock.account or "").strip()
    password = (stock.password or "").strip()
    spc_f = (stock.cookie_spc_f or "").strip() or "f_auto_device_fingerprint"

    if username and password:
        return f"{username}|{password}|SPC_F={spc_f}"

    return username or password


def find_user_stock_by_identifier(
    db: Session,
    user_id: int,
    identifier: str
) -> Tuple[Optional[ProductStock], Optional[str]]:
    """
    Tra cứu tài khoản mà khách hàng này đã mua:
    Hỗ trợ identifier là:
    - Mã ID tài khoản (ví dụ: '105' hoặc '#105')
    - Hoặc Mã Đơn Hàng (ví dụ: 'DH100201')
    
    Đảm bảo 100% tài khoản đó thuộc quyền sở hữu của user_id (Bảo mật tuyệt đối).
    """
    clean_id = identifier.strip().lstrip("#")
    if not clean_id:
        return None, "⚠️ Vui lòng cung cấp Mã ID tài khoản hoặc Mã đơn hàng!"

    # 1. Tìm theo ID tài khoản (ProductStock.id)
    if clean_id.isdigit():
        stock_id = int(clean_id)
        stock = db.query(ProductStock).filter(ProductStock.id == stock_id).first()
        if stock:
            # Kiểm tra xem tài khoản này đã bán cho user này chưa (tránh thao tác trên nick đã thu hồi hoặc chưa bán)
            if not stock.order or stock.order.user_id != user_id or stock.status != "sold":
                return None, f"⛔ Bạn không có quyền thao tác trên tài khoản ID [#{stock_id}]."
            return stock, None

    # 2. Tìm theo Mã đơn hàng (Order.order_code)
    order_code = clean_id.upper()
    order = db.query(Order).filter(
        Order.order_code == order_code,
        Order.user_id == user_id
    ).first()

    if order:
        stocks = db.query(ProductStock).filter(
            ProductStock.order_id == order.id,
            ProductStock.status == "sold"
        ).all()

        if not stocks:
            return None, f"⚠️ Đơn hàng #{order_code} không tìm thấy tài khoản nào trong kho."

        if len(stocks) == 1:
            return stocks[0], None

        # Đơn có nhiều tài khoản -> yêu cầu khách chỉ định rõ ID
        id_list = ", ".join([f"#{s.id}" for s in stocks])
        return None, (
            f"ℹ️ Đơn hàng #{order_code} gồm {len(stocks)} tài khoản ({id_list}).\n"
            f"👉 Vui lòng nhập đúng Mã ID tài khoản cụ thể. Ví dụ: OTP {stocks[0].id}"
        )

    return None, f"❌ Không tìm thấy tài khoản hoặc đơn hàng [{identifier}] thuộc sở hữu của bạn."


BACKUP_PROXY = "http://163.61.183.185:38888"


def get_candidate_proxies(preferred_proxy: str = None) -> list:
    """
    Lấy danh sách proxy khả dụng theo thứ tự ưu tiên:
    1. preferred_proxy (nếu có truyền)
    2. Danh sách Proxy do Admin cấu hình (admin_proxies.json)
    3. Danh sách PROXY_LIST trong .env
    4. DEFAULT_PROXY tự host của VPS (Tinyproxy :38888)
    """
    candidates = []
    if preferred_proxy and preferred_proxy.strip():
        p = preferred_proxy.strip()
        if not p.startswith("http://") and not p.startswith("https://") and not p.startswith("socks5://"):
            p = f"http://{p}"
        candidates.append(p)

    # 2. Ưu tiên Proxy Admin nạp vào
    try:
        from .proxy_service import load_admin_proxies
        for p in load_admin_proxies():
            if p and p not in candidates:
                candidates.append(p)
    except Exception:
        pass

    for item in PROXY_LIST:
        if item and item.strip():
            p = item.strip()
            if not p.startswith("http://") and not p.startswith("https://") and not p.startswith("socks5://"):
                p = f"http://{p}"
            if p not in candidates:
                candidates.append(p)

    # Fallback mặc định về Proxy tự host VPS
    vps_fallback = DEFAULT_PROXY or "http://163.61.183.185:38888"
    if vps_fallback not in candidates:
        candidates.append(vps_fallback)

    return candidates


def add_email_to_shopee_account(
    db: Session,
    stock: ProductStock,
    custom_email: str = None,
    proxy: str = None,
    strict_user_proxy: bool = False
) -> Dict[str, Any]:
    """
    Gán hòm thư bảo mật (Add Mail) vào tài khoản Shopee.
    - strict_user_proxy=True: BẮT BUỘC chỉ dùng Proxy riêng của User, không dùng chung IP VPS hoặc proxy hệ thống.
    - Tích hợp tự động chuyển mạch Proxy (Auto-failover) nếu không dùng chế độ strict.
    1. Nếu đã có sẵn Cookie SPC_ST sống: Thử addmail trực tiếp bằng SPC_ST trước (nhanh, không cần login).
    2. Nếu chưa có SPC_ST hoặc SPC_ST hết hạn: Đăng nhập Shopee (/v1/shopee/login) để lấy SPC_ST mới.
    3. Gán email mới vào tài khoản qua /v1/shopee/addmail.
    """
    if strict_user_proxy:
        if not proxy or not proxy.strip():
            return {
                "ok": False,
                "error": "Bắt buộc phải có Proxy riêng của bạn để gán email. Vui lòng thêm proxy qua lệnh ADDPROXY!"
            }
        p_clean = proxy.strip()
        if not p_clean.startswith("http://") and not p_clean.startswith("https://") and not p_clean.startswith("socks5://"):
            p_clean = f"http://{p_clean}"
        proxy_candidates = [p_clean]
    else:
        proxy_candidates = get_candidate_proxies(proxy)

    if not proxy_candidates:
        return {
            "ok": False,
            "error": "Hệ thống chưa cấu hình Proxy kết nối Shopee. Vui lòng nạp Proxy riêng của bạn qua lệnh ADDPROXY!"
        }

    # Nếu tài khoản đã được gán email thành công trước đó và khách không yêu cầu đổi email cụ thể
    if stock.assigned_email and stock.mail_status in ["added", "linked"] and not (custom_email and custom_email.strip()):
        return {
            "ok": True,
            "already_linked": True,
            "email": stock.assigned_email,
            "password": stock.email_password or "",
            "message": f"Tài khoản này đã có sẵn email liên kết: {stock.assigned_email}"
        }

    username = (stock.account or "").strip()
    password = (stock.password or "").strip()
    spc_f = (stock.cookie_spc_f or "").replace("SPC_F=", "").strip() or "7Ou56TEKGyd6gdP9rbH4m4j798u1wCPz"
    active_spc_st = (stock.cookie_spc_st or "").replace("SPC_ST=", "").strip()

    last_error_msg = "Không thể gán email vào Shopee lúc này. Vui lòng thử lại sau ít phút!"
    last_res = {}

    for used_proxy in proxy_candidates:
        logger.info("Đang thử gán email Stock #%s với proxy=%s...", stock.id, used_proxy)

        # -------------------------------------------------------------
        # BƯỚC 1: NẾU ĐÃ CÓ SPC_ST SỐNG, THỬ GỌI TRỰC TIẾP /v1/shopee/addmail
        # -------------------------------------------------------------
        if active_spc_st:
            payload = {
                "input": f"SPC_ST={active_spc_st}",
                "proxy": used_proxy
            }
            if custom_email and custom_email.strip():
                payload["email"] = custom_email.strip()
            else:
                payload["mode"] = "random"

            res = call_shopee_core_api("/v1/shopee/addmail", method="POST", json_data=payload)
            err_text = str(res.get("error") or res.get("message") or "")

            # Nếu proxy bị lỗi kết nối 502 / refused / timeout
            if "<!doctype" in err_text.lower() or "502" in err_text or "refused" in err_text.lower() or "timed out" in err_text.lower():
                logger.warning("Proxy %s lỗi kết nối Shopee khi addmail (502). Thử proxy tiếp theo...", used_proxy)
                last_error_msg = "Proxy kết nối Shopee bị gián đoạn (502 / Timed out). Đang thử tuyến proxy khác..."
                continue

            data = res.get("data") or {}
            assigned_email = data.get("email") or custom_email or res.get("email")
            email_pass = data.get("password") or data.get("pass") or res.get("password") or ""
            is_ok = res.get("ok") is True or res.get("status") == "success"

            # Kiểm tra xem tài khoản đã liên kết email trước đó chưa
            match_linked = re.search(r"đã liên kết email:\s*([^\s]+)", err_text, re.IGNORECASE)
            if match_linked:
                existing_email = match_linked.group(1).strip()
                stock.assigned_email = existing_email
                stock.mail_status = "linked"
                db.commit()
                db.refresh(stock)
                return {
                    "ok": True,
                    "already_linked": True,
                    "email": existing_email,
                    "message": f"Tài khoản này đã có sẵn email liên kết: {existing_email}",
                    "raw": res
                }

            if is_ok or (assigned_email and "lỗi" not in err_text.lower() and "thất bại" not in err_text.lower()):
                stock.assigned_email = assigned_email
                if email_pass:
                    stock.email_password = email_pass
                stock.mail_status = "added"
                db.commit()
                db.refresh(stock)
                return {
                    "ok": True,
                    "already_linked": False,
                    "message": res.get("message") or "Đã gán email bảo mật vào Shopee thành công!",
                    "email": assigned_email,
                    "password": email_pass,
                    "raw": res
                }

            # Nếu lỗi phiên đăng nhập hết hạn -> xóa active_spc_st để login lại ở bước sau
            if "login" in err_text.lower() or "unauthorized" in err_text.lower() or "phiên" in err_text.lower() or "cookie" in err_text.lower():
                logger.info("Cookie SPC_ST của Stock #%s đã hết hạn, chuyển sang đăng nhập lại...", stock.id)
                active_spc_st = ""

        # -------------------------------------------------------------
        # BƯỚC 2: ĐĂNG NHẬP SHOPEE LẠI NẾU CHƯA CÓ SPC_ST HOẶC SPC_ST HẾT HẠN
        # -------------------------------------------------------------
        if username and password and not active_spc_st:
            login_payload = {
                "username": username,
                "password": password,
                "spc_f": spc_f,
                "proxy": used_proxy
            }
            login_res = call_shopee_core_api("/v1/shopee/login", method="POST", json_data=login_payload)
            if login_res.get("ok"):
                login_data = login_res.get("data") or {}
                acc_info = login_data.get("account") or {}
                existing_email = (acc_info.get("email") or "").strip()

                new_st = login_data.get("spc_st") or login_res.get("spc_st")
                if new_st:
                    active_spc_st = new_st
                    stock.cookie_spc_st = f"SPC_ST={new_st}"
                    db.commit()

                if existing_email and "@" in existing_email:
                    stock.assigned_email = existing_email
                    stock.mail_status = "linked"
                    db.commit()
                    db.refresh(stock)
                    return {
                        "ok": True,
                        "already_linked": True,
                        "email": existing_email,
                        "message": f"Tài khoản này đã có sẵn email liên kết: {existing_email}",
                        "raw": login_res
                    }
            else:
                login_err = str(login_res.get("error") or "")
                if "502" in login_err or "refused" in login_err.lower() or "<!doctype" in login_err.lower() or "timed out" in login_err.lower():
                    logger.warning("Proxy %s lỗi kết nối Shopee khi login (502). Đang thử proxy kế tiếp...", used_proxy)
                    last_error_msg = "Proxy kết nối Shopee bị gián đoạn (502 / Timed out). Đang thử tuyến proxy khác..."
                    continue

        # -------------------------------------------------------------
        # BƯỚC 3: GỌI LỆNH GÁN EMAIL VỚI THÔNG TIN VỪA CÓ
        # -------------------------------------------------------------
        account_input = f"SPC_ST={active_spc_st}" if active_spc_st else f"{username}|{password}|SPC_F={spc_f}"
        payload = {
            "input": account_input,
            "proxy": used_proxy
        }
        if custom_email and custom_email.strip():
            payload["email"] = custom_email.strip()
        else:
            payload["mode"] = "random"

        res = call_shopee_core_api("/v1/shopee/addmail", method="POST", json_data=payload)
        last_res = res

        err_text = str(res.get("error") or res.get("message") or "")
        data = res.get("data") or {}
        assigned_email = data.get("email") or custom_email or res.get("email")
        email_pass = data.get("password") or data.get("pass") or res.get("password") or ""
        is_ok = res.get("ok") is True or res.get("status") == "success"

        match_linked = re.search(r"đã liên kết email:\s*([^\s]+)", err_text, re.IGNORECASE)
        if match_linked:
            existing_email = match_linked.group(1).strip()
            stock.assigned_email = existing_email
            stock.mail_status = "linked"
            db.commit()
            db.refresh(stock)
            return {
                "ok": True,
                "already_linked": True,
                "email": existing_email,
                "message": f"Tài khoản này đã có sẵn email liên kết: {existing_email}",
                "raw": res
            }

        if is_ok or (assigned_email and "lỗi" not in err_text.lower() and "thất bại" not in err_text.lower()):
            stock.assigned_email = assigned_email
            if email_pass:
                stock.email_password = email_pass
            stock.mail_status = "added"
            db.commit()
            db.refresh(stock)

            return {
                "ok": True,
                "already_linked": False,
                "message": res.get("message") or "Đã gửi lệnh gán email vào tài khoản Shopee thành công!",
                "email": assigned_email,
                "password": email_pass,
                "raw": res
            }

        if "<!doctype" in err_text.lower() or "502" in err_text or "refused" in err_text.lower() or "timed out" in err_text.lower():
            logger.warning("Lệnh addmail qua proxy %s thất bại (502). Chuyển sang proxy kế tiếp...", used_proxy)
            last_error_msg = "Proxy kết nối Shopee bị gián đoạn (502 / Timed out). Đang thử tuyến proxy khác..."
            continue
        else:
            last_error_msg = err_text or "Không thể gán email vào Shopee lúc này. Vui lòng thử lại sau ít phút!"
            break

    # Nếu chạy hết danh sách proxy mà vẫn lỗi 502/timeout
    if "502" in last_error_msg or "timed out" in last_error_msg.lower() or "gián đoạn" in last_error_msg:
        last_error_msg = "Proxy kết nối Shopee hiện tại không phản hồi (502 / Hết hạn kết nối). Vui lòng cập nhật Proxy sống mới trong cấu hình hệ thống!"

    return {"ok": False, "error": last_error_msg, "raw": last_res}


def fetch_otp_from_email(
    db: Session,
    stock: ProductStock
) -> Dict[str, Any]:
    """
    Quét hòm thư bảo mật của tài khoản để lấy mã OTP Shopee mới nhất.
    """
    email = (stock.assigned_email or "").strip()
    if not email:
        return {
            "ok": False,
            "error": "Tài khoản này chưa được gán Email! Bạn hãy dùng lệnh ADDMAIL trước."
        }

    password = (stock.email_password or "").strip()

    # Nếu chưa có password trong DB, tự động tra cứu từ lịch sử hệ thống
    if not password:
        hist = call_shopee_core_api("/v1/email/history?limit=50&offset=0", method="GET")
        if hist.get("ok") and hist.get("data", {}).get("items"):
            for item in hist["data"]["items"]:
                if item.get("email") == email and item.get("password"):
                    password = item["password"]
                    stock.email_password = password
                    db.commit()
                    break

    if not password:
        return {
            "ok": False,
            "error": f"Không tìm thấy mật khẩu hòm thư cho email '{email}'. Vui lòng liên hệ Admin hỗ trợ!"
        }

    payload = {
        "email": email,
        "password": password
    }
    logger.info("Quét hòm thư cho email: %s", email)
    res = call_shopee_core_api("/v1/email/get", method="POST", json_data=payload)

    if not res.get("ok"):
        err = res.get("error") or "Không thể kết nối đến máy chủ hòm thư lúc này."
        return {"ok": False, "error": err}

    emails = res.get("data", {}).get("emails", [])
    if not emails:
        return {
            "ok": True,
            "has_mail": False,
            "email": email,
            "message": "Hòm thư hiện chưa có email mới nào từ Shopee. Vui lòng bấm gửi mã trên Shopee và thử lại sau vài giây!"
        }

    # Lấy email mới nhất
    latest = emails[0]
    subject = latest.get("subject", "")
    body = latest.get("body", "") or latest.get("text", "") or ""
    date_str = latest.get("date", "") or datetime.now().strftime("%H:%M:%S %d/%m/%Y")

    # Tìm mã OTP 6 chữ số
    otp_code = None
    all_text = f"{subject} {body}"
    numbers = re.findall(r"\b\d{6}\b", all_text)
    if numbers:
        otp_code = numbers[0]

    # Lưu mã OTP gần nhất vào DB
    if otp_code:
        stock.last_otp = otp_code
        stock.last_otp_at = datetime.utcnow()
        stock.mail_status = "verified"
        db.commit()

    return {
        "ok": True,
        "has_mail": True,
        "email": email,
        "otp": otp_code,
        "subject": subject,
        "date": date_str,
        "body_preview": (body[:160] + "...") if len(body) > 160 else body
    }


def verify_shopee_email_link(
    db: Session,
    stock: ProductStock
) -> Dict[str, Any]:
    """
    Tự động phê duyệt đăng nhập Shopee (hỗ trợ cả Duyệt thiết bị lạ & Xác minh qua Email).
    """
    username = (stock.account or "").strip()
    password_acc = (stock.password or "").strip()
    spc_f = (stock.cookie_spc_f or "").replace("SPC_F=", "").strip()
    proxy_used = DEFAULT_PROXY or "http://163.61.183.185:8888"

    # BƯỚC 1: DUYỆT THIẾT BỊ LẠ TRỰC TIẾP QUA SHOPEE NOTIFICATION (/v1/verify/device)
    # Phương thức này cực kỳ mạnh, không cần mật khẩu hòm thư!
    if username and password_acc:
        try:
            device_payload = {
                "input": f"{username}|{password_acc}|SPC_F={spc_f}",
                "proxy": proxy_used
            }
            logger.info("Gọi /v1/verify/device cho Stock #%s (%s)...", stock.id, username)
            dev_res = call_shopee_core_api("/v1/verify/device", method="POST", json_data=device_payload)
            if dev_res.get("ok"):
                data = dev_res.get("data") or {}
                results = data.get("results") or []
                # Nếu có thiết bị được duyệt hoặc có kết quả duyệt
                if results or "thành công" in str(dev_res).lower():
                    stock.mail_status = "verified"
                    db.commit()
                    return {
                        "ok": True,
                        "message": "Đã tự động bấm duyệt đăng nhập thiết bị mới của Shopee thành công! 🎉",
                        "type": "device"
                    }
        except Exception as e:
            logger.warning("Lỗi duyệt device cho Stock #%s: %s", stock.id, e)

    email = (stock.assigned_email or "").strip()
    password_mail = (stock.email_password or "").strip()

    if not password_mail and email:
        hist = call_shopee_core_api("/v1/email/history?limit=100&offset=0", method="GET")
        if hist.get("ok") and hist.get("data", {}).get("items"):
            for item in hist["data"]["items"]:
                if item.get("email") == email and item.get("password"):
                    password_mail = item["password"]
                    stock.email_password = password_mail
                    db.commit()
                    break

    # BƯỚC 2: XÁC MINH QUA LINK EMAIL NẾU CÓ PASSWORD HÒM THƯ
    if email and password_mail:
        payload = {"email": email, "password": password_mail}
        logger.info("Tự động xác minh liên kết email Shopee cho: %s", email)
        res = call_shopee_core_api("/v1/verify/email", method="POST", json_data=payload)

        if res.get("ok"):
            stock.mail_status = "verified"
            db.commit()
            return {
                "ok": True,
                "message": res.get("message") or "Đã tự động xác minh liên kết Email thành công!",
                "verify_link": res.get("verify_link") or res.get("link"),
                "type": "email"
            }

        # Quét hòm thư tìm thư xác minh thiết bị mới từ Shopee
        mail_res = call_shopee_core_api("/v1/email/get", method="POST", json_data=payload)
        if mail_res.get("ok"):
            emails = mail_res.get("data", {}).get("emails", [])
            if emails:
                latest = emails[0]
                verify_url = latest.get("verify_link")
                if not verify_url and latest.get("links"):
                    verify_url = latest["links"][0].get("url")

                if not verify_url:
                    content = f"{latest.get('body', '')} {latest.get('text', '')}"
                    found_links = re.findall(r'https?://[^\s"\'<>]+(?:shopee|banhang\.shopee)[^\s"\'<>]*', content)
                    if found_links:
                        verify_url = found_links[0]

                if verify_url:
                    try:
                        headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                        }
                        httpx.get(verify_url, headers=headers, proxy=proxy_used, timeout=12.0, follow_redirects=True)
                        stock.mail_status = "verified"
                        db.commit()
                        return {
                            "ok": True,
                            "message": "Đã tự động kích hoạt link duyệt đăng nhập thiết bị mới của Shopee thành công!",
                            "verify_link": verify_url,
                            "type": "email"
                        }
                    except Exception as ex:
                        logger.warning("Không thể tự click link xác nhận: %s", ex)
                        return {
                            "ok": True,
                            "message": "Đã tìm thấy link duyệt đăng nhập Shopee!",
                            "verify_link": verify_url,
                            "type": "email"
                        }

    # BƯỚC 3: XỬ LÝ KHI TÀI KHOẢN KHÔNG CÓ MẬT KHẨU EMAIL
    if email and not password_mail:
        return {
            "ok": False,
            "no_mail_pass": True,
            "error": "no_email_password",
            "message": "Tài khoản này dùng phương thức xác nhận thiết bị lạ hoặc SMS."
        }

    return {
        "ok": False,
        "error": "not_found",
        "message": "Chưa nhận được yêu cầu duyệt đăng nhập mới từ Shopee."
    }
