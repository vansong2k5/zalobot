"""
Định nghĩa các Schemas Pydantic — Xác thực dữ liệu đầu vào và định dạng đầu ra cho API.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field


# ==============================================================================
# 1. SẢN PHẨM (PRODUCTS)
# ==============================================================================

class ProductCreate(BaseModel):
    """Schema tạo mới một sản phẩm."""
    product_name: str = Field(..., description="Tên sản phẩm", example="Netflix Premium 1 Tháng")
    price: Decimal = Field(..., gt=0, description="Giá bán VNĐ", example=69000)
    description: Optional[str] = Field(None, description="Mô tả chi tiết")
    commission_rate: Decimal = Field(default=Decimal("0.0"), ge=0, le=100, description="Tỷ lệ hoa hồng (%)")
    status: str = Field(default="active", description="active hoặc inactive")


class ProductUpdate(BaseModel):
    """Schema cập nhật sản phẩm."""
    product_name: Optional[str] = None
    price: Optional[Decimal] = None
    description: Optional[str] = None
    commission_rate: Optional[Decimal] = None
    status: Optional[str] = None


class ProductResponse(BaseModel):
    """Schema trả về thông tin sản phẩm kèm số lượng tồn kho."""
    id: int
    product_name: str
    price: Decimal
    description: Optional[str] = None
    commission_rate: Decimal
    status: str
    available_stock: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


# ==============================================================================
# 2. KHO HÀNG (STOCKS)
# ==============================================================================

class StockItemImport(BaseModel):
    """Một dòng tài khoản khi nạp vào kho."""
    account: str = Field(..., description="Email/Tên đăng nhập", example="user@gmail.com")
    password: str = Field(..., description="Mật khẩu", example="Pass123456")
    sdt: Optional[str] = Field(None, description="Số điện thoại")
    cookie_spc_f: Optional[str] = None
    cookie_spc_st: Optional[str] = None


class StockImportRequest(BaseModel):
    """Request nạp nhiều tài khoản vào kho của một sản phẩm."""
    product_id: int = Field(..., description="ID của sản phẩm cần nạp kho")
    accounts: List[StockItemImport] = Field(..., min_items=1, description="Danh sách tài khoản")


class StockCountResponse(BaseModel):
    """Số lượng hàng tồn kho của sản phẩm."""
    product_id: int
    product_name: str
    available_count: int


# ==============================================================================
# 3. ĐƠN HÀNG (ORDERS)
# ==============================================================================

class OrderResponse(BaseModel):
    """Thông tin đơn hàng."""
    id: int
    order_code: str
    user_id: int
    product_id: int
    quantity: int
    price: Decimal
    status: str
    account_delivered: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==============================================================================
# 4. YÊU CẦU RÚT TIỀN (WITHDRAWALS)
# ==============================================================================

class WithdrawalRequest(BaseModel):
    """Khách yêu cầu rút tiền qua Zalo hoặc API."""
    amount: Decimal = Field(..., gt=0, description="Số tiền muốn rút")
    bank_name: str = Field(..., description="Tên ngân hàng (vd: Vietcombank, MB, BIDV)")
    bank_account: str = Field(..., description="Số tài khoản nhận")
    account_holder: str = Field(..., description="Tên chủ tài khoản")


class WithdrawalProcessRequest(BaseModel):
    """Admin duyệt hoặc từ chối rút tiền."""
    admin_note: Optional[str] = Field(None, description="Ghi chú lý do xử lý")


# ==============================================================================
# 5. WEBHOOK SEPAY (CỔNG THANH TOÁN TỰ ĐỘNG)
# ==============================================================================

class SepayWebhookPayload(BaseModel):
    """
    Dữ liệu giao dịch Sepay gửi sang khi có tiền vào tài khoản ngân hàng.
    Tài liệu chính thức: https://developer.sepay.vn/webhook
    """
    id: int = Field(..., description="ID giao dịch trên Sepay")
    gateway: Optional[str] = None
    transactionDate: Optional[str] = None
    accountNumber: Optional[str] = None
    code: Optional[str] = None
    content: str = Field(..., description="Nội dung chuyển khoản (chứa mã đơn hàng DHxxxxxx)")
    transferType: str = Field(..., description="'in' = nhận tiền, 'out' = chuyển tiền đi")
    transferAmount: Decimal = Field(..., description="Số tiền giao dịch")
    accumulated: Optional[Decimal] = None
    subAccount: Optional[str] = None
    referenceCode: Optional[str] = None
    description: Optional[str] = None


# ==============================================================================
# 6. WEBHOOK ZALO BOT PLATFORM
# ==============================================================================

class ZaloSender(BaseModel):
    id: Optional[str] = None
    display_name: Optional[str] = "Khách"


class ZaloMessage(BaseModel):
    msg_id: Optional[str] = None
    text: Optional[str] = ""


class ZaloWebhookPayload(BaseModel):
    """
    Payload Zalo Bot Platform gửi sang khi người dùng nhắn tin cho Bot.
    """
    event_name: Optional[str] = "user_send_text"
    app_id: Optional[str] = None
    user_id_by_app: Optional[str] = None
    sender: Optional[ZaloSender] = None
    message: Optional[ZaloMessage] = None
    timestamp: Optional[int] = None


# ==============================================================================
# 7. GỬI TIN HÀNG LOẠT (BROADCAST)
# ==============================================================================

class BroadcastRequest(BaseModel):
    """Admin gửi thông báo cho toàn bộ hoặc nhóm khách hàng."""
    message: str = Field(..., min_length=1, description="Nội dung thông báo cần gửi")