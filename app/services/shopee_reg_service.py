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
from app.models import ShopeeRegLog
from app.services.proxy_validator import validate_proxy_connection
from app.services.viotp_service import ViOTPService
from app.services.captcha_solver_service import solve_shopee_slider_captcha

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

# Giới hạn tối đa 3 tác vụ Playwright chạy song song cùng lúc để bảo vệ RAM/CPU VPS
MAX_CONCURRENT_REG = 3
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
        user_proxy: str,
        zalo_user_id: str = "",
        service_id: int = 4,
        provided_phone: Optional[str] = None,
        custom_password: Optional[str] = None,
        acc_prefix: str = "",
        headless: Optional[bool] = None,
        progress_callback: Optional[Callable[[str], Any]] = None
    ) -> Dict[str, Any]:
        """
        Quy trình đăng ký tài khoản Shopee tự động:
        - Hỗ trợ số điện thoại riêng của khách hoặc thuê số tự động.
        - Giải Captcha trượt thử 3 lần kèm đổi hình câu đố.
        - Bắt OTP từ khách hoặc từ hệ thống nhà mạng.
        - Đặt mật khẩu và thu hoạch Cookie SPC_ST.
        """
        from app.services.task_manager import is_stop_requested, update_user_task_progress

        prefix = f"{acc_prefix} " if acc_prefix else ""
        timeline_logs = []

        def log_step(msg: str):
            timestamp = datetime.now().strftime("%H:%M:%S")
            entry = f"[{timestamp}] {msg}"
            timeline_logs.append(entry)
            logger.info(f"[{zalo_user_id or 'SYSTEM'}] {msg}")

        async def notify(msg: str, step_label: str = ""):
            full_msg = f"{prefix}{msg}"
            log_step(full_msg)
            if step_label and zalo_user_id:
                update_user_task_progress(zalo_user_id, f"{prefix}{step_label}".strip())
            if progress_callback:
                if asyncio.iscoroutinefunction(progress_callback):
                    await progress_callback(full_msg)
                else:
                    progress_callback(full_msg)

        # Kiểm tra trước nếu user đã bấm STOP
        if zalo_user_id and is_stop_requested(zalo_user_id):
            return {"ok": False, "step": "stopped", "error": "Tiến trình đã được dừng theo yêu cầu của bạn.", "should_refund": True}

        # Tạo bản ghi log trong CSDL ban đầu
        db = SessionLocal()
        reg_record = ShopeeRegLog(
            zalo_user_id=zalo_user_id,
            proxy_used=user_proxy,
            status="in_progress",
            created_at=datetime.utcnow()
        )
        try:
            db.add(reg_record)
            db.commit()
            db.refresh(reg_record)
            record_id = reg_record.id
        except Exception as e:
            logger.error(f"Lỗi tạo record log DB: {e}")
            record_id = None
        finally:
            db.close()

        # Áp dụng Semaphore đa luồng an toàn
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
            latency = p_info["latency_ms"]
            self._update_db_log(record_id, external_ip=external_ip)
            await notify(f"✅ [Giai đoạn 1/6] Kết nối mạng ổn định! (IP: {external_ip} | Delay: {latency}ms)", "1/6 Mạng OK")

            if zalo_user_id and is_stop_requested(zalo_user_id):
                return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP).", "should_refund": True}

            # -------------------------------------------------------------
            # BƯỚC 2: Cấp số điện thoại (Dùng số của khách hoặc thuê tự động)
            # -------------------------------------------------------------
            req_id = None
            if provided_phone:
                phone_num = provided_phone
                self._update_db_log(record_id, phone_number=phone_num)
                await notify(f"📱 [Giai đoạn 2/6] Sử dụng số điện thoại của bạn: {phone_num}", "2/6 SĐT của bạn")
            else:
                await notify("📱 [Giai đoạn 2/6] Đang kết nối nhà mạng để cấp số điện thoại...", "2/6 Cấp số điện thoại")
                ok_phone, phone_num, req_id, phone_msg = self.viotp.request_phone_number(service_id=service_id)
                if not ok_phone or not phone_num:
                    self._update_db_log(record_id, status="failed", step_failed="phone", error_msg=phone_msg, logs=timeline_logs)
                    return {"ok": False, "step": "phone", "error": f"❌ Lỗi cấp số: {phone_msg}", "should_refund": True}

                self._update_db_log(record_id, phone_number=phone_num)
                await notify(f"✅ [Giai đoạn 2/6] Đã cấp số điện thoại: {phone_num}", "2/6 Cấp số OK")

            if zalo_user_id and is_stop_requested(zalo_user_id):
                return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP).", "should_refund": True}

            # -------------------------------------------------------------
            # BƯỚC 3 & 4: Mở cổng đăng ký Shopee, nhập số & xác thực
            # -------------------------------------------------------------
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

            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(**launch_kwargs)
                except Exception:
                    # Fallback nếu máy không có Chrome
                    launch_kwargs.pop("channel", None)
                    browser = await p.chromium.launch(**launch_kwargs)

                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768},
                    locale="vi-VN",
                    timezone_id="Asia/Ho_Chi_Minh"
                )

                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                """)

                page = await context.new_page()

                try:
                    if zalo_user_id and is_stop_requested(zalo_user_id):
                        await browser.close()
                        return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP).", "should_refund": True}

                    await notify("🌐 [Giai đoạn 3/6] Đang truy cập cổng đăng ký Shopee...", "3/6 Mở Shopee")
                    await page.goto("https://shopee.vn/buyer/signup", timeout=50000, wait_until="domcontentloaded")
                    await asyncio.sleep(2)

                    if zalo_user_id and is_stop_requested(zalo_user_id):
                        await browser.close()
                        return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP).", "should_refund": True}

                    from app.services.viotp_service import normalize_vietnamese_phone
                    phone_num = normalize_vietnamese_phone(phone_num)

                    # Nhập số điện thoại
                    await notify(f"⌨️ [Giai đoạn 3/6] Đang nhập thông tin số điện thoại {phone_num}...", "3/6 Nhập SĐT")
                    phone_input = page.locator("input[name='phone'], input[placeholder*='Số điện thoại'], input[autocomplete='tel']").first
                    await phone_input.wait_for(state="visible", timeout=35000)
                    await phone_input.click()
                    await phone_input.fill("")

                    for ch in phone_num:
                        await phone_input.type(ch, delay=random.randint(60, 140))
                        await asyncio.sleep(random.uniform(0.04, 0.09))

                    await asyncio.sleep(1)

                    # Bấm NEXT (kiểm tra trạng thái enabled trước)
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
                        await browser.close()
                        self._update_db_log(record_id, status="failed", step_failed="phone_reject", error_msg=err_text, logs=timeline_logs)
                        return {"ok": False, "step": "phone_reject", "error": f"❌ Shopee thông báo: {err_text}", "should_refund": True}

                    await next_btn.click()
                    await asyncio.sleep(3)

                    # Kiểm tra xem Shopee có báo lỗi số điện thoại không (ví dụ: đã đăng ký hoặc không hợp lệ)
                    err_text = ""
                    try:
                        error_loc = page.locator(".shopee-input-helper-text, [class*='error-message'], [class*='errorMessage']").first
                        if await error_loc.count() > 0 and await error_loc.is_visible():
                            err_text = (await error_loc.inner_text()).strip()
                    except Exception:
                        pass

                    if err_text:
                        await browser.close()
                        self._update_db_log(record_id, status="failed", step_failed="phone_reject", error_msg=err_text, logs=timeline_logs)
                        return {"ok": False, "step": "phone_reject", "error": f"❌ Shopee thông báo: {err_text}", "should_refund": True}

                    # -------------------------------------------------------------
                    # BƯỚC 4: Kiểm tra và giải xác thực Captcha (Cố gắng thử tối đa 3 lần)
                    # -------------------------------------------------------------
                    slider_exists = await page.locator("div.HrMY5p, div.Mcqxdt, canvas.BX1JD-, div[class*='F0XJ1W']").count() > 0
                    if slider_exists:
                        await notify("🧩 [Giai đoạn 4/6] Đang tự động xử lý mã xác thực an toàn (thử tối đa 3 lần)...", "4/6 Xử lý xác thực")
                        solved = await solve_shopee_slider_captcha(page, max_attempts=3)
                        if not solved:
                            await browser.close()
                            self._update_db_log(record_id, status="failed", step_failed="captcha", error_msg="Không vượt qua xác thực bảo mật sau 3 lần thử", logs=timeline_logs)
                            return {"ok": False, "step": "captcha", "error": "❌ Không vượt qua được bước xác thực bảo mật sau 3 lần thử.", "should_refund": True}
                        await notify("✅ [Giai đoạn 4/6] Đã vượt qua bước xác thực thành công!", "4/6 Xác thực OK")
                    else:
                        await notify("✅ [Giai đoạn 4/6] Bước xác thực bảo mật được thông qua an toàn.", "4/6 Xác thực OK")

                    if zalo_user_id and is_stop_requested(zalo_user_id):
                        await browser.close()
                        return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP).", "should_refund": True}

                    # -------------------------------------------------------------
                    # BƯỚC 5: Hứng và Điền mã OTP
                    # -------------------------------------------------------------
                    if provided_phone:
                        # Luồng dùng SĐT của chính người dùng: Chờ người dùng nhắn mã OTP qua Zalo
                        await notify(
                            f"📩 [Giai đoạn 5/6] Shopee đã gửi mã xác thực OTP về SĐT {phone_num}!\n"
                            f"👉 Vui lòng soạn: OTP <mã> (hoặc gõ luôn mã 6 số) trong vòng 90 giây để hoàn tất!",
                            "5/6 Chờ bạn nhập OTP"
                        )
                        from app.services.task_manager import request_user_otp, wait_for_user_otp
                        request_user_otp(zalo_user_id)

                        loop = asyncio.get_running_loop()
                        otp_code = await loop.run_in_executor(None, wait_for_user_otp, zalo_user_id, 90)

                        if not otp_code:
                            await browser.close()
                            self._update_db_log(record_id, status="failed", step_failed="otp", error_msg="Quá thời gian chờ nhập OTP (90s)", logs=timeline_logs)
                            return {"ok": False, "step": "otp", "error": "❌ Quá thời gian chờ nhập mã OTP từ bạn (90 giây).", "should_refund": True}

                        await notify(f"✅ [Giai đoạn 5/6] Đã nhận mã xác thực {otp_code}, đang hoàn tất...", "5/6 Điền OTP")
                    else:
                        # Luồng thuê SIM tự động
                        await notify(f"📩 [Giai đoạn 5/6] Đang chờ mã OTP xác nhận cho số {phone_num}...", "5/6 Chờ OTP")
                        ok_otp, otp_code, otp_msg = self.viotp.poll_otp(req_id, timeout_seconds=120, interval=5)

                        if not ok_otp or not otp_code:
                            await browser.close()
                            self._update_db_log(record_id, status="failed", step_failed="otp", error_msg=otp_msg, logs=timeline_logs)
                            return {"ok": False, "step": "otp", "error": f"❌ Lỗi xác thực OTP: {otp_msg}", "should_refund": True}

                        await notify(f"✅ [Giai đoạn 5/6] Đã nhận mã xác thực thành công, đang hoàn tất...", "5/6 Điền OTP")

                    # Điền OTP
                    otp_inputs = page.locator("input[class*='otp'], input[maxlength='1'], input[autocomplete*='one-time-code']")
                    count_inputs = await otp_inputs.count()

                    if count_inputs >= 6:
                        for idx_digit, digit in enumerate(otp_code[:6]):
                            await otp_inputs.nth(idx_digit).fill(digit)
                            await asyncio.sleep(0.1)
                    else:
                        single_otp = page.locator("input[type='number'], input[placeholder*='mã'], input[name='otp']").first
                        if await single_otp.count() > 0:
                            await single_otp.fill(otp_code)

                    await asyncio.sleep(1)

                    # Bấm xác nhận OTP nếu có nút
                    btn_verify = page.locator("button:has-text('Xác nhận'), button:has-text('NEXT'), button:has-text('Tiếp theo')").first
                    if await btn_verify.count() > 0 and await btn_verify.is_enabled():
                        await btn_verify.click()

                    await asyncio.sleep(2)

                    if zalo_user_id and is_stop_requested(zalo_user_id):
                        await browser.close()
                        return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP)."}

                    # -------------------------------------------------------------
                    # BƯỚC 6: XỬ LÝ LÀM SẠCH HOẶC TẠO MỚI & ĐẶT MẬT KHẨU
                    # -------------------------------------------------------------
                    is_reclaimed = 0
                    found_target_stage = False

                    for _ in range(12):
                        if zalo_user_id and is_stop_requested(zalo_user_id):
                            await browser.close()
                            return {"ok": False, "step": "stopped", "error": "Đã dừng tiến trình theo yêu cầu của bạn (Lệnh STOP)."}

                        # Kiểm tra xem có popup "Reclaim Phone Number"
                        reclaim_link = page.locator("a:has-text('Reclaim Phone Number'), button:has-text('Reclaim Phone Number'), :text('Reclaim Phone Number'), :text('tiếp tục đăng ký')").first
                        if await reclaim_link.count() > 0 and await reclaim_link.is_visible():
                            is_reclaimed = 1
                            await notify("🔄 [Giai đoạn 6/6] Đang làm sạch và cấp mới tài khoản cho số điện thoại...", "6/6 Làm sạch tài khoản")
                            await reclaim_link.click()
                            await asyncio.sleep(3)
                            found_target_stage = True
                            break

                        # Kiểm tra xem có chuyển thẳng vào màn hình "Set your password"
                        pwd_input_check = page.locator("input[type='password'], input[name='newPassword'], input[placeholder*='Mật khẩu']").first
                        if await pwd_input_check.count() > 0 and await pwd_input_check.is_visible():
                            await notify("✨ [Giai đoạn 6/6] Số mới hợp lệ! Đang chuyển sang thiết lập tài khoản...", "6/6 Tạo pass mới")
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

                    # Bấm nút SIGN UP / Đăng ký hoàn tất
                    btn_signup = page.locator("button:has-text('SIGN UP'), button:has-text('ĐĂNG KÝ'), button:has-text('Đăng ký')").first
                    await btn_signup.click()
                    await asyncio.sleep(4)

                    # Thu hoạch Session Cookie & Username
                    await notify("🍪 [Giai đoạn 6/6] Đăng ký hoàn tất! Đang lưu thông tin phiên làm việc...", "6/6 Lưu Cookie")
                    cookies = await context.cookies()
                    cookie_dict = {c["name"]: c["value"] for c in cookies}
                    spc_st = cookie_dict.get("SPC_ST", "")
                    spc_f = cookie_dict.get("SPC_F", "")
                    spc_u = cookie_dict.get("SPC_U", "")
                    cookie_str = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])

                    # Lấy username được sinh tự động
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

                    # Cập nhật thành công vào CSDL
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

                except Exception as e:
                    await browser.close()
                    self._update_db_log(record_id, status="failed", step_failed="runtime", error_msg=str(e), logs=timeline_logs)
                    return {"ok": False, "step": "runtime", "error": f"❌ Lỗi tiến trình đăng ký: {str(e)}"}

                await browser.close()

            return extracted_account

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
