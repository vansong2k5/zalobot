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

        async def notify(msg: str, step_label: str = "", send_to_user: bool = False):
            full_msg = f"{prefix}{msg}".strip()
            log_step(full_msg)
            if step_label and zalo_user_id:
                update_user_task_progress(zalo_user_id, f"{prefix}{step_label}".strip())
            # Chỉ gửi tin nhắn đến Zalo người dùng khi thật sự cần thiết (tránh spam log)
            if send_to_user and progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(full_msg)
                else:
                    progress_callback(full_msg)

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
            # BƯỚC 1: Validate Proxy
            # -------------------------------------------------------------
            await notify("🔍 [Giai đoạn 1/6] Đang kết nối đường truyền mạng riêng bảo mật...", "1/6 Kết nối mạng")
            is_p_valid, p_msg, p_info = validate_proxy_connection(user_proxy)
            if not is_p_valid:
                self._update_db_log(record_id, status="failed", step_failed="proxy", error_msg=p_msg, logs=timeline_logs)
                return {"ok": False, "step": "proxy", "error": f"❌ Lỗi đường truyền Proxy: {p_msg}", "should_refund": True}

            proxy_url = p_info["proxy_url"]
            external_ip = p_info["ip"]
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
            # VÒNG LẶP THỬ TỐI ĐA 3 ĐẦU SỐ (Nếu số trước không về OTP sau 150s)
            # -------------------------------------------------------------
            for phone_try in range(1, max_phone_tries + 1):
                if zalo_user_id and is_stop_requested(zalo_user_id):
                    return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP).", "should_refund": True}

                if phone_try > 1:
                    await notify(f"🔄 [Lần thử {phone_try}/{max_phone_tries}] Đầu số phản hồi chậm sau 150s, đang tự động kết nối đầu số khác...", f"Thử số {phone_try}")
                    await asyncio.sleep(2)

                # BƯỚC 2: Cấp số điện thoại (Bảo mật: Không show số tạm thời lên Zalo của khách)
                req_id = None
                if provided_phone:
                    phone_num = provided_phone
                    self._update_db_log(record_id, phone_number=phone_num)
                    await notify(f"📱 [Giai đoạn 2/6] Sử dụng số điện thoại của bạn: {phone_num}", "2/6 SĐT của bạn")
                else:
                    await notify("Đang kết nối tổng đài và xác thực tài khoản...", "2/6 Kết nối")
                    ok_phone, phone_num, req_id, phone_msg = self.viotp.request_shopee_phone(services=[20, 7, 527])
                    if not ok_phone or not phone_num:
                        log_step(f"Lỗi cấp số lần {phone_try}: {phone_msg}")
                        if phone_try < max_phone_tries:
                            await asyncio.sleep(3)
                            continue
                        self._update_db_log(record_id, status="failed", step_failed="phone", error_msg=phone_msg, logs=timeline_logs)
                        return {"ok": False, "step": "phone", "error": "❌ Hệ thống tổng đài tạm thời hết đầu số. Vui lòng thử lại sau ít phút!", "should_refund": True}

                    self._update_db_log(record_id, phone_number=phone_num)
                    log_step(f"Đã cấp số an toàn: {phone_num}")

                from app.services.viotp_service import normalize_vietnamese_phone
                phone_num = normalize_vietnamese_phone(phone_num)

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

                        await notify("🌐 [Giai đoạn 3/6] Đang truy cập cổng đăng ký Shopee...", "3/6 Mở Shopee")
                        await page.goto("https://shopee.vn/buyer/signup", timeout=50000, wait_until="domcontentloaded")
                        await asyncio.sleep(2)

                        # Nhập số điện thoại (Bảo mật: Ẩn số thô trên thông báo)
                        await notify("🌐 [Giai đoạn 3/6] Đang thiết lập phiên đăng ký bảo mật...", "3/6 Nhập thông tin")
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

                        # BƯỚC 4: Toàn quyền xử lý tất cả các cổng xác thực (Captcha trượt, Popup Zalo, Cuộc gọi, SMS)
                        await notify("🛡️ [Giai đoạn 4/6] Đang tự động xử lý xác thực bảo mật tài khoản...", "4/6 Xác thực bảo mật")
                        gate_ok, gate_info = await handle_shopee_verification_gate(
                            page,
                            max_wait_seconds=65,
                            notify_callback=lambda msg: notify(f"🛡️ [Giai đoạn 4/6] {msg}", "4/6 Xác thực")
                        )
                        if not gate_ok:
                            log_step(f"Không vượt qua cổng xác thực ở đầu số {phone_num}: {gate_info}")
                            await browser.close()
                            if phone_try < max_phone_tries:
                                continue
                            self._update_db_log(record_id, status="failed", step_failed="captcha", error_msg=gate_info, logs=timeline_logs)
                            return {"ok": False, "step": "captcha", "error": f"❌ Không vượt qua được bước xác thực bảo mật: {gate_info}", "should_refund": True}

                        await notify("✅ [Giai đoạn 4/6] Đã thông qua cổng xác thực bảo mật! Hệ thống đang phát mã...", "4/6 Xác thực OK")

                        # BƯỚC 5: Hứng và Điền mã OTP
                        if provided_phone:
                            await notify(
                                f"📩 Shopee đã gửi mã OTP về số {phone_num}!\n"
                                f"👉 Soạn: OTP <mã> trong 90s để hoàn tất.",
                                "5/6 Chờ nhập OTP",
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
                            # Luồng tự động: Lắng nghe mã OTP 150s (Ẩn số và OTP thô trên Zalo)
                            await notify("📩 [Giai đoạn 5/6] Đang lắng nghe mã xác thực từ hệ thống (tối đa 150s)...", "5/6 Chờ OTP")
                            ok_otp, otp_code, otp_msg = self.viotp.poll_otp(req_id, timeout_seconds=150, interval=4)

                            if not ok_otp or not otp_code:
                                log_step(f"Đầu số {phone_num} không nhận được OTP: {otp_msg}")
                                await browser.close()
                                if phone_try < max_phone_tries:
                                    # Tiếp tục vòng lặp sang số tiếp theo
                                    continue
                                self._update_db_log(record_id, status="failed", step_failed="otp", error_msg=otp_msg, logs=timeline_logs)
                                return {"ok": False, "step": "otp", "error": f"❌ Hệ thống đã thử {max_phone_tries} đầu số liên tiếp nhưng chưa nhận được mã xác thực OTP từ tổng đài.", "should_refund": True}

                            await notify("✅ [Giai đoạn 5/6] Đã nhận mã xác thực an toàn! Đang hoàn tất tài khoản...", "5/6 Điền OTP")

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

                        # BƯỚC 6: Xử lý reclaim hoặc đặt mật khẩu
                        is_reclaimed = 0
                        found_target_stage = False
                        for _ in range(12):
                            if zalo_user_id and is_stop_requested(zalo_user_id):
                                await browser.close()
                                return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP)."}

                            reclaim_link = page.locator("a:has-text('Reclaim Phone Number'), button:has-text('Reclaim Phone Number'), :text('Reclaim Phone Number'), :text('tiếp tục đăng ký')").first
                            if await reclaim_link.count() > 0 and await reclaim_link.is_visible():
                                is_reclaimed = 1
                                await notify("🔄 [Giai đoạn 6/6] Đang làm sạch và cấp mới tài khoản cho số điện thoại...", "6/6 Làm sạch tài khoản")
                                await reclaim_link.click()
                                await asyncio.sleep(3)
                                found_target_stage = True
                                break

                            pwd_input_check = page.locator("input[type='password'], input[name='newPassword'], input[placeholder*='Mật khẩu']").first
                            if await pwd_input_check.count() > 0 and await pwd_input_check.is_visible():
                                await notify("✨ [Giai đoạn 6/6] Đầu số hợp lệ! Đang chuyển sang thiết lập tài khoản...", "6/6 Tạo pass mới")
                                found_target_stage = True
                                break

                            await asyncio.sleep(1)

                        # Đặt Mật Khẩu
                        await notify("🔑 [Giai đoạn 6/6] Đang thiết lập mật khẩu bảo mật cho tài khoản...", "6/6 Nhập mật khẩu")
                        pwd_input = page.locator("input[type='password'], input[name='newPassword'], input[placeholder*='Mật khẩu']").first
                        await pwd_input.wait_for(timeout=15000)
                        await pwd_input.click()

                        for ch in target_password:
                            await pwd_input.type(ch, delay=random.randint(40, 90))

                        await asyncio.sleep(1)

                        btn_signup = page.locator("button:has-text('SIGN UP'), button:has-text('ĐĂNG KÝ'), button:has-text('Đăng ký')").first
                        await btn_signup.click()
                        await asyncio.sleep(4)

                        # Thu hoạch Cookies
                        await notify("🍪 [Giai đoạn 6/6] Đăng ký hoàn tất! Đang lưu thông tin phiên làm việc...", "6/6 Lưu Cookie")
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
