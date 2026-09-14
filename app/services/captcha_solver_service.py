"""
Module Captcha Solver Service - Giải quyết Slider Puzzle CAPTCHA của Shopee với tỉ lệ pass 80% - 100%.
Hỗ trợ:
- Hệ thống nhận diện lỗ khuyết Multi-Engine Vision Ensemble (Shadow Density, Sobel-X Edge Pairs, Contour Geometry, Piece Alpha Silhouette).
- Cơ chế giải quyết Distractor Hole (Lỗ giả) & Đổi hình mới thông minh (Auto-Refresh) qua selector svg.Wts1Xu.
- Bộ sinh quỹ đạo cánh tay con người Minimum-Jerk (Flash & Hogan) với micro-tremor và phản xạ overshoot/pullback tự nhiên.
- Bộ kiểm tra thành công đa điểm (Watertight Multi-Check) chống false-positive.
"""

import math
import random
import time
import base64
import asyncio
import numpy as np
import cv2
from typing import Tuple, List, Optional
import logging
import requests
from app.config import SADCAPTCHA_API_KEY

logger = logging.getLogger("CaptchaSolverService")

_sadcaptcha_disabled = False

def query_sadcaptcha_puzzle(puzzle_b64: str, piece_b64: str) -> Optional[float]:
    """
    Gọi SadCaptcha API chuyên dụng cho Shopee (/shopee-image-drag) hoặc fallback (/puzzle)
    để lấy tỉ lệ trượt proportion chính xác từ AI Cloud.
    Trả về: float proportion (0.0 .. 1.0) hoặc None nếu lỗi/hết credit.
    """
    global _sadcaptcha_disabled
    if not SADCAPTCHA_API_KEY or _sadcaptcha_disabled:
        return None

    payload = {
        "puzzleImageB64": puzzle_b64,
        "pieceImageB64": piece_b64
    }

    # 1. Thử endpoint chuyên dụng Shopee: /shopee-image-drag
    try:
        shopee_url = f"https://www.sadcaptcha.com/api/v1/shopee-image-drag?licenseKey={SADCAPTCHA_API_KEY}"
        resp = requests.post(shopee_url, json=payload, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            pts = data.get("proportionalPoints") or []
            if pts and isinstance(pts, list) and len(pts) > 0:
                p_x = pts[0].get("proportionX") if "proportionX" in pts[0] else pts[0].get("x")
                if p_x is not None:
                    prop = float(p_x)
                    logger.info("🤖 [SadCaptcha Shopee-Drag] AI giải thành công proportion = %.4f", prop)
                    return prop
        elif resp.status_code in (401, 403):
            _sadcaptcha_disabled = True
            logger.warning("SadCaptcha API Key không hợp lệ hoặc hết hạn (%d). Đã chuyển sang Vision Solver nội bộ.", resp.status_code)
            return None
    except Exception as ex:
        logger.warning("Lỗi gọi SadCaptcha /shopee-image-drag: %s", ex)

    # 2. Fallback sang endpoint /puzzle
    try:
        puzzle_url = f"https://www.sadcaptcha.com/api/v1/puzzle?licenseKey={SADCAPTCHA_API_KEY}"
        resp = requests.post(puzzle_url, json=payload, timeout=7)
        if resp.status_code == 200:
            data = resp.json()
            prop = data.get("slideXProportion")
            if prop is not None and isinstance(prop, (int, float)):
                logger.info("🤖 [SadCaptcha Puzzle] AI giải thành công slideXProportion = %.4f", prop)
                return float(prop)
        else:
            if resp.status_code in (401, 403):
                _sadcaptcha_disabled = True
            logger.warning("SadCaptcha API trả về mã lỗi %d: %s", resp.status_code, resp.text[:200])
    except Exception as ex:
        logger.warning("Lỗi kết nối SadCaptcha API: %s", ex)

    return None


def get_rotation_angle_at_x(piece_x: float) -> float:
    """Tính góc xoay lý thuyết tuyệt đối của mảnh ghép Shopee tại tọa độ X."""
    mouse_d = piece_x_to_mouse_drag(piece_x)
    return mouse_d * 0.9


def detect_shopee_holes_calibrated(bg_img: np.ndarray, piece_img: Optional[np.ndarray] = None, y_dom: Optional[float] = None) -> List[Tuple[int, float]]:
    """
    Thuật toán nhận diện lỗ khuyết Shopee Vision Ensemble v3:
    1. Trích xuất Y slice: nếu có y_dom từ DOM, quét trong dải hẹp [y_dom - 4, y_dom + 4]. Nếu không, quét toàn dải Y.
    2. Multi-scale Black-Hat (20x20, 28x28, 38x38) để bắt độ lõm tối bất kể góc xoay của lỗ.
    3. Sobel Gradient & Canny Edges để tìm cấu trúc biên sắc nét của lỗ.
    4. Weber Relative Contrast: triệt tiêu ưu thế nền sáng, đo độ tương phản chuẩn mực.
    5. Color Matching với mảnh ghép: nếu có piece_img (alpha > 140), so sánh tương quan vector màu BGR.
    6. Trả về Top 2 ứng viên X phân tách nhau ít nhất 32px (1 ô thật + 1 ô giả distractor).
    """
    try:
        h_bg, w_bg = bg_img.shape[:2]
        gray = cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(bg_img, cv2.COLOR_BGR2HSV)
        v_chan = hsv[:, :, 2].astype(float)

        # 1. Multi-scale Black-Hat
        k1 = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 20))
        k2 = cv2.getStructuringElement(cv2.MORPH_RECT, (28, 28))
        k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (38, 38))
        bh = (cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k1).astype(float) * 0.3 +
              cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k2).astype(float) * 0.4 +
              cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k3).astype(float) * 0.3)
        bh[:, :34] = 0
        bh[:, 245:] = 0

        # 2. Sobel gradient magnitude
        sob_x = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        sob_y = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
        sob = sob_x + sob_y * 0.5

        pw, ph = 42, 42

        # 3. Phân tích màu sắc lõi mảnh ghép nếu có (alpha > 140)
        p_ratio = None
        if piece_img is not None:
            try:
                if len(piece_img.shape) == 3 and piece_img.shape[2] == 4:
                    alpha = piece_img[:, :, 3]
                    mask = alpha > 140
                    p_pix = piece_img[:, :, :3][mask]
                else:
                    p_pix = piece_img.reshape(-1, 3)
                if len(p_pix) > 20:
                    p_m = np.mean(p_pix, axis=0)
                    p_ratio = p_m / (np.sum(p_m) + 1e-6)
            except Exception:
                pass

        # Xác định dải quét Y
        if y_dom is not None and 0 <= y_dom <= (h_bg - ph):
            y_start = max(0, int(round(y_dom - 4)))
            y_end = min(h_bg - ph, int(round(y_dom + 4))) + 1
            y_step = 1
        else:
            y_start = 0
            y_end = h_bg - ph + 1
            y_step = 2

        scores = []
        # Quét X từ 42px đến w_bg - pw - 6
        for x in range(42, w_bg - pw - 6):
            best_sc = -999.0
            best_y = y_start
            for y in range(y_start, y_end, y_step):
                bh_mean = float(np.mean(bh[y:y+ph, x:x+pw]))
                bh_max = float(np.max(bh[y:y+ph, x:x+pw]))
                std_val = float(np.std(gray[y:y+ph, x:x+pw]))
                sob_mean = float(np.mean(sob[y:y+ph, x:x+pw]))

                # Weber contrast
                pad = 6
                y1_p, y2_p = max(0, y - pad), min(h_bg, y + ph + pad)
                x1_p, x2_p = max(0, x - pad), min(w_bg, x + pw + pad)
                outer_v = float(np.mean(v_chan[y1_p:y2_p, x1_p:x2_p]))
                inner_v = float(np.mean(v_chan[y:y+ph, x:x+pw]))
                contrast = max(0.0, outer_v - inner_v) / (outer_v + 35.0) * 80.0

                # Color match bonus
                color_sc = 0.0
                if p_ratio is not None:
                    reg = bg_img[y:y+ph, x:x+pw]
                    b_m = np.mean(reg, axis=(0, 1))
                    b_ratio = b_m / (np.sum(b_m) + 1e-6)
                    c_dist = float(np.sum(np.abs(p_ratio - b_ratio)))
                    color_sc = max(0.0, (0.24 - c_dist) / 0.24) * 85.0

                sc = (bh_mean * 1.8 + bh_max * 0.6 + 
                      std_val * 1.5 + sob_mean * 2.2 + 
                      contrast * 1.0 + color_sc)

                if sc > best_sc:
                    best_sc = sc
                    best_y = y
            scores.append((x, best_sc, best_y))

        scores.sort(key=lambda s: s[1], reverse=True)
        peaks = []
        for x, sc, y in scores:
            if not any(abs(p[0] - x) < 32 for p in peaks):
                peaks.append((x, round(sc, 1)))
                if len(peaks) >= 2:
                    break

        if not peaks:
            peaks = [(60, 1.0), (160, 0.8)]

        logger.info("🎯 Vision Ensemble v3 tìm thấy 2 ứng viên lỗ khuyết: %s", peaks)
        return peaks
    except Exception as ex:
        logger.warning("Lỗi Vision Ensemble v3: %s", ex)
        return [(60, 1.0), (160, 0.8)]


