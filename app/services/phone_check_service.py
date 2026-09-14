"""
Dịch vụ Kiểm Tra Số Điện Thoại Shopee & Trạng thái F02.
Engine: Playwright Headless (tự host, không phụ thuộc API ngoài).
"""

import re
import asyncio
import logging
import concurrent.futures
from typing import Optional, Dict, Any, List

from playwright.async_api import async_playwright
from .proxy_service import get_active_proxy

logger = logging.getLogger("PhoneCheckService")

_SHOPEE_PHONE_PROXY = "http://127.0.0.1:38888"


# ==============================================================================
# 1. TRÍCH XUẤT SỐ ĐIỆN THOẠI TỪ TIN NHẮN
# ==============================================================================

def extract_phone_number(text: str) -> Optional[str]:
    """
    Nhận diện số điện thoại Việt Nam hợp lệ trong tin nhắn.
    Hỗ trợ: 0987654321, 0987.654.321, +84987654321, 84987654321, 584237653 (9 số).
    """
    if not text:
        return None
    raw = text.strip()
    # Bỏ qua các lệnh bot khác
    if raw.upper().startswith(("SPX", "DH", "BUY", "ADDMAIL", "REG", "HELP", "MENU", "NAP", "SODU")):
        return None

    pattern = r"(?:(?:\+84|84|0)?[35789](?:[\s.\-]?\d){8})"
    match = re.search(pattern, raw)
    if not match:
        return None

    cleaned = re.sub(r"[\s.\-\(\)]", "", match.group(0))
    if cleaned.startswith("+84"):
        cleaned = "0" + cleaned[3:]
    elif cleaned.startswith("84") and len(cleaned) == 11:
        cleaned = "0" + cleaned[2:]
    elif not cleaned.startswith("0") and len(cleaned) == 9:
        cleaned = "0" + cleaned

    return cleaned if re.match(r"^0[35789]\d{8}$", cleaned) else None


# ==============================================================================
# 2. PLAYWRIGHT ENGINE - KIỂM TRA F02
# ==============================================================================

async def _check_phone_via_playwright(
    phone: str,
    proxy: str = _SHOPEE_PHONE_PROXY,
    timeout: int = 25000
) -> Dict[str, Any]:
    """
    Kiểm tra trạng thái SĐT Shopee qua Playwright headless stealth.
    Bắt chính xác mã lỗi F02 (error 9) vs Đầu số sạch (error 2).
    """
    clean_phone = re.sub(r"[^\d]", "", phone.strip())
    if clean_phone.startswith("84") and len(clean_phone) > 9:
        clean_phone = "0" + clean_phone[2:]
    elif not clean_phone.startswith("0") and len(clean_phone) == 9:
        clean_phone = "0" + clean_phone

    result: Dict[str, Any] = {
        "ok": True,
        "phone": clean_phone,
        "status": "Không xác định",
        "is_f02": False,
        "f02_recoverable": False,
        "raw_error": None
    }

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            context_opts: Dict[str, Any] = {
                "viewport": {"width": 1280, "height": 800},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                "locale": "vi-VN"
            }
            if proxy:
                context_opts["proxy"] = {"server": proxy}

            context = await browser.new_context(**context_opts)
            page = await context.new_page()

            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )

            captured_apis: List[Dict[str, Any]] = []

            async def on_response(resp):
                if "login_by_password" in resp.url or "account/login" in resp.url:
                    try:
                        data = await resp.json()
                        captured_apis.append(data)
                    except Exception:
                        pass

            page.on("response", on_response)

            await page.goto(
                "https://shopee.vn/buyer/login",
                wait_until="domcontentloaded",
                timeout=min(timeout, 20000)
            )

            inp_user = await page.wait_for_selector('input[name="loginKey"]', timeout=25000)
            inp_pass = await page.wait_for_selector('input[name="password"]', timeout=25000)

            await inp_user.fill(clean_phone)
            await page.wait_for_timeout(200)
            await inp_pass.fill("DummyPass@CheckF02")
            await page.wait_for_timeout(200)
            await inp_pass.press("Enter")

            btn = await page.query_selector(
                'button:has-text("Đăng nhập"), button:has-text("ĐĂNG NHẬP"), button[type="submit"]'
            )
            if btn:
                try:
                    await btn.click(timeout=1000)
                except Exception:
                    pass

            for _ in range(25):
                if captured_apis:
                    break
                await asyncio.sleep(0.2)

            login_resp = captured_apis[0] if captured_apis else None

            if login_resp:
                err = login_resp.get("error")
                err_msg = login_resp.get("error_msg", "")
                result["raw_error"] = f"error_{err}: {err_msg}"

                if err == 9 or "f02" in err_msg.lower() or "giới hạn" in err_msg.lower():
                    result.update({"is_f02": True, "f02_recoverable": True,
                                   "status": "🔴 Bị khóa (F02 lấy lại được)"})
                elif err == 2:
                    result.update({"is_f02": False, "status": "🟢 Đầu số sạch (Không bị F02)"})
                else:
                    result["status"] = f"Shopee phản hồi: {err_msg or err}"
            else:
                body_text = await page.inner_text("body")
                if "f02" in body_text.lower() or "giới hạn" in body_text.lower():
                    result.update({"is_f02": True, "status": "🔴 Bị khóa (F02 lấy lại được)"})
                elif "không chính xác" in body_text.lower() or "quên" in body_text.lower():
                    result["status"] = "🟢 Đầu số sạch (Không bị F02)"
                else:
                    result["status"] = "🟢 Đầu số sạch (Chưa đăng ký Shopee)"

            await browser.close()
            return result

        except Exception as e:
            logger.error("[PlaywrightPhone] Lỗi kiểm tra %s: %s", clean_phone, e)
            if browser:
                await browser.close()
            return {"ok": False, "phone": clean_phone, "error": str(e)}


