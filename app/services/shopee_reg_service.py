"""
Module Shopee Registration Service - Tự động hóa đăng ký tài khoản Shopee chuẩn Vũ Bel:
1. Yêu cầu Proxy người dùng cung cấp & kiểm tra kết nối độc lập
2. Thuê số điện thoại (Số Grab ID: 20 hoặc Shopee ID: 4 từ ViOTP)
3. Nhập số & Giải Captcha trượt (Slider Puzzle) bằng OpenCV & Human-like mouse
4. Hứng OTP tự động từ ViOTP
5. XỬ LÝ LINH HOẠT CẢ 2 TRƯỜNG HỢP:
   - Số cũ đã đăng ký: Bấm 'Reclaim Phone Number' chiếm lại số để tạo acc mới tinh.
   - Số mới tinh chưa từng đăng ký: Tự động chuyển thẳng sang màn hình đặt mật khẩu.
6. Đặt mật khẩu an toàn & Thu hoạch Full Cookies (SPC_ST, SPC_F, SPC_U).
7. Ghi Log chi tiết vào CSDL (bảng shopee_reg_logs) và file logs/shopee_reg.log.
8. Đa luồng (Multi-worker Semaphore) an toàn, chống nghẽn VPS.
"""

import os
import asyncio
import logging
import random
import string
import time
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from playwright.async_api import async_playwright

from app.db import SessionLocal
from app.models import ShopeeRegLog, User, UserIdentity, UserProxy
from app.services.proxy_validator import validate_proxy_connection
from app.services.viotp_service import ViOTPService
from app.services.captcha_solver_service import solve_shopee_slider_captcha, handle_shopee_verification_gate

# Cấu hình Logger chuyên biệt cho Shopee Registration
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
REG_LOG_FILE = os.path.join(LOGS_DIR, "shopee_reg.log")

logger = logging.getLogger("shopee_reg")
logger.setLevel(logging.INFO)
if not logger.handlers:
    fh = logging.FileHandler(REG_LOG_FILE, encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

# Quản lý hàng đợi & Tải cao cho 1,000+ người dùng:
# 1. Khóa phân lập theo từng người dùng (Per-User Lock): Người này không bao giờ chặn người kia
_user_locks: Dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()

async def get_user_reg_lock(user_key: str) -> asyncio.Lock:
    async with _locks_guard:
        if user_key not in _user_locks:
            _user_locks[user_key] = asyncio.Lock()
        return _user_locks[user_key]

# 2. Giới hạn tối đa số phiên Chrome chạy đồng thời để bảo vệ VPS RAM/CPU không bao giờ sập (OOM)
MAX_CONCURRENT_REG = 6
_reg_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REG)


def generate_secure_password(length: int = 12) -> str:
    """Sinh mật khẩu thỏa mãn chính sách Shopee: có chữ hoa, thường, số và ký tự đặc biệt."""
    upper = random.choice(string.ascii_uppercase)
    lower = random.choice(string.ascii_lowercase)
    digit = random.choice(string.digits)
    special = random.choice("!@#$%&*")
    others = "".join(random.choices(string.ascii_letters + string.digits, k=length - 4))
    pwd_list = list(upper + lower + digit + special + others)
    random.shuffle(pwd_list)
    return "".join(pwd_list)


