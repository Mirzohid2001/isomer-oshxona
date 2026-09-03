from django.db import transaction
from django.utils import timezone

from kitchen.models import (
    ApprovalStatus,
    PurchaseOrder,
    StockChangeRequest,
)
from kitchen.services.audit import log_action
from kitchen.services.stock import StockError, adjust_stock, receive_stock, record_waste


@transaction.atomic
def submit_adjust_request(*, product, new_quantity, user, note=''):
    if user.is_staff:
        return adjust_stock(product=product, new_quantity=new_quantity, user=user, note=note), None
    req = StockChangeRequest.objects.create(
        request_type=StockChangeRequest.RequestType.ADJUST,
        product=product,
        quantity=abs(new_quantity - product.quantity),
        new_quantity=new_quantity,
        note=note,
        requested_by=user,
    )
    log_action(user, 'so‘rov_tuzatish', 'stock_change_request', req.pk, note)
    return None, req


@transaction.atomic
def submit_waste_request(*, product, quantity, user, note=''):
    if user.is_staff:
        return record_waste(product=product, quantity=quantity, user=user, note=note), None
    req = StockChangeRequest.objects.create(
        request_type=StockChangeRequest.RequestType.WASTE,
        product=product,
        quantity=quantity,
        note=note,
        requested_by=user,
    )
    log_action(user, 'so‘rov_chiqindi', 'stock_change_request', req.pk, note)
    return None, req


@transaction.atomic
def review_change_request(*, request_obj, reviewer, approve=True):
    req = StockChangeRequest.objects.select_for_update().select_related('product').get(pk=request_obj.pk)
    if req.status != ApprovalStatus.PENDING:
        raise StockError('So‘rov allaqachon ko‘rib chiqilgan.')
    if not reviewer.is_staff:
        raise StockError('Faqat admin tasdiqlashi mumkin.')

    req.reviewed_by = reviewer
    req.reviewed_at = timezone.now()
    if not approve:
        req.status = ApprovalStatus.REJECTED
        req.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
        log_action(reviewer, 'rad', 'stock_change_request', req.pk, req.note)
        return req

    if req.request_type == StockChangeRequest.RequestType.ADJUST:
        adjust_stock(
            product=req.product,
            new_quantity=req.new_quantity,
            user=reviewer,
            note=req.note or f'So‘rov #{req.pk}',
        )
    else:
        record_waste(
            product=req.product,
            quantity=req.quantity,
            user=reviewer,
            note=req.note or f'So‘rov #{req.pk}',
        )
    req.status = ApprovalStatus.APPROVED
    req.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
    log_action(reviewer, 'tasdiq', 'stock_change_request', req.pk, req.note)
    return req


@transaction.atomic
def receive_purchase_order(*, order, user=None):
    order = PurchaseOrder.objects.select_for_update().prefetch_related('lines__product').get(pk=order.pk)
    if order.status == PurchaseOrder.Status.RECEIVED:
        raise StockError('Buyurtma allaqachon qabul qilingan.')
    if order.status == PurchaseOrder.Status.CANCELLED:
        raise StockError('Bekor qilingan buyurtmani qabul qilib bo‘lmaydi.')
    if not order.lines.exists():
        raise StockError('Buyurtmada qator yo‘q.')

    for line in order.lines.all():
        receive_stock(
            product=line.product,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            user=user,
            supplier=order.supplier,
            expiry_date=line.expiry_date,
            note=f'PO-{order.pk}',
        )
    order.status = PurchaseOrder.Status.RECEIVED
    order.save(update_fields=['status'])
    log_action(user, 'po_qabul', 'purchase_order', order.pk, str(order.supplier))
    return order
