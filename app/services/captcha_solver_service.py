"""
Module Captcha Solver Service - Giải quyết Slider Puzzle CAPTCHA của Shopee.
Hỗ trợ:
- Thuật toán đa cấp độ nhận diện lỗ khuyết (Template matching, Canny edge shadow, Sobel gradient).
- Bộ sinh quỹ đạo chuột 3 giai đoạn tự nhiên (Gia tốc ban đầu, giảm tốc khi tới gần, jittering Y ±1-2px, micro-correction overshoot).
- Cơ chế thử lại 3 lần, tự động click nút Refresh / Đổi hình nếu trượt thất bại.
"""

import math
import random
import time
import base64
import numpy as np
import cv2
from typing import Tuple, List, Optional
import logging

logger = logging.getLogger("CaptchaSolver")


def find_puzzle_gap_offset(bg_bytes: bytes, piece_bytes: Optional[bytes] = None) -> int:
    """
    Tính toán khoảng cách cần kéo trượt (pixel offset X) từ ảnh nền và mảnh ghép.
    Kết hợp:
    1. Template matching với viền Canny mảnh ghép
    2. Nhận diện hình học contour của lỗ khuyết (aspect ratio 0.75 - 1.35, x in [60, 220])
    3. Phân tích vùng bóng tối shadow / contrast
    """
    try:
        nparr_bg = np.frombuffer(bg_bytes, np.uint8)
        bg_img = cv2.imdecode(nparr_bg, cv2.IMREAD_COLOR)
        if bg_img is None:
            return 125

        h_bg, w_bg = bg_img.shape[:2]
        bg_gray = cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
        bg_blur = cv2.GaussianBlur(bg_gray, (5, 5), 0)
        bg_canny = cv2.Canny(bg_blur, 50, 150)
        # Bỏ 55px mép trái (chứa mảnh ban đầu) và 20px mép phải
        bg_canny[:, :55] = 0
        bg_canny[:, -20:] = 0

        # 1. Nếu có ảnh mảnh ghép piece
        if piece_bytes:
            try:
                nparr_piece = np.frombuffer(piece_bytes, np.uint8)
                piece_img = cv2.imdecode(nparr_piece, cv2.IMREAD_COLOR)
                if piece_img is not None:
                    piece_gray = cv2.cvtColor(piece_img, cv2.COLOR_BGR2GRAY)
                    piece_blur = cv2.GaussianBlur(piece_gray, (3, 3), 0)
                    piece_canny = cv2.Canny(piece_blur, 50, 150)

                    # Template matching trên ảnh Canny
                    res = cv2.matchTemplate(bg_canny, piece_canny, cv2.TM_CCOEFF_NORMED)
                    # Giới hạn x trong khoảng hợp lý [60, 220]
                    res[:, :60] = -1
                    res[:, 220:] = -1
                    _, max_val, _, max_loc = cv2.minMaxLoc(res)
                    if max_val > 0.08 and 60 <= max_loc[0] <= 220:
                        logger.info("Tìm thấy lỗ khuyết qua Template Matching: X=%d (score=%.2f)", max_loc[0], max_val)
                        return max_loc[0]
            except Exception as e:
                logger.warning("Lỗi template matching: %s", e)

        # 2. Phân tích viền Canny & Khối khuyết hình học
        contours, _ = cv2.findContours(bg_canny, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []

        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            if x < 60 or x > 220:
                continue
            if 25 <= w <= 65 and 25 <= h <= 65:
                aspect_ratio = float(w) / h
                if 0.7 <= aspect_ratio <= 1.4:
                    area = cv2.contourArea(cnt)
                    score = area if area > 0 else (w * h)
                    candidates.append((score, x, y, w, h))

        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            best_x = candidates[0][1]
            logger.info("Tìm thấy lỗ khuyết qua Canny Contour: X=%d", best_x)
            return best_x

        # 3. Phân tích Sobel Gradient dọc
        sobel_x = cv2.Sobel(bg_gray, cv2.CV_64F, 1, 0, ksize=3)
        sobel_x = np.abs(sobel_x)
        sobel_x[:, :65] = 0
        sobel_x[:, 215:] = 0
        col_sums = np.sum(sobel_x, axis=0)
        peak_x = int(np.argmax(col_sums))
        if 65 <= peak_x <= 215:
            logger.info("Tìm thấy lỗ khuyết qua Sobel Gradient: X=%d", peak_x)
            return peak_x

    except Exception as ex:
        logger.warning("Lỗi phân tích ảnh captcha: %s", ex)

    # Fallback an toàn vào khoảng giữa canvas
    return 130


def generate_human_tracks(distance: int) -> List[Tuple[float, float, float]]:
    """
    Sinh danh sách tọa độ (delta_x, delta_y, sleep_time) mô phỏng động tác kéo trượt của người thật.
    Quy luật:
    - 0% - 65%: Tăng tốc mạnh (người kéo dứt khoát)
    - 65% - 90%: Giảm tốc, điều chỉnh mắt nhìn
    - 90% - 100%: Rê chậm từng pixel để khớp lỗ
    - Micro-jittering Y: rung lắc tay tự nhiên ±1px
    - Overshoot & Correct: kéo lố 2-3px rồi trả lại 2-3px (phản xạ tay người)
    """
    tracks = []
    current = 0
    total_dist = float(distance)

    # Chia hành trình làm 3 pha
    phase1_dist = total_dist * 0.65
    phase2_dist = total_dist * 0.90

    # Pha 1: Tăng tốc
    while current < phase1_dist:
        step = random.uniform(8.0, 16.0)
        current += step
        if current > phase1_dist:
            step -= (current - phase1_dist)
            current = phase1_dist
        shake_y = random.choice([-1, 0, 0, 1])
        sleep_t = random.uniform(0.008, 0.018)
        tracks.append((step, shake_y, sleep_t))

    # Pha 2: Giảm tốc (Deceleration)
    while current < phase2_dist:
        step = random.uniform(3.0, 7.0)
        current += step
        if current > phase2_dist:
            step -= (current - phase2_dist)
            current = phase2_dist
        shake_y = random.choice([0, 0, -1, 1])
        sleep_t = random.uniform(0.015, 0.030)
        tracks.append((step, shake_y, sleep_t))

    # Pha 3: Căn chỉnh micro (Fine adjustment)
    while current < total_dist:
        step = random.uniform(1.0, 2.5)
        current += step
        if current > total_dist:
            step -= (current - total_dist)
            current = total_dist
        shake_y = 0
        sleep_t = random.uniform(0.025, 0.050)
        tracks.append((step, shake_y, sleep_t))

    # Động tác con người: Overshoot (kéo hơi quá trớn 2-3px) rồi kéo ngược lại
    overshoot_px = random.randint(2, 3)
    tracks.append((overshoot_px, 0, random.uniform(0.04, 0.08)))
    tracks.append((-overshoot_px, 0, random.uniform(0.05, 0.09)))

    return tracks


async def solve_shopee_slider_captcha(page, max_attempts: int = 3) -> bool:
    """
    Tự động bắt và giải Slider Captcha trên Playwright Page của Shopee:
    1. Nhận diện thanh trượt (slider track & button)
    2. Chụp ảnh nền và tính khoảng cách
    3. Kéo thả chuột mượt mà 3 pha
    4. Thử lại tối đa max_attempts lần, nếu thất bại bấm nút đổi hình (refresh captcha)
    """
    import asyncio

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info("Bắt đầu giải Captcha Shopee (Lần thử %d/%d)...", attempt, max_attempts)

            # Chờ thanh trượt slider xuất hiện (sử dụng container track để tránh locator bị hidden)
            slider_track = page.locator("div.F0XJ1W, div[class*='F0XJ1W'], [class*='slider-track'], div.HrMY5p [class*='track']").first
            await slider_track.wait_for(state="visible", timeout=8000)
            track_box = await slider_track.bounding_box()
            if not track_box:
                logger.warning("Không lấy được bounding box của slider track ở lần %d", attempt)
                continue

            # Thử tìm nút trượt con nếu hiển thị, nếu không dùng tọa độ gốc của track
            start_x = track_box["x"] + 20
            start_y = track_box["y"] + track_box["height"] / 2

            try:
                visible_btn = page.locator("div.F0XJ1W > div:visible, div[class*='F0XJ1W'] > div:visible").first
                if await visible_btn.count() > 0:
                    btn_box = await visible_btn.bounding_box()
                    if btn_box and btn_box["width"] > 0:
                        start_x = btn_box["x"] + btn_box["width"] / 2
                        start_y = btn_box["y"] + btn_box["height"] / 2
            except Exception:
                pass

            # Lấy ảnh background
            bg_elem = page.locator("div.Mcqxdt, canvas.BX1JD-, canvas[width='280'], .captcha-bg").first
            await bg_elem.wait_for(state="visible", timeout=5000)
            bg_bytes = await bg_elem.screenshot()

            # Lấy ảnh mảnh ghép nếu có
            piece_elem = page.locator("canvas[width='44'], canvas.MqzVM5, .captcha-puzzle").first
            piece_bytes = None
            if await piece_elem.count() > 0 and await piece_elem.is_visible():
                try:
                    piece_bytes = await piece_elem.screenshot()
                except Exception:
                    piece_bytes = None

            # Tính khoảng cách trượt
            offset_x = find_puzzle_gap_offset(bg_bytes, piece_bytes)
            # Điều chỉnh độ lệch thực tế (mảnh ghép thường bắt đầu lệch ~5px)
            adjusted_distance = max(35, min(220, int(offset_x) - 5))
            logger.info("Khoảng cách kéo tính được: %dpx", adjusted_distance)

            # Di chuột tới nút trượt tự nhiên
            await page.mouse.move(start_x + random.uniform(-2, 2), start_y + random.uniform(-2, 2))
            await asyncio.sleep(random.uniform(0.12, 0.22))
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.08, 0.15))

            # Sinh quỹ đạo và kéo
            tracks = generate_human_tracks(adjusted_distance)
            cur_x = start_x
            cur_y = start_y

            for dx, dy, dt in tracks:
                cur_x += dx
                cur_y += dy
                await page.mouse.move(cur_x, cur_y)
                await asyncio.sleep(dt)

            await asyncio.sleep(random.uniform(0.15, 0.3))
            await page.mouse.up()
            await asyncio.sleep(3.0)

            # Kiểm tra xem popup captcha đã đóng chưa
            modal = page.locator("div.HrMY5p, div.Mcqxdt")
            if await modal.count() == 0 or not await modal.first.is_visible():
                logger.info("🎉 Vượt qua Captcha Shopee thành công ở lần thử %d!", attempt)
                return True

            # Nếu chưa qua mà còn lượt thử, bấm nút Refresh/Đổi hình khác để thử lại
            if attempt < max_attempts:
                logger.warning("Lần thử %d chưa qua, đang bấm đổi hình mới...", attempt)
                refresh_btn = page.locator("div[class*='refresh'], button[class*='refresh'], .captcha_verify_img_reload, svg[class*='refresh'], div.HrMY5p svg, div[class*='F0XJ1W'] ~ div svg").first
                if await refresh_btn.count() > 0 and await refresh_btn.is_visible():
                    try:
                        await refresh_btn.click(force=True, timeout=2500)
                    except Exception:
                        await refresh_btn.dispatch_event("click")
                    await asyncio.sleep(2.5)
                else:
                    await asyncio.sleep(2.0)

        except Exception as ex:
            logger.warning("Lỗi trong lần thử giải captcha %d: %s", attempt, ex)
            await asyncio.sleep(1.5)

    return False