class ShopeeRegService:
    def __init__(self, viotp_token: Optional[str] = None):
        self.viotp = ViOTPService(viotp_token)

    async def register_account(
        self,
        user_proxy: str = "",
        zalo_user_id: str = "",
        platform: str = "zalo",
        service_id: Optional[int] = None,
        provided_phone: Optional[str] = None,
        custom_password: Optional[str] = None,
        acc_prefix: str = "",
        headless: Optional[bool] = None,
        progress_callback: Optional[Callable[[str], Any]] = None
    ) -> Dict[str, Any]:
        """
        Quy trình đăng ký tài khoản Shopee tự động:
        - Phân lập 1 luồng xử lý độc lập cho từng người dùng (Per-User Queue & Lock).
        - Tự động ưu tiên nạp Proxy riêng của từng khách hàng nếu đã add vào kho.
        - Giới hạn Concurrency Pool toàn cục để chịu tải 1,000+ người dùng không bao giờ sập VPS.
        - Giải Captcha trượt S-Curve và tự động bắt SMS OTP.
        """
        from app.services.task_manager import is_stop_requested, update_user_task_progress

        prefix = f"{acc_prefix} " if acc_prefix else ""
        timeline_logs = []

        def log_step(msg: str):
            timestamp = datetime.now().strftime("%H:%M:%S")
            entry = f"[{timestamp}] {msg}"
            timeline_logs.append(entry)
            logger.info(f"[{platform.upper()}:{zalo_user_id or 'SYSTEM'}] {msg}")

        async def notify(msg: str, step_label: str = "", send_to_user: bool = True):
            full_msg = f"{prefix}{msg}".strip()
            log_step(full_msg)
            if step_label and zalo_user_id:
                update_user_task_progress(zalo_user_id, f"{prefix}{step_label}".strip())
            # Luôn gửi tin nhắn tiến độ đến Zalo người dùng
            if send_to_user and progress_callback:
                try:
                    if asyncio.iscoroutinefunction(progress_callback):
                        await progress_callback(full_msg)
                    else:
                        progress_callback(full_msg)
                except Exception as ex_cb:
                    logger.error(f"Lỗi gửi progress_callback: {ex_cb}")

        # 1. Khóa phân lập luồng theo từng User (Người này không chặn người kia, nhưng 1 user không được spam click)
        user_key = f"{platform}_{zalo_user_id}" if zalo_user_id else f"anon_{random.randint(1000, 9999)}"
        user_lock = await get_user_reg_lock(user_key)
        if user_lock.locked():
            return {
                "ok": False,
                "step": "busy",
                "error": "⚠️ Bạn đang có một tiến trình tạo tài khoản đang chạy. Vui lòng chờ hoàn tất hoặc gõ STOP trước khi tạo mới!",
                "should_refund": True
            }

        # 2. Kiểm tra trước nếu user đã bấm STOP
        if zalo_user_id and is_stop_requested(zalo_user_id):
            return {"ok": False, "step": "stopped", "error": "Tiến trình đã được dừng theo yêu cầu của bạn.", "should_refund": True}

        # 3. Tra cứu Core User ID trong Star Schema & Tự động lấy Proxy riêng của User nếu có
        db = SessionLocal()
        core_user_id = None
        try:
            if zalo_user_id:
                ident = db.query(UserIdentity).filter(
                    UserIdentity.platform == platform,
                    UserIdentity.platform_user_id == str(zalo_user_id)
                ).first()
                if ident:
                    core_user_id = ident.user_id
                else:
                    u_legacy = db.query(User).filter(User.user_id == str(zalo_user_id)).first()
                    if u_legacy:
                        core_user_id = u_legacy.id

            # Nếu chưa truyền proxy ngoài, tự động tìm proxy riêng đang active trong kho của user
            if not user_proxy and core_user_id:
                p_user = db.query(UserProxy).filter(
                    UserProxy.user_id == core_user_id,
                    UserProxy.status == "active"
                ).order_by(UserProxy.id.desc()).first()
                if p_user:
                    user_proxy = p_user.proxy_url
                    log_step(f"Đã tự động nạp Proxy riêng từ kho của khách: {user_proxy}")

            # Tạo bản ghi log trong CSDL ban đầu gắn với core_user_id
            reg_record = ShopeeRegLog(
                user_id=core_user_id,
                platform=platform,
                zalo_user_id=zalo_user_id,
                proxy_used=user_proxy or "direct",
                status="in_progress",
                created_at=datetime.utcnow()
            )
            db.add(reg_record)
            db.commit()
            db.refresh(reg_record)
            record_id = reg_record.id
        except Exception as e:
            logger.error(f"Lỗi tạo record log DB: {e}")
            record_id = None
        finally:
            db.close()

        # 4. Chạy trong luồng phân lập của User và kiểm soát bởi Global Worker Semaphore
        async with user_lock:
            async with _reg_semaphore:
                log_step(f"{prefix}Bắt đầu khởi động worker đăng ký tài khoản...")

            # -------------------------------------------------------------
            # BƯỚC 1: Validate Proxy (Ưu tiên proxy truyền vào, nếu die thì fallback về default/direct)
            # -------------------------------------------------------------
            from app.config import DEFAULT_PROXY
            pw_proxy = None
            external_ip = "DIRECT"

            if user_proxy and user_proxy != "direct":
                is_p_valid, p_msg, p_info = validate_proxy_connection(user_proxy)
                if is_p_valid and p_info:
                    proxy_url = p_info["proxy_url"]
                    external_ip = p_info["ip"]
                    if "@" in proxy_url:
                        auth_part, host_port = proxy_url.replace("http://", "").replace("https://", "").replace("socks5://", "").split("@")
                        user_p, pwd_p = auth_part.split(":")
                        pw_proxy = {
                            "server": f"http://{host_port}",
                            "username": user_p,
                            "password": pwd_p
                        }
                    else:
                        pw_proxy = {"server": proxy_url}
                    log_step(f"Đang sử dụng Proxy: {external_ip}")
                else:
                    log_step(f"⚠️ Proxy {user_proxy} không khả dụng ({p_msg}). Đang tự động fallback về IP mặc định...")
                    from app.services.proxy_service import remove_admin_proxy
                    remove_admin_proxy(user_proxy)
                    if DEFAULT_PROXY and DEFAULT_PROXY != "direct":
                        pw_proxy = {"server": DEFAULT_PROXY}
                        external_ip = "DEFAULT_PROXY"
                    else:
                        pw_proxy = None
            else:
                if DEFAULT_PROXY and DEFAULT_PROXY != "direct":
                    pw_proxy = {"server": DEFAULT_PROXY}
                    external_ip = "DEFAULT_PROXY"
                else:
                    pw_proxy = None
                log_step("Đang sử dụng kết nối IP mặc định của hệ thống")
            from app.config import VIOTP_SERVICE_ID
            target_service_id = service_id or VIOTP_SERVICE_ID or 20
            max_phone_tries = 1 if provided_phone else 3

            pw_proxy = None
            if "@" in proxy_url:
                auth_part, host_port = proxy_url.replace("http://", "").replace("https://", "").replace("socks5://", "").split("@")
                user_p, pwd_p = auth_part.split(":")
                pw_proxy = {
                    "server": f"http://{host_port}",
                    "username": user_p,
                    "password": pwd_p
                }
            else:
                pw_proxy = {"server": proxy_url}

            target_password = custom_password or generate_secure_password(12)
            extracted_account = {}

            is_headless = headless if headless is not None else (os.getenv("SHOPEE_HEADLESS", "true").strip().lower() in ("true", "1", "yes"))
            if os.name != "nt" and "DISPLAY" not in os.environ:
                is_headless = True

            launch_kwargs = {
                "headless": is_headless,
                "proxy": pw_proxy,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage"
                ]
            }
            if os.name == "nt":
                launch_kwargs["channel"] = "chrome"

            # -------------------------------------------------------------
            # VÒNG LẶP THỬ TỐI ĐA 3 ĐẦU SỐ
            # -------------------------------------------------------------
            for phone_try in range(1, max_phone_tries + 1):
                if zalo_user_id and is_stop_requested(zalo_user_id):
                    return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP).", "should_refund": True}

                if phone_try > 1:
                    logger.info(f"🔄 [Lần thử {phone_try}/{max_phone_tries}] Thử kết nối đầu số khác...")
                    await asyncio.sleep(2)

                # BƯỚC 2: Cấp số điện thoại
                req_id = None
                if provided_phone:
                    phone_num = provided_phone
                    self._update_db_log(record_id, phone_number=phone_num)
                else:
                    await notify(f"📱 {prefix}Đang thuê số điện thoại...", "1/4 Thuê SĐT")
                    logger.info("Đang kết nối ViOTP request số Shopee...")
                    ok_phone, phone_num, req_id, phone_msg = self.viotp.request_shopee_phone(services=[20, 7, 527])
                    if not ok_phone or not phone_num:
                        log_step(f"Lỗi cấp số lần {phone_try}: {phone_msg}")
                        if phone_try < max_phone_tries:
                            await asyncio.sleep(3)
                            continue
                        self._update_db_log(record_id, status="failed", step_failed="phone", error_msg=phone_msg, logs=timeline_logs)
                        return {"ok": False, "step": "phone", "error": "❌ Tổng đài tạm thời hết đầu số khả dụng. Vui lòng thử lại sau!", "should_refund": True}

                    self._update_db_log(record_id, phone_number=phone_num)
                    log_step(f"Đã cấp số an toàn: {phone_num}")

                from app.services.viotp_service import normalize_vietnamese_phone
                phone_num = normalize_vietnamese_phone(phone_num)

                # Đánh dấu đã thuê số thành công vào task_manager
                if zalo_user_id:
                    from app.services.task_manager import set_user_task_phone_rented
                    set_user_task_phone_rented(str(zalo_user_id), phone_num)

                # BƯỚC 3 & 4: Mở Shopee, nhập số & Vượt Captcha
                async with async_playwright() as p:
                    try:
                        browser = await p.chromium.launch(**launch_kwargs)
                    except Exception:
                        launch_kwargs.pop("channel", None)
                        browser = await p.chromium.launch(**launch_kwargs)

                    context = await browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                        viewport={"width": 1366, "height": 768},
                        locale="vi-VN",
                        timezone_id="Asia/Ho_Chi_Minh"
                    )
                    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
                    page = await context.new_page()

                    try:
                        if zalo_user_id and is_stop_requested(zalo_user_id):
                            await browser.close()
                            return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP).", "should_refund": True}

                        # LOG 2: Đang giải captcha
                        await notify(f"🧩 {prefix}Đang giải captcha...", "2/4 Giải Captcha")
                        await page.goto("https://shopee.vn/buyer/signup", timeout=50000, wait_until="domcontentloaded")
                        await asyncio.sleep(2)

                        # Nhập số điện thoại
                        phone_input = page.locator("input[name='phone'], input[placeholder*='Số điện thoại'], input[autocomplete='tel']").first
                        await phone_input.wait_for(state="visible", timeout=35000)
                        await phone_input.click()
                        await phone_input.fill("")

                        for ch in phone_num:
                            await phone_input.type(ch, delay=random.randint(60, 130))
                            await asyncio.sleep(random.uniform(0.03, 0.08))

                        await asyncio.sleep(1)

                        # Bấm TIẾP THEO
                        next_btn = page.locator("button:has-text('TIẾP THEO'), button:has-text('Tiếp theo'), button:has-text('NEXT')").first
                        is_btn_ready = False
                        for _ in range(6):
                            if await next_btn.count() > 0 and await next_btn.is_enabled():
                                is_btn_ready = True
                                break
                            await asyncio.sleep(0.5)

                        if not is_btn_ready:
                            err_text = "Số điện thoại không hợp lệ hoặc bị khóa đăng ký"
                            try:
                                error_loc = page.locator(".shopee-input-helper-text, [class*='error-message'], [class*='errorMessage']").first
                                if await error_loc.count() > 0 and await error_loc.is_visible():
                                    err_text = (await error_loc.inner_text()).strip()
                            except Exception:
                                pass
                            log_step(f"Shopee từ chối số {phone_num}: {err_text}")
                            await browser.close()
                            if phone_try < max_phone_tries:
                                continue
                            self._update_db_log(record_id, status="failed", step_failed="phone_reject", error_msg=err_text, logs=timeline_logs)
                            return {"ok": False, "step": "phone_reject", "error": f"❌ Shopee thông báo: {err_text}", "should_refund": True}

                        await next_btn.click()
                        await asyncio.sleep(3)

                        # Kiểm tra xem Shopee có báo lỗi số điện thoại không
                        err_text = ""
                        try:
                            error_loc = page.locator(".shopee-input-helper-text, [class*='error-message'], [class*='errorMessage']").first
                            if await error_loc.count() > 0 and await error_loc.is_visible():
                                err_text = (await error_loc.inner_text()).strip()
                        except Exception:
                            pass

                        if err_text:
                            log_step(f"Shopee báo lỗi số {phone_num}: {err_text}")
                            await browser.close()
                            if phone_try < max_phone_tries:
                                continue
                            self._update_db_log(record_id, status="failed", step_failed="phone_reject", error_msg=err_text, logs=timeline_logs)
                            return {"ok": False, "step": "phone_reject", "error": f"❌ Shopee thông báo: {err_text}", "should_refund": True}

                        # Xử lý tất cả các cổng xác thực (Captcha trượt, Popup Zalo, Cuộc gọi thoại, SMS)
                        gate_ok, gate_info = await handle_shopee_verification_gate(
                            page,
                            max_wait_seconds=65
                        )
                        if not gate_ok:
                            log_step(f"Không vượt qua cổng xác thực ở đầu số {phone_num}: {gate_info}")
                            await browser.close()
                            if phone_try < max_phone_tries:
                                continue
                            self._update_db_log(record_id, status="failed", step_failed="captcha", error_msg=gate_info, logs=timeline_logs)
                            return {"ok": False, "step": "captcha", "error": f"❌ Không vượt qua được bước xác thực bảo mật: {gate_info}", "should_refund": True}

                        # LOG 3: Nhập mã OTP
                        masked_p = phone_num[:4] + "***" + phone_num[-3:] if len(phone_num) >= 7 else phone_num
                        if provided_phone:
                            await notify(
                                f"📩 {prefix}Shopee đã gửi mã OTP về số {phone_num}!\n"
                                f"👉 Soạn: OTP <mã> trong 90s để hoàn tất.",
                                "3/4 Nhập mã OTP",
                                send_to_user=True
                            )
                            from app.services.task_manager import request_user_otp, wait_for_user_otp
                            request_user_otp(zalo_user_id)
                            loop = asyncio.get_running_loop()
                            otp_code = await loop.run_in_executor(None, wait_for_user_otp, zalo_user_id, 90)
                            if not otp_code:
                                await browser.close()
                                self._update_db_log(record_id, status="failed", step_failed="otp", error_msg="Quá thời gian chờ nhập OTP (90s)", logs=timeline_logs)
                                return {"ok": False, "step": "otp", "error": "❌ Quá thời gian chờ nhập mã OTP từ bạn (90 giây).", "should_refund": True}
                        else:
                            await notify(
                                f"📩 {prefix}Đang chờ nhận mã OTP ({masked_p})...\n"
                                f"⚠️ Lưu ý: Đã thuê số, nếu soạn STOP hệ thống sẽ không hoàn phí thuê số của nick này.",
                                "3/4 Chờ OTP"
                            )
                            ok_otp, otp_code, otp_msg = self.viotp.poll_otp(req_id, timeout_seconds=150, interval=4)

                            if not ok_otp or not otp_code:
                                log_step(f"Đầu số {phone_num} không nhận được OTP: {otp_msg}")
                                await browser.close()
                                if phone_try < max_phone_tries:
                                    continue
                                self._update_db_log(record_id, status="failed", step_failed="otp", error_msg=otp_msg, logs=timeline_logs)
                                return {"ok": False, "step": "otp", "error": f"❌ Hệ thống đã thử {max_phone_tries} đầu số liên tiếp nhưng chưa nhận được mã xác thực OTP từ tổng đài.", "should_refund": True}

                        # ĐÁNH DẤU ĐÃ NHẬN OTP THÀNH CÔNG -> KHÔNG CHO PHÉP DÙNG LỆNH STOP
                        if zalo_user_id:
                            from app.services.task_manager import set_user_task_otp_received
                            set_user_task_otp_received(str(zalo_user_id))

                        # LOG 4: Tạo tài khoản
                        await notify(f"🔑 {prefix}Đang tạo tài khoản & lưu dữ liệu...", "4/4 Tạo tài khoản")

                        # Điền OTP vào 6 ô input
                        try:
                            await page.locator(
                                "input[class*='otp'], input[class*='pin'], input[maxlength='1'], input[autocomplete*='one-time-code'], input[type='tel'], input[placeholder*='mã']"
                            ).first.wait_for(state="visible", timeout=15000)
                        except Exception:
                            pass

                        otp_inputs = page.locator(".shopee-pin-input input, input[class*='pin-input'], input[class*='otp'], input[maxlength='1'], input[autocomplete*='one-time-code']")
                        count_inputs = await otp_inputs.count()
                        filled_ok = False
                        if count_inputs >= 6:
                            try:
                                first_box = otp_inputs.first
                                await first_box.click()
                                await asyncio.sleep(0.3)
                                await page.keyboard.type(otp_code[:6], delay=120)
                                await asyncio.sleep(0.5)
                                if await otp_inputs.nth(5).input_value():
                                    filled_ok = True
                            except Exception:
                                pass
                            if not filled_ok:
                                for idx_digit, digit in enumerate(otp_code[:6]):
                                    try:
                                        await otp_inputs.nth(idx_digit).fill(digit)
                                        await asyncio.sleep(0.08)
                                    except Exception:
                                        pass
                        else:
                            single_otp = page.locator("input[type='number'], input[type='tel'], input[placeholder*='mã'], input[name='otp'], input[name='code']").first
                            if await single_otp.count() > 0:
                                await single_otp.click()
                                await single_otp.fill(otp_code)

                        await asyncio.sleep(1.5)

                        # Bấm xác nhận OTP
                        btn_verify = page.locator("button:has-text('Xác nhận'), button:has-text('XÁC NHẬN'), button:has-text('NEXT'), button:has-text('Tiếp theo')").first
                        if await btn_verify.count() > 0 and await btn_verify.is_enabled():
                            await btn_verify.click()
                            await asyncio.sleep(2.5)

                        # BƯỚC 6: Xử lý Reclaim Phone Number (nếu số đã đăng ký) hoặc đặt mật khẩu (nếu số sạch)
                        is_reclaimed = 0
                        found_target_stage = False

                        for check_step in range(16):
                            if zalo_user_id and is_stop_requested(zalo_user_id):
                                await browser.close()
                                return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP).", "should_refund": True}

                            # 1. Kiểm tra màn hình "Number already registered" / "Số điện thoại đã được đăng ký"
                            body_txt = await page.evaluate("() => document.body ? document.body.innerText : ''")
                            is_registered_screen = (
                                "Number already registered" in body_txt or 
                                "already registered" in body_txt or 
                                "Số điện thoại đã được đăng ký" in body_txt or 
                                "Reclaim Phone Number" in body_txt or
                                "Lấy lại số điện thoại" in body_txt
                            )

                            if is_registered_screen:
                                is_reclaimed = 1
                                logger.info("🔄 Phát hiện màn hình đã đăng ký! Tiến hành click 'Reclaim Phone Number'...")

                                # Thử click selector
                                clicked_reclaim = False
                                for rec_sel in [
                                    "a:has-text('Reclaim Phone Number')",
                                    "button:has-text('Reclaim Phone Number')",
                                    "div:has-text('Reclaim Phone Number')",
                                    ":text('Reclaim Phone Number')",
                                    "a:has-text('Lấy lại số điện thoại')",
                                    "button:has-text('Lấy lại số điện thoại')",
                                    ":text('Lấy lại số điện thoại')",
                                    ":text('tiếp tục đăng ký')"
                                ]:
                                    rec_loc = page.locator(rec_sel).last
                                    if await rec_loc.count() > 0 and await rec_loc.is_visible():
                                        try:
                                            b_box = await rec_loc.bounding_box()
                                            if b_box:
                                                await page.mouse.click(b_box["x"] + b_box["width"] / 2, b_box["y"] + b_box["height"] / 2)
                                            else:
                                                await rec_loc.click()
                                            clicked_reclaim = True
                                            logger.info("✅ Đã click Reclaim qua selector: %s", rec_sel)
                                            break
                                        except Exception:
                                            pass

                                if not clicked_reclaim:
                                    # Fallback click qua DOM
                                    clicked_reclaim = await page.evaluate('''() => {
                                        let all = Array.from(document.querySelectorAll('a, button, div, span'));
                                        for (let el of all) {
                                            let t = (el.innerText || '').trim().toLowerCase();
                                            if (t === 'reclaim phone number' || t === 'lấy lại số điện thoại' || t.includes('reclaim phone') || t.includes('tiếp tục đăng ký')) {
                                                el.click();
                                                return true;
                                            }
                                        }
                                        return false;
                                    }''')
                                    if clicked_reclaim:
                                        logger.info("✅ Đã click Reclaim qua DOM evaluate!")

                                await asyncio.sleep(3)
                                found_target_stage = True
                                break

                            # 2. Kiểm tra nếu là số sạch (đã vào thẳng màn hình Set your password / Thiết lập mật khẩu)
                            pwd_check = page.locator("input[type='password'], input[placeholder*='Password'], input[placeholder*='password'], input[placeholder*='Mật khẩu'], input[name='newPassword']").first
                            if await pwd_check.count() > 0 and await pwd_check.is_visible():
                                logger.info("✨ Số sạch hợp lệ! Đang chuyển thẳng sang thiết lập mật khẩu mới...")
                                found_target_stage = True
                                break

                            await asyncio.sleep(1)

                        # Chờ màn hình Set your password xuất hiện (tối đa 15s)
                        pwd_input = page.locator(
                            "input[type='password'], input[placeholder*='Password'], input[placeholder*='password'], input[placeholder*='Mật khẩu'], input[name='newPassword']"
                        ).first
                        try:
                            await pwd_input.wait_for(state="visible", timeout=15000)
                        except Exception:
                            logger.warning("Không thấy ô nhập password sau 15s, kiểm tra lại DOM...")

                        # Đặt Mật Khẩu ngẫu nhiên thỏa mãn chính sách bảo mật của Shopee
                        logger.info("🔑 Đang nhập mật khẩu tài khoản mới: %s", target_password)

                        try:
                            await pwd_input.click()
                            await pwd_input.fill("")
                            for ch in target_password:
                                await pwd_input.type(ch, delay=random.randint(40, 80))
                                await asyncio.sleep(random.uniform(0.02, 0.04))
                        except Exception as ex_pwd:
                            logger.warning("Lỗi type password: %s, dùng fallback evaluate", ex_pwd)
                            await page.evaluate('''(val) => {
                                let inp = document.querySelector("input[type='password'], input[placeholder*='Password'], input[placeholder*='Mật khẩu']");
                                if (inp) {
                                    inp.value = val;
                                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                            }''', target_password)

                        await asyncio.sleep(1.5)

                        # Bấm nút SIGN UP / ĐĂNG KÝ
                        btn_signup = page.locator(
                            "button:has-text('SIGN UP'), button:has-text('Sign Up'), button:has-text('ĐĂNG KÝ'), button:has-text('Đăng ký')"
                        ).first
                        try:
                            if await btn_signup.count() > 0 and await btn_signup.is_visible():
                                await btn_signup.click()
                                logger.info("👉 Đã bấm nút SIGN UP / ĐĂNG KÝ!")
                        except Exception:
                            await page.evaluate('''() => {
                                let btns = Array.from(document.querySelectorAll('button'));
                                for (let b of btns) {
                                    let t = (b.innerText || '').trim().toUpperCase();
                                    if (t === 'SIGN UP' || t === 'ĐĂNG KÝ') {
                                        b.click();
                                        return true;
                                    }
                                }
                                return false;
                            }''')

                        await asyncio.sleep(4.5)

                        # Thu hoạch Cookies
                        logger.info("🍪 Đăng ký hoàn tất! Đang lưu thông tin phiên làm việc...")
                        cookies = await context.cookies()
                        cookie_dict = {c["name"]: c["value"] for c in cookies}
                        spc_st = cookie_dict.get("SPC_ST", "")
                        spc_f = cookie_dict.get("SPC_F", "")
                        spc_u = cookie_dict.get("SPC_U", "")
                        cookie_str = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])

                        username = f"user_{phone_num[-6:]}"
                        try:
                            user_elem = page.locator(".navbar__username, [class*='username'], .shopee-avatar__name").first
                            if await user_elem.count() > 0:
                                txt = (await user_elem.inner_text()).strip()
                                if txt:
                                    username = txt
                        except Exception:
                            pass

                        extracted_account = {
                            "ok": True,
                            "phone": phone_num,
                            "username": username,
                            "password": target_password,
                            "spc_st": spc_st,
                            "spc_f": spc_f,
                            "spc_u": spc_u,
                            "cookie_str": cookie_str,
                            "is_reclaimed": is_reclaimed,
                            "proxy_used": proxy_url,
                            "external_ip": external_ip
                        }

                        self._update_db_log(
                            record_id,
                            status="success",
                            username=username,
                            password=target_password,
                            spc_st=spc_st,
                            spc_f=spc_f,
                            spc_u=spc_u,
                            cookie_full=cookie_str,
                            is_reclaimed=is_reclaimed,
                            logs=timeline_logs
                        )

                        await browser.close()
                        return extracted_account

                    except Exception as loop_err:
                        log_step(f"Lỗi lượt thử {phone_try}: {loop_err}")
                        await browser.close()
                        if phone_try < max_phone_tries:
                            continue
                        self._update_db_log(record_id, status="failed", step_failed="runtime", error_msg=str(loop_err), logs=timeline_logs)
                        if provided_phone:
                            return {"ok": False, "step": "runtime", "error": f"❌ Chưa hoàn tất được bước xác thực đăng ký cho số {provided_phone}. Vui lòng thử lại!", "should_refund": True}
                        return {"ok": False, "step": "runtime", "error": "❌ Chưa hoàn tất được bước xác thực đăng ký. Vui lòng thử lại sau!", "should_refund": True}

            if provided_phone:
                return {"ok": False, "step": "exhausted", "error": f"❌ Quá trình xác thực số {provided_phone} chưa thành công. Vui lòng thử lại!", "should_refund": True}
            return {"ok": False, "step": "exhausted", "error": "❌ Hệ thống chưa nhận được mã xác thực OTP. Đã hoàn tiền vào ví của bạn.", "should_refund": True}

    def _update_db_log(self, record_id: Optional[int], **kwargs):
        """Cập nhật dữ liệu nhật ký vào CSDL một cách an toàn."""
        if not record_id:
            return
        db = SessionLocal()
        try:
            rec = db.query(ShopeeRegLog).filter(ShopeeRegLog.id == record_id).first()
            if rec:
                for k, v in kwargs.items():
                    if k == "logs":
                        rec.logs_detail = "\n".join(v) if isinstance(v, list) else str(v)
                    elif hasattr(rec, k):
                        setattr(rec, k, v)
                db.commit()
        except Exception as e:
            logger.error(f"Lỗi cập nhật CSDL ShopeeRegLog: {e}")
        finally:
            db.close()
