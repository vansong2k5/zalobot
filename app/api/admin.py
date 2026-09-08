"""
Router Quản Trị Hệ Thống (Admin APIs).
Bảo vệ bằng Header 'X-Admin-Key' khớp với ADMIN_API_KEY trong file .env.
Bao gồm:
- Quản lý sản phẩm (thêm, sửa, xem danh sách, tồn kho)
- Quản lý nạp kho tài khoản số
- Quản lý yêu cầu rút tiền hoa hồng (duyệt, từ chối và hoàn tiền)
- Gửi tin nhắn thông báo hàng loạt (Broadcast)
"""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import ADMIN_API_KEY
from app.db import get_db
from app.models import Product, ProductStock, Withdrawal, User
from app.schemas import (
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    StockImportRequest,
    StockCountResponse,
    WithdrawalProcessRequest,
    BroadcastRequest,
)
from app.services import send_zalo_message

router = APIRouter(prefix="/admin", tags=["Quản Trị (Admin)"])


def verify_admin(x_admin_key: str = Header(None)):
    """
    Dependency kiểm tra quyền Admin qua Header 'X-Admin-Key'.
    """
    if not x_admin_key or x_admin_key.strip() != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Bạn không có quyền truy cập Admin (Sai X-Admin-Key)")
    return True


# ==============================================================================
# 1. QUẢN LÝ SẢN PHẨM (PRODUCTS)
# ==============================================================================

@router.get("/products", response_model=List[ProductResponse], dependencies=[Depends(verify_admin)])
def list_products(db: Session = Depends(get_db)):
    """
    Xem danh sách tất cả các sản phẩm kèm số lượng tồn kho còn lại.
    """
    products = db.query(Product).order_by(Product.id.asc()).all()
    results = []
    for p in products:
        # Đếm tồn kho còn sẵn sàng
        available = db.query(ProductStock).filter(
            ProductStock.product_id == p.id,
            ProductStock.status == "available"
        ).count()
        results.append(ProductResponse(
            id=p.id,
            product_name=p.product_name,
            price=p.price,
            description=p.description,
            commission_rate=p.commission_rate,
            status=p.status,
            available_stock=available,
            created_at=p.created_at
        ))
    return results


@router.post("/products", response_model=ProductResponse, dependencies=[Depends(verify_admin)])
def create_product(data: ProductCreate, db: Session = Depends(get_db)):
    """
    Tạo một gói sản phẩm / tài khoản mới.
    """
    new_product = Product(
        product_name=data.product_name,
        price=data.price,
        description=data.description,
        commission_rate=data.commission_rate,
        status=data.status
    )
    db.add(new_product)
    db.commit()
    db.refresh(new_product)
    return ProductResponse(
        id=new_product.id,
        product_name=new_product.product_name,
        price=new_product.price,
        description=new_product.description,
        commission_rate=new_product.commission_rate,
        status=new_product.status,
        available_stock=0,
        created_at=new_product.created_at
    )


@router.patch("/products/{product_id}", response_model=ProductResponse, dependencies=[Depends(verify_admin)])
def update_product(product_id: int, data: ProductUpdate, db: Session = Depends(get_db)):
    """
    Cập nhật thông tin gói sản phẩm (giá, tên, mô tả, trạng thái).
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm")

    if data.product_name is not None:
        product.product_name = data.product_name
    if data.price is not None:
        product.price = data.price
    if data.description is not None:
        product.description = data.description
    if data.commission_rate is not None:
        product.commission_rate = data.commission_rate
    if data.status is not None:
        product.status = data.status

    db.commit()
    db.refresh(product)

    available = db.query(ProductStock).filter(
        ProductStock.product_id == product.id,
        ProductStock.status == "available"
    ).count()

    return ProductResponse(
        id=product.id,
        product_name=product.product_name,
        price=product.price,
        description=product.description,
        commission_rate=product.commission_rate,
        status=product.status,
        available_stock=available,
        created_at=product.created_at
    )


# ==============================================================================
# 2. QUẢN LÝ KHO TÀI KHOẢN (STOCKS)
# ==============================================================================

@router.post("/stock/import", dependencies=[Depends(verify_admin)])
def import_stock(data: StockImportRequest, db: Session = Depends(get_db)):
    """
    Nạp danh sách tài khoản (email/mật khẩu) vào kho của một sản phẩm.
    """
    product = db.query(Product).filter(Product.id == data.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy sản phẩm có ID: {data.product_id}")

    count = 0
    for item in data.accounts:
        stock = ProductStock(
            product_id=data.product_id,
            account=item.account.strip(),
            password=item.password.strip(),
            sdt=item.sdt.strip() if item.sdt else None,
            cookie_spc_f=item.cookie_spc_f,
            cookie_spc_st=item.cookie_spc_st,
            status="available"
        )
        db.add(stock)
        count += 1

    db.commit()
    return {
        "status": "ok",
        "message": f"Đã nạp thành công {count} tài khoản vào sản phẩm '{product.product_name}'",
        "imported_count": count
    }


@router.get("/stock/{product_id}/count", response_model=StockCountResponse, dependencies=[Depends(verify_admin)])
def count_stock(product_id: int, db: Session = Depends(get_db)):
    """
    Kiểm tra số lượng tài khoản còn trong kho của sản phẩm.
    """
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Không tìm thấy sản phẩm")

    available = db.query(ProductStock).filter(
        ProductStock.product_id == product_id,
        ProductStock.status == "available"
    ).count()

    return StockCountResponse(
        product_id=product.id,
        product_name=product.product_name,
        available_count=available
    )


# ==============================================================================
# 3. QUẢN LÝ RÚT TIỀN HOA HỒNG (WITHDRAWALS)
# ==============================================================================

@router.get("/withdrawals", dependencies=[Depends(verify_admin)])
def list_withdrawals(status: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Xem danh sách các yêu cầu rút tiền của khách (lọc theo trạng thái 'pending', 'approved', 'rejected').
    """
    query = db.query(Withdrawal)
    if status:
        query = query.filter(Withdrawal.status == status)
    withdrawals = query.order_by(Withdrawal.id.desc()).all()

    return [
        {
            "id": w.id,
            "user_id": w.user_id,
            "zalo_id": w.user.user_id if w.user else None,
            "display_name": w.user.display_name if w.user else None,
            "amount": int(w.amount),
            "bank_name": w.bank_name,
            "bank_account": w.bank_account,
            "account_holder": w.account_holder,
            "status": w.status,
            "admin_note": w.admin_note,
            "created_at": w.created_at,
            "processed_at": w.processed_at,
        }
        for w in withdrawals
    ]


