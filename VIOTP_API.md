# TÀI LIỆU API VIOTP (viotp.com)

Hệ thống API thuê số điện thoại nhận mã OTP tự động của **ViOTP**.

---

## 1. Thông tin chung & Xác thực

* **Base URL:** `https://api.viotp.com`
* **Phương thức xác thực:** Truyền API Key thông qua query parameter `token` ở mỗi request.
* **Định dạng dữ liệu:** JSON (`Content-Type: application/json`).
* **Lấy API Key:** Đăng nhập vào tài khoản trên [viotp.com](https://viotp.com) -> vào mục **Tài khoản** / **Cấu hình API** để lấy API Token.

---

## 2. Danh sách Endpoints

### 2.1. Kiểm tra số dư tài khoản
Lấy số dư hiện tại trong ví ViOTP của bạn.

* **Phương thức:** `GET`
* **Endpoint:** `/users/balance`
* **Query Parameters:**
  | Tham số | Kiểu dữ liệu | Bắt buộc | Mô tả |
  | :--- | :--- | :--- | :--- |
  | `token` | `string` | Có | API Token của bạn |

* **Ví dụ Request:**
  ```http
  GET https://api.viotp.com/users/balance?token=YOUR_API_TOKEN
  ```

* **Response mẫu:**
  ```json
  {
    "status_code": 200,
    "message": "Success",
    "success": true,
    "data": {
      "balance": 50000
    }
  }
  ```

---

### 2.2. Lấy danh sách các dịch vụ & ID dịch vụ
Lấy toàn bộ danh sách dịch vụ được hỗ trợ kèm `id` để phục vụ cho việc tạo yêu cầu thuê số.

* **Phương thức:** `GET`
* **Endpoint:** `/service/get`
* **Query Parameters:**
  | Tham số | Kiểu dữ liệu | Bắt buộc | Mô tả |
  | :--- | :--- | :--- | :--- |
  | `token` | `string` | Có | API Token của bạn |

* **Ví dụ Request:**
  ```http
  GET https://api.viotp.com/service/get?token=YOUR_API_TOKEN
  ```

* **Response mẫu:**
  ```json
  {
    "status_code": 200,
    "message": "Success",
    "success": true,
    "data": [
      {
        "id": 1,
        "name": "Shopee",
        "price": 1000
      },
      {
        "id": 2,
        "name": "Zalo",
        "price": 2500
      },
      {
        "id": 3,
        "name": "Telegram",
        "price": 3000
      },
      {
        "id": 4,
        "name": "Tiktok",
        "price": 1500
      }
    ]
  }
  ```

---

### 2.3. Thuê số điện thoại nhận OTP (Request Number)
Tạo yêu cầu thuê một số điện thoại mới cho một dịch vụ cụ thể.

* **Phương thức:** `GET`
* **Endpoint:** `/request/getv2`
* **Query Parameters:**
  | Tham số | Kiểu dữ liệu | Bắt buộc | Mô tả |
  | :--- | :--- | :--- | :--- |
  | `token` | `string` | Có | API Token của bạn |
  | `serviceId` | `int` | Có | ID dịch vụ (ví dụ: `1` cho Shopee) |
  | `network` | `string` | Không | Nhà mạng ưu tiên: `VIETTEL`, `MOBIFONE`, `VINAPHONE`, `VIETNAMOBILE` |
  | `prefix` | `string` | Không | Đầu số mong muốn (vd: `098`, `090`...) |

* **Ví dụ Request:**
  ```http
  GET https://api.viotp.com/request/getv2?token=YOUR_API_TOKEN&serviceId=1
  ```

* **Response thành công:**
  ```json
  {
    "status_code": 200,
    "message": "Tạo yêu cầu thành công !",
    "success": true,
    "data": {
      "request_id": 58266507,
      "phone_number": "0587988009",
      "re_phone_number": "0587988009",
      "countryISO": "VN",
      "countryCode": "84",
      "balance": 49000
    }
  }
  ```

* **Response lỗi khi hết số:**
  ```json
  {
    "status_code": 400,
    "message": "Hết số trên hệ thống",
    "success": false,
    "data": null
  }
  ```

---

### 2.4. Kiểm tra tin nhắn và lấy mã OTP
Dùng `request_id` nhận được ở bước trên để kiểm tra xem tin nhắn SMS chứa mã OTP đã về chưa.

* **Phương thức:** `GET`
* **Endpoint:** `/session/getv2`
* **Query Parameters:**
  | Tham số | Kiểu dữ liệu | Bắt buộc | Mô tả |
  | :--- | :--- | :--- | :--- |
  | `token` | `string` | Có | API Token của bạn |
  | `requestId` | `int` | Có | ID của yêu cầu nhận được từ bước thuê số |

* **Ví dụ Request:**
  ```http
  GET https://api.viotp.com/session/getv2?requestId=58266507&token=YOUR_API_TOKEN
  ```

* **Response khi đang chờ tin nhắn (`Status: 0`):**
  ```json
  {
    "status_code": 200,
    "message": "Đang chờ tin nhắn",
    "success": true,
    "data": {
      "ID": 58266507,
      "Phone": "0587988009",
      "ServiceId": 1,
      "ServiceName": "Shopee",
      "Status": 0,
      "CreatedTime": "2026-09-10T12:00:00",
      "Code": null,
      "SmsContent": null,
      "IsSound": false
    }
  }
  ```

* **Response khi đã nhận được mã OTP thành công (`Status: 1`):**
  ```json
  {
    "status_code": 200,
    "message": "Thành công",
    "success": true,
    "data": {
      "ID": 58266507,
      "Phone": "0587988009",
      "ServiceId": 1,
      "ServiceName": "Shopee",
      "Status": 1,
      "CreatedTime": "2026-09-10T12:00:00",
      "Code": "849201",
      "SmsContent": "Ma OTP Shopee cua ban la 849201. Ma nay se het han sau 15 phut.",
      "IsSound": false
    }
  }
  ```

* **Response khi phiên hết hạn / Không nhận được tin (`Status: 2`):**
  ```json
  {
    "status_code": 200,
    "message": "Hết thời gian chờ",
    "success": true,
    "data": {
      "ID": 58266507,
      "Phone": "0587988009",
      "Status": 2,
      "Code": null,
      "SmsContent": null
    }
  }
  ```

---

## 3. Bảng mã trạng thái (Session Status)

Trong trường `data.Status` của API `/session/getv2`:

| Giá trị Status | Ý nghĩa | Hành động tiếp theo |
| :---: | :--- | :--- |
| **`0`** | Đang chờ tin nhắn SMS đến | Tiếp tục chờ khoảng 3-5s rồi gọi lại endpoint |
| **`1`** | Nhận OTP thành công | Trích xuất `data.Code` để hoàn tất xác minh |
| **`2`** | Hết thời gian chờ (Timeout) / Đã hủy | Hệ thống hoàn lại tiền, kết thúc phiên hoặc thử thuê số mới |

---

## 4. Code mẫu Python tích hợp hoàn chỉnh

Đoạn mã Python sử dụng thư viện `httpx` (hoặc `requests`) thực hiện đầy đủ luồng: Kiểm tra số dư -> Thuê số -> Polling chờ mã OTP.

```python
import time
import httpx

VIOTP_TOKEN = "YOUR_VIOTP_API_TOKEN"
BASE_URL = "https://api.viotp.com"

def get_balance():
    """Kiểm tra số dư tài khoản"""
    url = f"{BASE_URL}/users/balance"
    params = {"token": VIOTP_TOKEN}
    res = httpx.get(url, params=params).json()
    if res.get("status_code") == 200 and res.get("data"):
        return res["data"]["balance"]
    return None

def rent_phone_number(service_id: int):
    """Thuê số điện thoại theo ID dịch vụ (vd: 1 là Shopee)"""
    url = f"{BASE_URL}/request/getv2"
    params = {
        "token": VIOTP_TOKEN,
        "serviceId": service_id
    }
    res = httpx.get(url, params=params).json()
    if res.get("status_code") == 200 and res.get("data"):
        request_id = res["data"]["request_id"]
        phone_number = res["data"]["phone_number"]
        return request_id, phone_number
    else:
        raise Exception(f"Lỗi thuê số: {res.get('message')}")

def wait_for_otp(request_id: int, timeout: int = 120, poll_interval: int = 4):
    """
    Polling kiểm tra OTP cho đến khi nhận được hoặc hết timeout
    """
    url = f"{BASE_URL}/session/getv2"
    params = {
        "token": VIOTP_TOKEN,
        "requestId": request_id
    }
    
    start_time = time.time()
    print(f"[*] Bắt đầu chờ OTP cho request ID: {request_id}...")
    
    while time.time() - start_time < timeout:
        res = httpx.get(url, params=params).json()
        data = res.get("data")
        
        if data:
            status = data.get("Status")
            if status == 1:
                otp_code = data.get("Code")
                sms_content = data.get("SmsContent")
                print(f"[+] Đã nhận được OTP: {otp_code}")
                return otp_code, sms_content
            elif status == 2:
                print("[-] Phiên thuê số đã hết hạn hoặc bị hủy.")
                return None, None
        
        # Chờ trước khi kiểm tra lại
        time.sleep(poll_interval)
        
    print("[-] Hết thời gian chờ OTP (Timeout)!")
    return None, None

if __name__ == "__main__":
    balance = get_balance()
    print(f"Số dư ViOTP: {balance} VNĐ")
    
    # Ví dụ thuê số cho Shopee (serviceId = 1)
    try:
        req_id, phone = rent_phone_number(service_id=1)
        print(f"Đã thuê số: {phone} (Request ID: {req_id})")
        
        # Chờ OTP
        otp, sms = wait_for_otp(req_id, timeout=90)
        if otp:
            print(f"-> MÃ OTP: {otp}")
            print(f"-> Nội dung SMS: {sms}")
        else:
            print("-> Không nhận được OTP.")
    except Exception as e:
        print(f"Lỗi: {e}")
```
