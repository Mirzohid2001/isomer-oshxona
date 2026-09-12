from decimal import Decimal

from django.db.models import Sum
from django.db.transaction import atomic
from django.utils import timezone

from kitchen.models import MovementType, Supplier, SupplierPayment, StockMovement
from kitchen.services.audit import log_action
from kitchen.services.precision import money


def credit_receipts_qs(supplier=None):
    qs = StockMovement.objects.filter(
        movement_type=MovementType.IN,
        is_credit=True,
        cook_batch__isnull=True,
        supplier__isnull=False,
    ).select_related('supplier', 'product')
    if supplier is not None:
        qs = qs.filter(supplier=supplier)
    return qs


def payments_qs(supplier=None):
    qs = SupplierPayment.objects.select_related('supplier', 'created_by')
    if supplier is not None:
        qs = qs.filter(supplier=supplier)
    return qs


def supplier_debt_rows():
    """Yetkazuvchi bo‘yicha: qarz prixod, to‘lov, qoldiq."""
    credit_map = {
        row['supplier_id']: money(row['total'] or 0)
        for row in credit_receipts_qs()
        .values('supplier_id')
        .annotate(total=Sum('total_cost'))
    }
    paid_map = {
        row['supplier_id']: money(row['total'] or 0)
        for row in payments_qs().values('supplier_id').annotate(total=Sum('amount'))
    }
    supplier_ids = set(credit_map) | set(paid_map)
    suppliers = {
        s.pk: s
        for s in Supplier.objects.filter(pk__in=supplier_ids).order_by('name')
    }
    rows = []
    for sid in supplier_ids:
        supplier = suppliers.get(sid)
        if not supplier:
            continue
        credit_total = credit_map.get(sid, money(0))
        paid_total = paid_map.get(sid, money(0))
        remaining = money(credit_total - paid_total)
        rows.append(
            {
                'supplier': supplier,
                'credit_total': credit_total,
                'paid_total': paid_total,
                'remaining': remaining,
            }
        )
    rows.sort(key=lambda r: (-r['remaining'], r['supplier'].name))
    return rows


def debt_summary():
    rows = supplier_debt_rows()
    credit_total = money(sum((r['credit_total'] for r in rows), Decimal('0')))
    paid_total = money(sum((r['paid_total'] for r in rows), Decimal('0')))
    remaining = money(credit_total - paid_total)
    return {
        'rows': rows,
        'credit_total': credit_total,
        'paid_total': paid_total,
        'remaining': remaining,
        'supplier_count': len(rows),
        'open_count': sum(1 for r in rows if r['remaining'] > 0),
    }


def supplier_debt_detail(supplier, *, limit=500):
    credits_qs = credit_receipts_qs(supplier)
    payments_all = payments_qs(supplier)
    credit_total = money(credits_qs.aggregate(t=Sum('total_cost'))['t'] or 0)
    paid_total = money(payments_all.aggregate(t=Sum('amount'))['t'] or 0)
    credits_count = credits_qs.count()
    payments_count = payments_all.count()
    return {
        'supplier': supplier,
        'credits': list(credits_qs.order_by('-created_at')[:limit]),
        'payments': list(payments_all.order_by('-paid_on', '-id')[:limit]),
        'credits_count': credits_count,
        'payments_count': payments_count,
        'credits_truncated': credits_count > limit,
        'payments_truncated': payments_count > limit,
        'credit_total': credit_total,
        'paid_total': paid_total,
        'remaining': money(credit_total - paid_total),
    }


@atomic
def record_supplier_payment(*, supplier, amount, paid_on=None, note='', user=None):
    amount = money(amount)
    if amount <= 0:
        raise ValueError('To‘lov summasi 0 dan katta bo‘lishi kerak.')
    payment = SupplierPayment.objects.create(
        supplier=supplier,
        amount=amount,
        paid_on=paid_on or timezone.localdate(),
        note=note or '',
        created_by=user,
    )
    log_action(
        user,
        'qarz_tolov',
        'supplier_payment',
        payment.pk,
        f'{supplier.name}: {amount}',
    )
    return payment


@atomic
def delete_supplier_payment(*, payment, user=None):
    supplier = payment.supplier
    label = f'{supplier.name}: {payment.amount}'
    pk = payment.pk
    payment.delete()
    log_action(user, 'qarz_tolov_ochirish', 'supplier_payment', pk, label)
    return supplier