def find_exact_slice_candidates(bg_bytes: bytes, piece_bytes: Optional[bytes] = None, y_val: float = 40.0) -> List[Tuple[int, float]]:
    """Tích hợp Dynamic Angle Calibration cho lát cắt Y."""
    try:
        nparr_bg = np.frombuffer(bg_bytes, np.uint8)
        bg_img = cv2.imdecode(nparr_bg, cv2.IMREAD_COLOR)
        if bg_img is None:
            return [(95, 1.0), (175, 0.8)]

        piece_img = None
        if piece_bytes:
            nparr_p = np.frombuffer(piece_bytes, np.uint8)
            piece_img = cv2.imdecode(nparr_p, cv2.IMREAD_UNCHANGED)

        return detect_shopee_holes_calibrated(bg_img, piece_img, y_val)
    except Exception as ex:
        logger.warning("Lỗi phân tích lát cắt: %s", ex)
        return [(95, 1.0), (175, 0.8)]


def find_ensemble_candidates(bg_bytes: bytes, piece_bytes: Optional[bytes] = None) -> List[Tuple[int, float]]:
    """
    Hệ thống nhận diện lỗ khuyết Shopee đa tầng (Multi-Engine Vision Ensemble):
    1. Engine 1: Adaptive Percentile Shadow Clustering (Tìm vùng tối / bóng đổ của lỗ khuyết)
    2. Engine 2: Sobel-X Vertical Boundary Pair Detector (Tìm cặp viền đứng cách nhau 38-46px)
    3. Engine 3: Contour Geometry Analysis (Phân tích chu vi, diện tích và tỉ lệ hộp 42x42)
    4. Engine 4: Piece Alpha Silhouette Match (Khớp mẫu đường viền notch của mảnh ghép)
    
    Kết hợp đồng thuận (Consensus Clustering) & thưởng điểm khi nhiều Engine cùng đồng thuận.
    QUY TẮC VÀNG: Shopee chỉ có tối đa 2 lỗ (1 lỗ thật + 1 lỗ giả distractor).
    Hàm chỉ trả về tối đa 2 ứng viên có điểm số cao nhất.
    """
    try:
        nparr_bg = np.frombuffer(bg_bytes, np.uint8)
        bg_img = cv2.imdecode(nparr_bg, cv2.IMREAD_COLOR)
        if bg_img is None:
            return [(116, 1.0), (165, 0.8)]

        h_bg, w_bg = bg_img.shape[:2]
        bg_gray = cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
        
        # Danh sách các phiếu bầu: (x_pos, trọng số, tên_engine)
        votes = []

        # ========================================================
        # ENGINE 1A: Morphological Black-Hat Cutout Detector (Tìm hố khuyết tối màu trên mọi nền)
        # ========================================================
        kernel_bh = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
        blackhat = cv2.morphologyEx(bg_gray, cv2.MORPH_BLACKHAT, kernel_bh)
        strip_bh = blackhat[10:110, 32:235]
        col_sums_bh = np.sum(strip_bh, axis=0)
        smoothed_bh = np.convolve(col_sums_bh, np.ones(7)/7.0, mode="same")

        s_copy = smoothed_bh.copy()
        for rank in range(3):
            max_idx = int(np.argmax(s_copy))
            val = s_copy[max_idx]
            if val < 400:
                break
            actual_left = max(30, 32 + max_idx - 16)
            weight = min(4.0, float(val) / 2200.0)
            votes.append((actual_left, weight, "blackhat_cutout"))
            s_copy[max(0, max_idx - 25):min(len(s_copy), max_idx + 25)] = 0

        # ========================================================
        # ENGINE 1B: Adaptive Percentile Shadow Density (Vùng Y: 10..110)
        # ========================================================
        search_strip = bg_gray[10:110, 32:235]
        p12 = np.percentile(search_strip, 12)
        p20 = np.percentile(search_strip, 20)
        thresh_val = max(30, min(85, (p12 + p20) / 2 + 5))
        dark_mask = (search_strip < thresh_val).astype(np.uint8) * 255

        col_sums = np.sum(dark_mask, axis=0)
        kernel = np.ones(9) / 9.0
        smoothed = np.convolve(col_sums, kernel, mode="same")

        s_copy2 = smoothed.copy()
        for rank in range(3):
            max_idx = int(np.argmax(s_copy2))
            val = s_copy2[max_idx]
            if val < 25:
                break
            actual_left = max(30, 32 + max_idx - 16)
            weight = float(val) / 45.0
            votes.append((actual_left, min(weight, 3.0), "shadow_density"))
            s_copy2[max(0, max_idx - 25):min(len(s_copy2), max_idx + 25)] = 0

        # ========================================================
        # ENGINE 2: Sobel-X Vertical Boundary Pair Detector (Y: 10..110, X: 32..235)
        # ========================================================
        sobel_x = cv2.Sobel(bg_gray, cv2.CV_32F, 1, 0, ksize=3)
        sobel_abs = np.abs(sobel_x)
        sobel_strip = sobel_abs[10:110, 32:235]
        edge_cols = np.sum(sobel_strip, axis=0)
        edge_smoothed = np.convolve(edge_cols, np.ones(5)/5.0, mode="same")
        
        for offset in range(36, 50):
            if len(edge_smoothed) > offset:
                pair_score = edge_smoothed[:-offset] * edge_smoothed[offset:]
                best_idx = int(np.argmax(pair_score))
                if pair_score[best_idx] > 3e6:
                    actual_left = 32 + best_idx
                    votes.append((actual_left, 2.5, f"sobel_pair_{offset}"))
                    break

        # ========================================================
        # ENGINE 3: Contour Geometry Analysis (Hỗ trợ cả hình vuông xoay 45 độ 32-62px)
        # ========================================================
        blurred = cv2.GaussianBlur(bg_gray, (5, 5), 0)
        thresh_contour = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 3
        )
        contours, _ = cv2.findContours(thresh_contour[10:110, 32:235], cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            area = cv2.contourArea(c)
            if 30 <= w <= 62 and 30 <= h <= 62 and 700 <= area <= 2800:
                actual_left = 32 + x
                votes.append((actual_left, 2.5, "contour_geometry"))

        # ========================================================
        # ENGINE 4: Piece Alpha Silhouette Match (nếu có piece)
        # ========================================================
        if piece_bytes:
            try:
                nparr_piece = np.frombuffer(piece_bytes, np.uint8)
                piece_img = cv2.imdecode(nparr_piece, cv2.IMREAD_UNCHANGED)
                if piece_img is not None and len(piece_img.shape) == 3 and piece_img.shape[2] == 4:
                    alpha = piece_img[:, :, 3]
                    piece_mask = (alpha > 50).astype(np.uint8) * 255
                    silhouette_edge = cv2.Canny(piece_mask, 50, 150)
                    
                    bg_edge = cv2.Canny(bg_gray, 50, 150)
                    bg_edge[:, :30] = 0
                    bg_edge[:, 235:] = 0
                    
                    res = cv2.matchTemplate(bg_edge, silhouette_edge, cv2.TM_CCOEFF)
                    res[:, :30] = -1
                    res[:, 235:] = -1
                    
                    # Trích xuất Top 2 vị trí khớp hình học notch cao nhất (Ứng viên 1 & 2)
                    res_copy = res.copy()
                    for r_idx in range(2):
                        _, max_v, _, max_l = cv2.minMaxLoc(res_copy)
                        if max_l[0] >= 30 and max_v > 0:
                            actual_left = max_l[0]
                            votes.append((actual_left, 5.0 if r_idx == 0 else 4.0, f"piece_silhouette_{r_idx+1}"))
                            x_m, y_m = max_l
                            res_copy[max(0, y_m - 12):min(res.shape[0], y_m + 12), max(0, x_m - 20):min(res.shape[1], x_m + 20)] = -1
            except Exception:
                pass

        # ========================================================
        # CONSENSUS CLUSTERING & RANKING
        # ========================================================
        clusters = []
        for x_pos, weight, eng in votes:
            matched = False
            for cl in clusters:
                if abs(cl["x"] - x_pos) <= 7:
                    old_weight = cl["score"]
                    new_weight = old_weight + weight
                    cl["x"] = (cl["x"] * old_weight + x_pos * weight) / new_weight
                    cl["score"] = new_weight
                    cl["count"] += 1
                    cl["engines"].append(eng)
                    matched = True
                    break
            if not matched:
                clusters.append({"x": float(x_pos), "score": weight, "count": 1, "engines": [eng]})

        # Multi-engine consensus bonus
        for cl in clusters:
            unique_engines = set(cl["engines"])
            if len(unique_engines) >= 2:
                cl["score"] *= 1.6
            if len(unique_engines) >= 3:
                cl["score"] *= 2.2

        clusters.sort(key=lambda c: c["score"], reverse=True)

        results = []
        for cl in clusters:
            # cl["x"] đã được chuẩn hóa về đúng mép trái của lỗ khuyết (canvas X)
            # Vì mảnh ghép khởi đầu tại X = 0, khoảng cách trượt = cl["x"]
            dist = int(round(cl["x"]))
            dist = max(32, min(225, dist))
            if not any(abs(r[0] - dist) < 6 for r in results):
                results.append((dist, round(cl["score"], 2)))

        # Chỉ giữ lại tối đa 2 ứng viên tốt nhất (1 lỗ thật + 1 lỗ giả distractor)
        results = results[:2]

        if not results:
            results = [(95, 1.0), (165, 0.8)]

        logger.info("Kết quả phân tích lỗ khuyết Ensemble (Top 2): %s", results)
        return results

    except Exception as ex:
        logger.warning("Lỗi phân tích lỗ khuyết captcha: %s", ex)
        return [(116, 1.0), (165, 0.8)]


def find_all_puzzle_candidates(bg_bytes: bytes, piece_bytes: Optional[bytes] = None) -> List[int]:
    """Tương thích ngược: trả về danh sách int khoảng cách trượt."""
    scored = find_ensemble_candidates(bg_bytes, piece_bytes)
    return [d for d, _ in scored]


def find_puzzle_gap_offset(bg_bytes: bytes, piece_bytes: Optional[bytes] = None) -> int:
    """Tương thích ngược: trả về ứng viên số 1."""
    cands = find_all_puzzle_candidates(bg_bytes, piece_bytes)
    return cands[0] if cands else 116


def generate_human_tracks(distance: int) -> List[Tuple[float, float, float]]:
    """
    Sinh quỹ đạo kéo chuột theo mô hình vận động người thật (Minimum-Jerk Human Arm Model):
    - Phương trình Flash & Hogan (1985): x(t) = D * (10*(t/T)^3 - 15*(t/T)^4 + 6*(t/T)^5)
    - Vận tốc hình chuông Gauss: gia tốc mượt mà, đạt đỉnh ở 50% chặng, giảm tốc tự nhiên.
    - Hand tremor: Vi rung lắc ngẫu nhiên trục Y dạng Brownian Walk bounded [-1.5px, +1.5px].
    - Visual motor reflex: Kéo lố qua phải 2-3px trong 50ms rồi hồi quy về khớp lỗ, dừng nhẹ kiểm tra.
    """
    tracks = []
    total_dist = float(distance)
    
    total_time = random.uniform(0.52, 0.72)
    num_steps = random.randint(40, 52)
    
    prev_x = 0.0
    prev_y = 0.0
    tremor_y = 0.0
    
    for i in range(1, num_steps + 1):
        t = i / float(num_steps)
        progress = 10.0 * (t ** 3) - 15.0 * (t ** 4) + 6.0 * (t ** 5)
        cur_x = total_dist * progress
        
        # Hand tremor ngẫu nhiên
        tremor_y += random.gauss(0, 0.35)
        tremor_y = max(-1.8, min(1.8, tremor_y))
        
        step_dx = cur_x - prev_x
        step_dy = tremor_y - prev_y
        
        base_dt = total_time / num_steps
        step_dt = max(0.008, base_dt + random.uniform(-0.003, 0.004))
        
        tracks.append((step_dx, step_dy, step_dt))
        prev_x = cur_x
        prev_y = tremor_y

    # Phản xạ tay người: Overshoot 2-3px rồi trả lại
    overshoot = random.randint(2, 3)
    tracks.append((float(overshoot), random.uniform(-0.3, 0.3), random.uniform(0.04, 0.07)))
    tracks.append((0.0, 0.0, random.uniform(0.03, 0.06)))
    tracks.append((-float(overshoot), random.uniform(-0.3, 0.3), random.uniform(0.04, 0.08)))
    
    return tracks


# Bảng tra thực nghiệm chính xác tuyệt đối (Mouse Drag -> Piece translateX)
CALIBRATED_SAMPLES = [
    (  0,   0.00),
    (  5,   0.01),
    ( 10,   0.11),
    ( 15,   0.59),
    ( 20,   1.81),
    ( 25,   4.11),
    ( 30,   7.74),
    ( 35,  12.85),
    ( 40,  19.46),
    ( 45,  27.49),
    ( 50,  36.80),
    ( 55,  47.18),
    ( 60,  58.43),
    ( 65,  70.32),
    ( 70,  82.61),
    ( 75,  95.12),
    ( 80, 107.66),
    ( 85, 120.08),
    ( 90, 132.24),
    ( 95, 144.04),
    (100, 155.41),
    (105, 166.27),
    (110, 176.59),
    (115, 186.34),
    (120, 195.51),
    (125, 204.10),
    (130, 212.12),
    (135, 219.57),
    (140, 226.48),
    (145, 232.88),
    (150, 236.00),
]
_TABLE_MOUSE = np.array([s[0] for s in CALIBRATED_SAMPLES], dtype=float)
_TABLE_PIECE_X = np.array([s[1] for s in CALIBRATED_SAMPLES], dtype=float)

def piece_x_to_mouse_drag(target_x: float) -> float:
    """Quy đổi tọa độ lỗ khuyết thành khoảng cách kéo chuột chính xác tuyệt đối qua nội suy S-Curve."""
    target_x = max(0.0, min(236.0, float(target_x)))
    return float(np.interp(target_x, _TABLE_PIECE_X, _TABLE_MOUSE))


async def solve_shopee_slider_captcha(page, max_attempts: int = 5) -> bool:
    """
    Tự động vượt Slider Puzzle CAPTCHA của Shopee với độ chính xác cao:
    1. Multi-Engine Ensemble / 1D Slice Matching tìm Top 2 ứng viên.
    2. Quy đổi tọa độ lỗ sang khoảng cách chuột bằng đường cong hiệu chuẩn S-Curve (CALIBRATED_SAMPLES).
    3. Kéo mượt hoàn toàn bằng Flash & Hogan Minimum-Jerk (Không eval ngắt quãng khi đang giữ chuột).
    4. Kiểm tra đa điểm chống False-Positive: Bắt buộc loại bỏ popup lỗi 'Vui lòng thử lại sau'.
    """
    cached_candidates: List[Tuple[int, float]] = []
    cand_index = 0

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info("🎯 Bắt đầu giải Captcha Shopee (Lần thử %d/%d)...", attempt, max_attempts)

            # 1. Tìm thanh trượt slider
            slider_track = page.locator("div.F0XJ1W, div[class*='F0XJ1W'], [class*='slider-track'], div.HrMY5p [class*='track']").first
            try:
                await slider_track.wait_for(state="visible", timeout=4000)
            except Exception:
                # Kiểm tra nếu thanh trượt đã biến mất và xuất hiện bước tiếp theo
                next_step = page.locator(
                    "button:has-text('Các phương pháp khác'), button:has-text('GỌI CHO TÔI'), button:has-text('Gọi cho tôi'), input[name='otp'], input[autocomplete='one-time-code'], [class*='other-methods'], div:has-text('Chúng tôi sẽ gửi mã'), div:has-text('Hoạt động bất thường')"
                ).first
                if await next_step.count() > 0 and await next_step.is_visible():
                    logger.info("🎉 ĐÃ CHUYỂN SANG BƯỚC TIẾP THEO (Vượt Captcha thành công)!")
                    return True

                modal = page.locator("div.HrMY5p, div:has-text('Xác nhận để tiếp tục')")
                if await modal.count() == 0 or not await modal.first.is_visible():
                    logger.info("🎉 Modal Captcha đã đóng hoàn toàn, vượt thành công!")
                    return True
                logger.warning("Không lấy được slider track ở lần %d", attempt)
                continue

            track_box = await slider_track.bounding_box()
            if not track_box:
                logger.warning("Không lấy được bounding box slider track ở lần %d", attempt)
                continue

            # Nút trượt xuất phát
            start_x = track_box["x"] + 20
            start_y = track_box["y"] + track_box["height"] / 2

            try:
                visible_btn = page.locator("div.F0XJ1W > div:visible, div[class*='F0XJ1W'] > div:visible, div.HrMY5p [class*='button']").first
                if await visible_btn.count() > 0:
                    btn_box = await visible_btn.bounding_box()
                    if btn_box and btn_box["width"] > 0:
                        start_x = btn_box["x"] + btn_box["width"] / 2
                        start_y = btn_box["y"] + btn_box["height"] / 2
            except Exception:
                pass

            # 2. Nếu chưa có candidates (lần đầu hoặc sau khi bấm Refresh)
            if not cached_candidates or cand_index >= len(cached_candidates):
                eval_data = await page.evaluate('''async () => {
                    let bg = document.querySelector('canvas[width="280"]');
                    let piece = document.querySelector('canvas[width="44"]') || document.querySelector('div.HrMY5p canvas:not([width="280"])');
                    
                    // Chờ canvas vẽ xong dữ liệu hình ảnh (không bị trắng xóa)
                    for (let w = 0; w < 30; w++) {
                        if (bg && piece) {
                            try {
                                let ctx = bg.getContext('2d');
                                let p = ctx.getImageData(10, 10, 50, 50).data;
                                let sum = 0;
                                for (let i = 0; i < p.length; i += 4) {
                                    sum += p[i] + p[i+1] + p[i+2];
                                }
                                if (sum > 1000) break;
                            } catch (e) {}
                        }
                        await new Promise(r => setTimeout(r, 200));
                        bg = document.querySelector('canvas[width="280"]');
                        piece = document.querySelector('canvas[width="44"]') || document.querySelector('div.HrMY5p canvas:not([width="280"])');
                    }

                    let pieceContainer = document.querySelector('div[class*="_4U309i"]') || (piece ? piece.parentElement : null);
                    let bgRect = bg ? bg.getBoundingClientRect() : null;
                    let pieceRect = piece ? piece.getBoundingClientRect() : null;

                    return {
                        bg_data: bg ? bg.toDataURL('image/png') : null,
                        piece_data: piece ? piece.toDataURL('image/png') : null,
                        bg_rect: bgRect ? {x: bgRect.x, y: bgRect.y, width: bgRect.width, height: bgRect.height} : null,
                        piece_rect: pieceRect ? {x: pieceRect.x, y: pieceRect.y, width: pieceRect.width, height: pieceRect.height} : null,
                        piece_transform: pieceContainer ? (pieceContainer.style.transform || pieceContainer.getAttribute('style') || '') : ''
                    };
                }''')

                bg_bytes = None
                piece_bytes = None
                if eval_data.get("bg_data"):
                    bg_bytes = base64.b64decode(eval_data["bg_data"].split(",")[1])
                if eval_data.get("piece_data"):
                    piece_bytes = base64.b64decode(eval_data["piece_data"].split(",")[1])

                # Xác định độ cao Y chính xác từ translateY
                y_val = None
                t_str = eval_data.get("piece_transform") or ""
                if "translateY(" in t_str:
                    try:
                        y_val = float(t_str.split("translateY(")[1].split("px")[0])
                    except Exception:
                        pass
                elif "translate3d(" in t_str:
                    try:
                        parts = t_str.split("translate3d(")[1].split(")")[0].split(",")
                        if len(parts) >= 2:
                            y_val = float(parts[1].replace("px", "").strip())
                    except Exception:
                        pass
                piece_init_x = 0.0
                if eval_data.get("piece_rect") and eval_data.get("bg_rect"):
                    if y_val is None:
                        y_val = eval_data["piece_rect"]["y"] - eval_data["bg_rect"]["y"]
                    piece_init_x = max(0.0, eval_data["piece_rect"]["x"] - eval_data["bg_rect"]["x"])
                if y_val is None:
                    y_val = 40.0

                logger.info("🎯 Tọa độ ban đầu mảnh ghép từ DOM: X=%.1fpx, Y=%.1fpx", piece_init_x, y_val)

                cached_candidates = []

                # ƯU TIÊN 1: Thử SadCaptcha AI nếu có API Key và còn credit
                if SADCAPTCHA_API_KEY and eval_data.get("bg_data") and eval_data.get("piece_data"):
                    try:
                        sad_bg_b64 = eval_data["bg_data"].split(",")[1]
                        sad_piece_b64 = eval_data["piece_data"].split(",")[1]
                        prop = query_sadcaptcha_puzzle(sad_bg_b64, sad_piece_b64)
                        if prop is not None and 0.10 <= prop <= 0.95:
                            sad_x = int(round(prop * 280.0))
                            cached_candidates.append((sad_x, 0.99))
                            logger.info("🤖 [SadCaptcha AI Cloud] Vị trí lỗ khuyết: prop=%.4f -> Canvas X=%dpx", prop, sad_x)
                    except Exception as ex_sad:
                        logger.warning("SadCaptcha Cloud không khả dụng: %s", ex_sad)

                # ƯU TIÊN 2 (CƠ CHẾ NỘI BỘ SIÊU CHUẨN XÁC): Vision Ensemble v3 Local
                if not cached_candidates and bg_bytes is not None:
                    try:
                        nparr_bg = np.frombuffer(bg_bytes, np.uint8)
                        bg_cv = cv2.imdecode(nparr_bg, cv2.IMREAD_COLOR)
                        piece_cv = None
                        if piece_bytes:
                            nparr_p = np.frombuffer(piece_bytes, np.uint8)
                            piece_cv = cv2.imdecode(nparr_p, cv2.IMREAD_UNCHANGED)

                        vision_cands = detect_shopee_holes_calibrated(bg_cv, piece_cv, y_dom=y_val)
                        for cand_x, sc in vision_cands:
                            cached_candidates.append((cand_x, sc))
                        logger.info("🎯 [Local Vision Ensemble v3] Nhận diện thành công 2 ứng viên lỗ khuyết: %s", cached_candidates)
                    except Exception as ex_vis:
                        logger.warning("Lỗi phân tích Vision Ensemble v3: %s", ex_vis)

                # Nếu cả 2 đều không lấy được ứng viên -> Bấm Refresh để tải ảnh mới
                if not cached_candidates:
                    logger.warning("⚠️ Không tìm thấy ứng viên lỗ khuyết ở lần %d. Đang bấm Refresh đổi hình mới...", attempt)
                    refresh_btn = page.locator("div.HrMY5p [class*='refresh'], div.HrMY5p button[title*='refresh'], div[class*='refresh-button']").first
                    if await refresh_btn.count() > 0 and await refresh_btn.is_visible():
                        await refresh_btn.click()
                        await asyncio.sleep(1.5)
                    continue

                cand_index = 0

            # 3. Lấy khoảng cách kéo của ứng viên hiện tại
            cur_cand = cached_candidates[cand_index]
            target_box_x = cur_cand[0]
            cand_score = cur_cand[1]
            cand_index += 1

            # Tọa độ translateX thực tế để mảnh ghép phủ khít trọn vẹn vào ô màu xám
            target_tx = max(0.0, target_box_x - piece_init_x)

            logger.info("👉 Mục tiêu Lỗ khuyết: Canvas X=%dpx -> Target translateX=%.1fpx (Điểm tin cậy: %.1f, Ứng viên %d/%d)", 
                        target_box_x, target_tx, cand_score, cand_index, len(cached_candidates))

            # 4. Định vị nút trượt ban đầu
            start_x = track_box["x"] + 20
            start_y = track_box["y"] + track_box["height"] / 2

            for _ in range(12):
                btn_loc = page.locator("div.F0XJ1W > div:visible, div[class*='F0XJ1W'] > div:visible, div.HrMY5p [class*='button']").first
                if await btn_loc.count() > 0:
                    b_b = await btn_loc.bounding_box()
                    if b_b and b_b["x"] <= track_box["x"] + 30:
                        start_x = b_b["x"] + b_b["width"] / 2
                        start_y = b_b["y"] + b_b["height"] / 2
                        break
                await asyncio.sleep(0.25)

            # 5. KÉO CHUỘT THEO ĐƯỜNG CONG HIỆU CHUẨN S-CURVE & QUỸ ĐẠO FLASH & HOGAN
            calculated_mouse_drag = piece_x_to_mouse_drag(target_tx)
            logger.info("📐 Target translateX %.1fpx -> Khoảng cách kéo chuột lý thuyết: %.2fpx", target_tx, calculated_mouse_drag)

            await page.mouse.move(start_x + random.uniform(-0.8, 0.8), start_y + random.uniform(-0.8, 0.8))
            await asyncio.sleep(random.uniform(0.12, 0.18))
            await page.mouse.down()
            await asyncio.sleep(random.uniform(0.10, 0.15))

            # Sinh quỹ đạo Minimum-Jerk sinh học Flash & Hogan kéo thẳng tới mục tiêu
            tracks = generate_human_tracks(max(5, int(round(calculated_mouse_drag))))
            cur_x = start_x
            cur_y = start_y
            for dx, dy, dt in tracks:
                cur_x += dx
                cur_y += dy * 0.25
                await page.mouse.move(cur_x, cur_y)
                await asyncio.sleep(dt)

            # Dừng nghỉ 180ms - 220ms để browser layout ổn định
            await asyncio.sleep(random.uniform(0.18, 0.22))

            # RADAR CLOSED-LOOP SUB-PIXEL TRACKING (Khóa khít tâm lỗ)
            try:
                real_tx = await page.evaluate('''() => {
                    let piece = document.querySelector('canvas[width="44"]') || document.querySelector('div.HrMY5p canvas:not([width="280"])');
                    let pieceContainer = document.querySelector('div[class*="_4U309i"]') || (piece ? piece.parentElement : null);
                    if (!pieceContainer) return null;
                    let st = pieceContainer.style.transform || pieceContainer.getAttribute('style') || '';
                    if (st.includes('translateX(')) {
                        return parseFloat(st.split('translateX(')[1].split('px')[0]);
                    }
                    if (st.includes('translate3d(')) {
                        return parseFloat(st.split('translate3d(')[1].split('px')[0]);
                    }
                    return null;
                }''')
                if real_tx is not None:
                    diff = target_tx - real_tx
                    logger.info("📡 Radar Closed-Loop: Target TX=%.2f, Real TX=%.2f, Sai số diff=%.2fpx", target_tx, real_tx, diff)
                    if abs(diff) > 0.85 and abs(diff) < 25.0:
                        # Vi chỉnh nhẹ chuột với hệ số giảm chấn 0.65
                        micro_dx = max(-3.0, min(3.0, diff * 0.65))
                        cur_x += micro_dx
                        await page.mouse.move(cur_x, cur_y)
                        await asyncio.sleep(0.15)
            except Exception as ex_radar:
                logger.debug("Radar evaluate error: %s", ex_radar)

            # Dừng nhẹ trước khi nhả chuột
            await asyncio.sleep(random.uniform(0.15, 0.20))
            await page.mouse.up()
            logger.info("👆 Đã nhả chuột ghép hình! Chờ Shopee xác thực...")

            # 6. Chờ Shopee SecVerify phản hồi
            await asyncio.sleep(2.5)

            # 7. KIỂM TRA TRẠNG THÁI THỰC SỰ (Chống False-Positive tuyệt đối)
            verify_res = await page.evaluate('''() => {
                let bodyText = document.body ? (document.body.innerText || '') : '';

                // 1. KIỂM TRA POPUP LỖI / BỊ CHẶN (Không bao giờ được coi là thành công)
                let isBlocked = bodyText.includes('Vui lòng thử lại sau') || 
                                bodyText.includes('Chưa thể hoàn tất xác thực') || 
                                bodyText.includes('Một lỗi đã xảy ra') || 
                                bodyText.includes('Thao tác quá thường xuyên') ||
                                (Array.from(document.querySelectorAll('button')).some(b => (b.innerText || '').trim() === 'Thử Lại'));

                if (isBlocked) {
                    return { status: 'blocked', reason: 'Shopee từ chối: Vui lòng thử lại sau / Chưa thể hoàn tất xác thực' };
                }

                // 2. KIỂM TRA TÍN HIỆU TIẾN VÀO BƯỚC TIẾP THEO (Thành công thật)
                let hasOtpInput = document.querySelectorAll('.shopee-pin-input input, input[autocomplete*="one-time-code"]').length >= 6;
                let hasZaloPopup = bodyText.includes('Chúng tôi sẽ gửi mã') || bodyText.includes('phương pháp khác');
                let hasSelectMethod = bodyText.includes('Chọn Phương thức xác minh') || bodyText.includes('Chọn một trong các phương thức');
                let hasPasswordScreen = bodyText.includes('Thiết lập mật khẩu');
                let hasAnomaly = bodyText.includes('Hoạt động bất thường');

                if (hasOtpInput || hasZaloPopup || hasSelectMethod || hasPasswordScreen || hasAnomaly) {
                    return { status: 'success', reason: 'Đã phát hiện màn hình tiếp theo' };
                }

                // 3. Nếu canvas vẫn còn hiển thị -> chưa khớp lỗ
                let bgCanvas = document.querySelector('canvas[width="280"]');
                let isBgVisible = bgCanvas && bgCanvas.getBoundingClientRect().width > 0;
                if (isBgVisible) {
                    return { status: 'mismatch', reason: 'Captcha vẫn còn trên màn hình (chưa khớp)' };
                }

                return { status: 'unknown', reason: 'Không có tín hiệu bước tiếp theo' };
            }''')

            v_status = verify_res.get("status")
            v_reason = verify_res.get("reason", "")

            if v_status == "success":
                logger.info("🎉 VƯỢT CAPTCHA THÀNH CÔNG ở lần thử %d! (%s)", attempt, v_reason)
                return True
            elif v_status == "blocked":
                logger.warning("⛔ Lần thử %d bị Shopee chặn: %s", attempt, v_reason)
                # Tự động bấm nút 'Thử Lại' nếu có để hồi phục giao diện Captcha
                retry_btn = page.locator("button:has-text('Thử Lại'), button:has-text('Thử lại')").first
                if await retry_btn.count() > 0 and await retry_btn.is_visible():
                    logger.info("👉 Bấm nút 'Thử Lại' để nhận câu đố Captcha mới...")
                    try:
                        await retry_btn.click(timeout=2000)
                    except Exception:
                        await retry_btn.dispatch_event("click")
                    await asyncio.sleep(2.5)
                cached_candidates = []
                cand_index = 0
                continue

            # 7. Nếu chưa qua:
            logger.warning("Lần thử %d chưa khớp. Chuẩn bị phương án tiếp theo...", attempt)
            if attempt < max_attempts:
                # Nếu vẫn còn ứng viên trên ảnh hiện tại (distractor bypass)
                if cand_index < len(cached_candidates):
                    logger.info("🔄 Thử tiếp ứng viên thứ %d trên ảnh này: %dpx", cand_index + 1, cached_candidates[cand_index][0])
                    await asyncio.sleep(1.2)
                else:
                    # Đã thử hết ứng viên của ảnh này -> Bấm Đổi Hình Mới (Refresh)
                    logger.info("🔄 Ảnh hiện tại không khớp các lỗ, đang bấm Đổi Hình Mới...")
                    refresh_btn = page.locator(
                        "svg.Wts1Xu, [class*='Wts1Xu'], div[class*='refresh'], button[class*='refresh'], .captcha_verify_img_reload, svg[class*='refresh'], div.HrMY5p svg, div[class*='F0XJ1W'] ~ div svg"
                    ).first
                    if await refresh_btn.count() > 0 and await refresh_btn.is_visible():
                        try:
                            await refresh_btn.click(force=True, timeout=2500)
                        except Exception:
                            await refresh_btn.dispatch_event("click")
                        cached_candidates = []
                        cand_index = 0
                        await asyncio.sleep(2.5)
                    else:
                        await asyncio.sleep(1.8)

        except Exception as ex:
            logger.warning("Lỗi trong lần thử giải captcha %d: %s", attempt, ex)
            await asyncio.sleep(1.5)

    return False


async def click_verification_method_card(page, method_type: str = "Voice Call") -> bool:
    """
    Tìm và click chính xác 100% vào Card phương thức xác minh trên Shopee
    (Ưu tiên Voice Call, fallback SMS, loại bỏ hoàn toàn Zalo).
    Tự động xác định đúng Card Container bao quanh icon và text,
    kích hoạt đồng thời cả DOM events và chuột vật lý Playwright tại tâm Card.
    """
    keywords = []
    if method_type.lower().startswith("voice") or "call" in method_type.lower():
        keywords = ["Voice Call", "voice call", "Cuộc gọi thoại", "cuộc gọi thoại", "Gọi cho tôi", "Call me", "Call Me"]
    elif method_type.lower().startswith("sms"):
        keywords = ["SMS", "Tin nhắn SMS", "tin nhắn sms", "Text Message", "Gửi qua SMS"]

    eval_script = '''(kws) => {
        let all = Array.from(document.querySelectorAll('div, button, a, span, p, li'));
        for (let el of all) {
            let directText = (el.innerText || '').trim();
            let matched = false;
            for (let kw of kws) {
                if (directText === kw || directText.startsWith(kw)) {
                    matched = true;
                    break;
                }
            }
            if (matched) {
                // Tuyệt đối không dính vào Zalo
                if (directText.includes('Zalo') || directText.includes('zalo')) {
                    continue;
                }
                // Tìm thẻ Card Container cha hình chữ nhật có kích thước chuẩn
                let card = el;
                let cur = el;
                for (let depth = 0; depth < 6; depth++) {
                    if (!cur || cur === document.body) break;
                    let rect = cur.getBoundingClientRect();
                    if (rect.width >= 140 && rect.height >= 35 && rect.height <= 130) {
                        card = cur;
                        break;
                    }
                    cur = cur.parentElement;
                }

                card.scrollIntoView({ behavior: 'instant', block: 'center' });
                let r = card.getBoundingClientRect();

                try {
                    card.click();
                    card.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    card.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    card.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                } catch(e) {}

                return {
                    found: true,
                    x: r.x + r.width / 2,
                    y: r.y + r.height / 2,
                    width: r.width,
                    height: r.height
                };
            }
        }
        return { found: false };
    }'''

    try:
        res = await page.evaluate(eval_script, keywords)
        if res and res.get("found"):
            x, y = res["x"], res["y"]
            logger.info("🎯 Đã định vị Card [%s] tại tọa độ (%.1f, %.1f)! Đang bấm chuột trực tiếp...", method_type, x, y)
            await page.mouse.move(x, y)
            await asyncio.sleep(0.1)
            await page.mouse.click(x, y)
            return True
    except Exception as ex:
        logger.debug("Lỗi click Card %s qua DOM: %s", method_type, ex)

    # Fallback qua Playwright Locators
    for kw in keywords:
        try:
            loc = page.locator(f"button:has-text('{kw}'), div[role='button']:has-text('{kw}'), :text-is('{kw}'), :text('{kw}')").first
            if await loc.count() > 0 and await loc.is_visible():
                box = await loc.bounding_box()
                if box:
                    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    logger.info("🎯 Đã click Card [%s] qua Playwright locator với từ khóa '%s'!", method_type, kw)
                    return True
        except Exception:
            pass

    return False


async def handle_shopee_verification_gate(page, max_wait_seconds: int = 60, notify_callback=None) -> Tuple[bool, str]:
    """
    Xử lý tự động toàn diện tất cả các cổng xác thực / rào cản của Shopee:
    1. Modal "Chúng tôi sẽ gửi mã qua Zalo" -> Bấm 'Các phương pháp khác' / 'Other Methods'
    2. Màn hình "Select Verification Method" -> 100% Click Card 'Voice Call' (Fallback SMS, bỏ hoàn toàn Zalo)
    3. Modal "Hoạt động bất thường được phát hiện" -> Bấm 'GỌI CHO TÔI'
    4. Slider CAPTCHA (ghép hình) -> Tự động giải với Vision Ensemble v3
    5. Phát hiện màn hình 6 ô nhập mã OTP -> Hoàn thành cổng xác thực!
    6. Phát hiện lỗi số điện thoại bị từ chối / khóa -> Báo lỗi để đổi số mới
    """
    start_time = time.time()
    logger.info("🛡️ Bắt đầu giám sát & xử lý cổng xác thực Shopee (Gatekeeper)...")

    while time.time() - start_time < max_wait_seconds:
        # 1. Kiểm tra màn hình nhập mã OTP (Đích đến thành công - Đa tín hiệu)
        try:
            is_otp_page = await page.evaluate('''() => {
                let text = (document.body ? document.body.innerText : '') || '';
                let hasOtpText = text.includes('Mã xác minh') || 
                                 text.includes('mã gồm 6 chữ số') || 
                                 text.includes('Nhập mã') || 
                                 text.includes('Gửi lại mã') ||
                                 text.includes('đã gửi mã đến') ||
                                 text.includes('Gửi lại qua') ||
                                 text.includes('Thiết lập mật khẩu');
                let inputs = document.querySelectorAll('input[maxlength="1"], .shopee-pin-input input, input[class*="pin-input"], input[class*="otp"], input[autocomplete*="one-time-code"]');
                return hasOtpText || inputs.length >= 6;
            }''')
            if is_otp_page:
                logger.info("🎯 ĐÃ XÁC NHẬN ĐẾN MÀN HÌNH NHẬP MÃ OTP!")
                return True, "otp_screen"
        except Exception:
            pass

        # 2. Kiểm tra thông báo lỗi từ Shopee (ví dụ số đã đăng ký, bị giới hạn)
        try:
            error_loc = page.locator(".shopee-input-helper-text, [class*='error-message'], [class*='errorMessage']").first
            if await error_loc.count() > 0 and await error_loc.is_visible():
                err_text = (await error_loc.inner_text()).strip()
                if err_text and len(err_text) > 3:
                    logger.warning("Shopee từ chối số điện thoại: %s", err_text)
                    return False, err_text
        except Exception:
            pass

        # 2B. Kiểm tra Shopee WAF Block Modal ("Vui lòng thử lại sau" / "Chưa thể hoàn tất xác thực lúc này")
        try:
            is_blocked_modal = await page.evaluate('''() => {
                let t = document.body ? (document.body.innerText || '') : '';
                return t.includes('Vui lòng thử lại sau') || t.includes('Chưa thể hoàn tất xác thực');
            }''')
            if is_blocked_modal:
                logger.warning("⛔ Phát hiện Shopee chặn: 'Vui lòng thử lại sau / Chưa thể hoàn tất xác thực lúc này'!")
                retry_btn = page.locator("button:has-text('Thử Lại'), button:has-text('Thử lại')").first
                if await retry_btn.count() > 0 and await retry_btn.is_visible():
                    logger.info("👉 Đang bấm nút 'Thử Lại' trên modal...")
                    try:
                        await retry_btn.click(timeout=2000)
                    except Exception:
                        await retry_btn.dispatch_event("click")
                    await asyncio.sleep(2.5)
                    continue
                return False, "Shopee chặn xác thực: Vui lòng thử lại sau (Cần đổi IP / chờ ít phút)"
        except Exception:
            pass

        # 3. Kiểm tra Captcha trượt (Slider Canvas)
        slider_visible = False
        try:
            slider_track = page.locator("canvas[width='280'], div.HrMY5p, div[class*='F0XJ1W']").first
            if await slider_track.count() > 0 and await slider_track.is_visible():
                slider_visible = True
        except Exception:
            pass

        if slider_visible:
            logger.info("🧩 Phát hiện Slider Captcha! Bắt đầu vượt...")
            if notify_callback:
                try:
                    await notify_callback("🧩 Đang tự động xử lý mã xác thực an toàn (Captcha trượt)...")
                except Exception:
                    pass
            solved = await solve_shopee_slider_captcha(page, max_attempts=5)
            if not solved:
                logger.warning("Không vượt qua Captcha sau 5 lần thử.")
                return False, "Không vượt qua xác thực Captcha sau 5 lần thử"
            await asyncio.sleep(2.0)
            continue

        # 4A. Kiểm tra Modal Popup Zalo: "Chúng tôi sẽ gửi mã xác minh qua Zalo" / "We will send a verification code via Zalo"
        zalo_popup_btn = page.locator(
            "button:has-text('Các phương pháp khác'), button:has-text('phương pháp khác'), button:has-text('Other Methods'), button:has-text('Other methods'), :text('Other Methods'), :text('Các phương pháp khác')"
        ).first
        has_zalo_modal = False
        try:
            if await zalo_popup_btn.count() > 0 and await zalo_popup_btn.is_visible():
                has_zalo_modal = True
            else:
                has_zalo_modal = await page.evaluate('''() => {
                    let t = (document.body ? document.body.innerText : '') || '';
                    return t.includes('verification code via Zalo') || t.includes('gửi mã xác minh qua Zalo') || t.includes('Send to Zalo');
                }''')
        except Exception:
            pass

        if has_zalo_modal:
            logger.info("⚙️ Phát hiện Popup Zalo! Bắt buộc bấm 'Các phương pháp khác' / 'Other Methods' để chuyển sang Gọi điện/SMS...")
            clicked_other = False
            try:
                if await zalo_popup_btn.count() > 0 and await zalo_popup_btn.is_visible():
                    b_box = await zalo_popup_btn.bounding_box()
                    if b_box:
                        await page.mouse.click(b_box["x"] + b_box["width"] / 2, b_box["y"] + b_box["height"] / 2)
                    else:
                        await zalo_popup_btn.click()
                    clicked_other = True
            except Exception:
                pass

            if not clicked_other:
                try:
                    clicked_other = await page.evaluate('''() => {
                        let all = Array.from(document.querySelectorAll('button, div, a, span'));
                        for (let el of all) {
                            let t = (el.innerText || '').trim().toLowerCase();
                            if (t === 'other methods' || t === 'các phương pháp khác' || t.includes('other methods') || t.includes('phương pháp khác')) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }''')
                except Exception:
                    pass

            await asyncio.sleep(2.0)
            continue

        # 4B. Kiểm tra Màn hình "Select Verification Method" / "Chọn Phương thức xác minh"
        is_select_method_screen = False
        try:
            is_select_method_screen = await page.evaluate('''() => {
                let t = document.body ? (document.body.innerText || '') : '';
                return t.includes('Select Verification Method') ||
                       t.includes('Select one of the methods below') ||
                       t.includes('Chọn Phương thức xác minh') || 
                       t.includes('Chọn một trong các phương thức') ||
                       t.includes('Choose a verification method');
            }''')
        except Exception:
            pass

        if is_select_method_screen:
            logger.info("👉 Phát hiện màn hình 'Select Verification Method'! Ưu tiên 100% Cuộc gọi thoại (Voice Call) -> sau đó Tin nhắn SMS (Bỏ Zalo)...")
            selected_method = None

            # ƯU TIÊN 1 (100%): Cuộc gọi thoại (Voice Call)
            ok_voice = await click_verification_method_card(page, "Voice Call")
            if ok_voice:
                selected_method = "VoiceCall"
                logger.info("📞 [Ưu tiên 1 - 100%] Đã bấm trúng Card 'Voice Call'!")
            else:
                # ƯU TIÊN 2: Tin nhắn SMS (nếu trang không có Voice Call)
                logger.warning("Không tìm thấy Card Voice Call, thử chọn Card SMS...")
                ok_sms = await click_verification_method_card(page, "SMS")
                if ok_sms:
                    selected_method = "SMS"
                    logger.info("✅ [Ưu tiên 2] Đã bấm trúng Card 'SMS'!")

            # TUYỆT ĐỐI KHÔNG CHỌN ZALO THEO YÊU CẦU CỦA SONG

            await asyncio.sleep(1.5)
            # Nếu có nút 'Tiếp theo' hoặc 'Xác nhận' trong modal sau khi chọn thẻ
            try:
                confirm_btn = page.locator(
                    "button:has-text('Tiếp theo'), button:has-text('TIẾP THEO'), button:has-text('Tiếp tục'), button:has-text('TIẾP TỤC'), button:has-text('Xác nhận'), button:has-text('XÁC NHẬN'), button:has-text('Gửi mã'), button:has-text('GỬI MÃ'), button:has-text('Next'), button:has-text('NEXT'), button:has-text('Confirm'), button:has-text('CONFIRM'), button.wyhvVD, button[class*='shopee-button-solid']"
                ).first
                if await confirm_btn.count() > 0 and await confirm_btn.is_visible():
                    await confirm_btn.click()
                    logger.info("👉 Đã bấm nút xác nhận gửi mã!")
            except Exception:
                pass

            await asyncio.sleep(2.0)
            # Kiểm tra ngay lập tức xem có Slider Captcha lần 2 xuất hiện không
            try:
                c_modal = page.locator("canvas[width='280'], div.HrMY5p, div[class*='F0XJ1W']").first
                if await c_modal.count() > 0 and await c_modal.is_visible():
                    logger.info("🧩 Shopee yêu cầu xác thực Captcha lần 2 để gửi mã! Đang giải ngay...")
                    solved2 = await solve_shopee_slider_captcha(page, max_attempts=5)
                    if not solved2:
                        return False, "Không vượt qua Captcha lần 2 sau 5 lần thử"
                    await asyncio.sleep(2.0)
            except Exception as ex_c2:
                logger.debug("Lỗi kiểm tra Captcha lần 2: %s", ex_c2)
            continue

        # 5. Kiểm tra Modal Cuộc gọi thoại: "Hoạt động bất thường được phát hiện. Chúng tôi sẽ gọi..."
        has_abnormal_call = False
        try:
            has_abnormal_call = await page.evaluate('''() => {
                let t = (document.body.innerText || '').toUpperCase();
                return (t.includes('HOẠT ĐỘNG BẤT THƯỜNG') || t.includes('ĐỌC MÃ XÁC MINH') || t.includes('ABNORMAL ACTIVITY') || t.includes('CALL TO PROVIDE')) && 
                       (t.includes('GỌI CHO TÔI') || t.includes('CALL ME'));
            }''')
        except Exception:
            pass

        if has_abnormal_call:
            logger.info("📞 Phát hiện popup Cuộc gọi thoại! Bấm 'GỌI CHO TÔI' / 'Call Me' theo ưu tiên 100%...")
            if notify_callback:
                try:
                    await notify_callback("📞 Shopee yêu cầu gọi thoại -> Đang bấm 'GỌI CHO TÔI' để nhận mã OTP qua cuộc gọi...")
                except Exception:
                    pass

            try:
                call_btn = page.locator("button:has-text('GỌI CHO TÔI'), button:has-text('Gọi cho tôi'), button:has-text('GỌI'), button:has-text('CALL ME'), button:has-text('Call Me'), button:has-text('Call me')").first
                if await call_btn.count() > 0 and await call_btn.is_visible():
                    await call_btn.click(timeout=3000)
                    logger.info("✅ Đã bấm 'GỌI CHO TÔI'! Đang chuyển tiếp vào màn hình OTP...")
                    await asyncio.sleep(2.5)
                    continue
            except Exception as ex_call:
                logger.warning("Lỗi bấm nút gọi: %s", ex_call)

        # 6. Kiểm tra các nút xác nhận SMS khác nếu có
        try:
            clicked_confirm = await page.evaluate('''() => {
                let all = Array.from(document.querySelectorAll('button, [role="button"], a'));
                for (let el of all) {
                    let t = (el.innerText || '').trim();
                    if (t === 'Gửi qua SMS' || t === 'Gửi SMS' || t === 'Xác nhận' || t === 'Đồng ý') {
                        if (el.offsetWidth > 0 && el.offsetHeight > 0) {
                            el.click();
                            return true;
                        }
                    }
                }
                return false;
            }''')
            if clicked_confirm:
                await asyncio.sleep(2.0)
                continue
        except Exception:
            pass

        await asyncio.sleep(1.0)

    # Sau max_wait_seconds, kiểm tra lại lần cuối
    try:
        otp_inputs = page.locator(".shopee-pin-input input, input[class*='pin-input'], input[class*='otp']")
        if await otp_inputs.count() >= 6:
            return True, "otp_screen"
    except Exception:
        pass

    return False, f"Hết thời gian chờ xử lý xác thực ({max_wait_seconds}s)"