# ==============================================================================
# 3. PUBLIC API — ĐỒNG BỘ (chạy trong thread pool để tránh conflict event loop)
# ==============================================================================

def check_shopee_phone(phone: str) -> Dict[str, Any]:
    """
    Kiểm tra số điện thoại Shopee bằng Playwright headless.
    Thread-safe: tự tạo event loop riêng trong ThreadPoolExecutor.
    """
    clean_phone = extract_phone_number(phone) or phone.strip()

    try:
        logger.info("[PhoneCheck] Kiểm tra SĐT %s qua Playwright...", clean_phone)

        def _run():
            return asyncio.run(
                _check_phone_via_playwright(clean_phone, proxy=_SHOPEE_PHONE_PROXY, timeout=30000)
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pw_res = executor.submit(_run).result(timeout=40)

        if pw_res.get("ok"):
            return {"ok": True, "engine": "playwright", "phone": clean_phone, "playwright_data": pw_res}
        return {"ok": False, "phone": clean_phone, "error": pw_res.get("error", "Không thể kiểm tra số lúc này.")}

    except Exception as e:
        logger.error("[PhoneCheck] Lỗi hệ thống khi kiểm tra %s: %s", clean_phone, e)
        return {"ok": False, "phone": clean_phone, "error": f"Lỗi hệ thống: {str(e)}"}


def format_phone_check_result(res: Dict[str, Any]) -> str:
    """
    Định dạng kết quả kiểm tra SĐT Shopee thành 2 dòng chuẩn:
    ☘️ Results: 0867706128
    🟢 Đầu số sạch (Không bị F02)
    """
    phone = res.get("phone", "")
    display_phone = "0" + phone if (len(phone) == 9 and not phone.startswith("0")) else phone

    if not res.get("ok"):
        err = res.get("error", "Không thể kiểm tra số lúc này.")
        if "timeout" in err.lower():
            err = "Hệ thống phản hồi chậm, thử lại sau ít phút."
        return f"☘️ Results: {display_phone}\n❌ Lỗi: {err}"

    pw_data = res.get("playwright_data", {})
    status = pw_data.get("status", "🟢 Đầu số sạch (Không bị F02)")
    return f"☘️ Results: {display_phone}\n{status}"