@router.post("/withdrawals/{withdraw_id}/approve", dependencies=[Depends(verify_admin)])
def approve_withdrawal(withdraw_id: int, data: WithdrawalProcessRequest, db: Session = Depends(get_db)):
    """
    Duyệt yêu cầu rút tiền (sau khi Admin đã chuyển khoản ngoài đời thực cho khách).
    """
    withdrawal = db.query(Withdrawal).filter(Withdrawal.id == withdraw_id).first()
    if not withdrawal:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu rút tiền")

    if withdrawal.status != "pending":
        raise HTTPException(status_code=400, detail=f"Yêu cầu này đã được xử lý trước đó ({withdrawal.status})")

    withdrawal.status = "approved"
    withdrawal.admin_note = data.admin_note or "Đã chuyển khoản thành công"
    withdrawal.processed_at = datetime.utcnow()
    db.commit()

    # Báo qua Zalo cho khách biết
    if withdrawal.user:
        msg = (
            f"✅ YÊU CẦU RÚT TIỀN ĐÃ ĐƯỢC DUYỆT!\n"
            f"Số tiền: {int(withdrawal.amount):,} VNĐ\n"
            f"Ngân hàng: {withdrawal.bank_name} - {withdrawal.bank_account}\n"
            f"Vui lòng kiểm tra tài khoản ngân hàng của bạn. Cảm ơn bạn!"
        )
        send_zalo_message(withdrawal.user.user_id, msg)

    return {"status": "ok", "message": f"Đã duyệt yêu cầu rút tiền #{withdraw_id}"}


@router.post("/withdrawals/{withdraw_id}/reject", dependencies=[Depends(verify_admin)])
def reject_withdrawal(withdraw_id: int, data: WithdrawalProcessRequest, db: Session = Depends(get_db)):
    """
    Từ chối yêu cầu rút tiền (Tự động hoàn lại số tiền vào số dư hoa hồng cho khách).
    """
    withdrawal = db.query(Withdrawal).filter(Withdrawal.id == withdraw_id).first()
    if not withdrawal:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu rút tiền")

    if withdrawal.status != "pending":
        raise HTTPException(status_code=400, detail=f"Yêu cầu này đã được xử lý trước đó ({withdrawal.status})")

    # Hoàn trả lại số dư cho khách
    if withdrawal.user:
        withdrawal.user.balance += withdrawal.amount

    withdrawal.status = "rejected"
    withdrawal.admin_note = data.admin_note or "Từ chối rút tiền (thông tin ngân hàng không hợp lệ)"
    withdrawal.processed_at = datetime.utcnow()
    db.commit()

    # Báo qua Zalo cho khách
    if withdrawal.user:
        msg = (
            f"❌ YÊU CẦU RÚT TIỀN BỊ TỪ CHỐI!\n"
            f"Số tiền: {int(withdrawal.amount):,} VNĐ\n"
            f"Lý do: {withdrawal.admin_note}\n"
            f"💰 Số tiền đã được hoàn lại vào số dư hoa hồng của bạn ({int(withdrawal.user.balance):,} VNĐ)."
        )
        send_zalo_message(withdrawal.user.user_id, msg)

    return {"status": "ok", "message": f"Đã từ chối và hoàn tiền yêu cầu rút tiền #{withdraw_id}"}


# ==============================================================================
# 4. GỬI TIN NHẮN HÀNG LOẠT (BROADCAST)
# ==============================================================================

@router.post("/broadcast", dependencies=[Depends(verify_admin)])
def broadcast_message(data: BroadcastRequest, db: Session = Depends(get_db)):
    """
    Gửi tin nhắn thông báo hoặc chương trình khuyến mãi đến tất cả khách hàng đang active.
    """
    users = db.query(User).filter(User.status == "active").all()
    success_count = 0
    fail_count = 0

    for u in users:
        ok = send_zalo_message(u.user_id, data.message)
        if ok:
            success_count += 1
        else:
            fail_count += 1

    return {
        "status": "ok",
        "total_users": len(users),
        "sent_successfully": success_count,
        "failed": fail_count
    }
