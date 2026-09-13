"""
Kịch bản chạy trực quan (NO-HEADLESS) kiểm thử đăng ký tài khoản Shopee:
- Cấu hình chuẩn Shopee ViOTP: Service ID = 4 (Shopee / ShopeePay).
- Bật Chrome giao diện thật (headless=False) trực tiếp trên màn hình Windows.
- Tự động nhập số, giải Captcha trượt bằng OpenCV, hứng OTP và đặt mật khẩu.
"""

import sys
import os
import asyncio
import logging

# Thêm đường dẫn project
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.viotp_service import ViOTPService
from app.services.captcha_solver_service import solve_shopee_slider_captcha
from app.services.shopee_reg_service import generate_secure_password
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
logger = logging.getLogger("VisualDemo")


async def run_visual_registration():
    print("=" * 60)
    print("🚀 BẮT ĐẦU CHẠY THỰC TẾ TRỰC QUAN (HEADLESS = FALSE) 🚀")
    print("=" * 60)

    # 1. Thuê số ViOTP Shopee (Service ID: 4)
    viotp = ViOTPService("12bed0f6f69d47e2b0985ea7a3452839")
    ok_bal, bal, bal_msg = viotp.get_balance()
    print(f"💰 Số dư ViOTP: {bal:,} VNĐ ({bal_msg})")

    print("\n📱 Đang yêu cầu cấp số Shopee (Service ID: 4 - Shopee/ShopeePay)...")
    ok_phone, phone, req_id, phone_msg = viotp.request_phone_number(service_id=4)

    if not ok_phone or not phone:
        print(f"❌ Không thể thuê số: {phone_msg}")
        return

    print(f"✅ ĐÃ NHẬN SỐ SHOPEE: {phone} (Request ID: {req_id})")

    # 2. Bật Chrome giao diện trực quan (headless=False)
    print("\n🌐 Đang mở trình duyệt Google Chrome (NO-HEADLESS)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )

        context = await browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh"
        )

        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page = await context.new_page()

        print("🔍 Đang tải trang Đăng ký Shopee: https://shopee.vn/buyer/signup ...")
        await page.goto("https://shopee.vn/buyer/signup", timeout=45000, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # 3. Nhập số điện thoại
        print(f"⌨️ Đang điền số điện thoại {phone} vào ô đăng ký...")
        phone_input = page.locator("input[name='phone'], input[placeholder*='Số điện thoại'], input[autocomplete='tel']").first
        await phone_input.wait_for(state="visible", timeout=15000)
        await phone_input.click()

        for ch in phone:
            await phone_input.type(ch, delay=80)
            await asyncio.sleep(0.05)

        await asyncio.sleep(1)

        # Bấm Tiếp theo
        print("👉 Bấm nút TIẾP THEO...")
        next_btn = page.locator("button:has-text('TIẾP THEO'), button:has-text('Tiếp theo'), button:has-text('NEXT')").first
        await next_btn.click()
        await asyncio.sleep(3)

        # Kiểm tra lỗi số
        err_msg = ""
        try:
            err_loc = page.locator(".shopee-input-helper-text, [class*='error-message']").first
            if await err_loc.count() > 0 and await err_loc.is_visible():
                err_msg = (await err_loc.inner_text()).strip()
        except Exception:
            pass

        if err_msg:
            print(f"⚠️ Shopee báo lỗi số: {err_msg}")
            print("👉 Tạm dừng 15s để mày quan sát giao diện trên màn hình...")
            await asyncio.sleep(15)
            await browser.close()
            return

        # 4. Kiểm tra Captcha trượt
        print("🧩 Đang kiểm tra xem có Captcha trượt không...")
        slider_exists = await page.locator("div.HrMY5p, div.Mcqxdt, canvas.BX1JD-, div[class*='F0XJ1W']").count() > 0

        if slider_exists:
            print("⚡ Phát hiện Captcha trượt! Tiến hành giải tự động bằng OpenCV...")
            solved = await solve_shopee_slider_captcha(page, max_attempts=3)
            if solved:
                print("🎉 GIẢI CAPTCHA THÀNH CÔNG RỰC RỠ!")
            else:
                print("❌ Không vượt qua Captcha sau 3 lần.")
                await asyncio.sleep(10)
                await browser.close()
                return
        else:
            print("✅ Không có Captcha hoặc Captcha được bỏ qua an toàn!")

        # 5. Hứng mã OTP từ ViOTP Shopee
        print(f"\n📩 Đang chờ mã OTP gửi về cho số {phone} (Tối đa 120s, kiểm tra mỗi 4s)...")
        ok_otp, otp_code, otp_msg = viotp.poll_otp(req_id, timeout_seconds=120, interval=4)

        if not ok_otp or not otp_code:
            print(f"❌ {otp_msg}")
            print("👉 Tạm dừng 15s để mày quan sát...")
            await asyncio.sleep(15)
            await browser.close()
            return

        print(f"🎉 ĐÃ NHẬN ĐƯỢC MÃ OTP: {otp_code}", flush=True)

        # 6. Điền mã OTP
        print(f"⌨️ Đang tự động điền mã OTP {otp_code}...")
        otp_inputs = page.locator("input[class*='otp'], input[maxlength='1']")
        cnt = await otp_inputs.count()
        if cnt >= 6:
            for i, digit in enumerate(otp_code[:6]):
                await otp_inputs.nth(i).fill(digit)
                await asyncio.sleep(0.1)
        else:
            single = page.locator("input[type='number'], input[placeholder*='mã'], input[name='otp']").first
            if await single.count() > 0:
                await single.fill(otp_code)

        await asyncio.sleep(2)
        btn_verify = page.locator("button:has-text('Xác nhận'), button:has-text('Tiếp theo')").first
        if await btn_verify.count() > 0 and await btn_verify.is_enabled():
            await btn_verify.click()

        await asyncio.sleep(3)

        # 7. Đặt mật khẩu
        pwd = generate_secure_password(12)
        print(f"🔑 Đang thiết lập mật khẩu mới: {pwd}")
        pwd_input = page.locator("input[type='password'], input[placeholder*='Mật khẩu']").first
        if await pwd_input.count() > 0 and await pwd_input.is_visible():
            await pwd_input.fill(pwd)
            await asyncio.sleep(1)
            btn_signup = page.locator("button:has-text('SIGN UP'), button:has-text('ĐĂNG KÝ'), button:has-text('Đăng ký')").first
            if await btn_signup.count() > 0:
                await btn_signup.click()
                print("🚀 ĐÃ BẤM ĐĂNG KÝ HOÀN TẤT!")

        await asyncio.sleep(5)
        cookies = await context.cookies()
        spc_st = next((c["value"] for c in cookies if c["name"] == "SPC_ST"), "None")
        print(f"\n🍪 SPC_ST thu được: {spc_st[:30]}...")
        print("🎉 QUY TRÌNH ĐĂNG KÝ HOÀN TẤT THÀNH CÔNG! Giữ màn hình 15s cho bạn xem.")
        await asyncio.sleep(15)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(run_visual_registration())
