from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from kitchen.models import (
    MovementType,
    Product,
    StockLot,
    StockLotAllocation,
    StockMovement,
)
from kitchen.services.audit import log_action
from kitchen.services.notifications import bump_notification_cache
from kitchen.services.precision import as_decimal, money, qty, weighted_avg


class StockError(Exception):
    pass


def _after_stock_change():
    bump_notification_cache()


def _sync_product_expiry(product):
    earliest = (
        StockLot.objects.filter(product=product, quantity__gt=0, expiry_date__isnull=False)
        .order_by('expiry_date')
        .values_list('expiry_date', flat=True)
        .first()
    )
    if product.expiry_date != earliest:
        product.expiry_date = earliest
        product.save(update_fields=['expiry_date'])


def _fefo_lots_qs(product, location=None):
    qs = StockLot.objects.select_for_update().filter(product=product, quantity__gt=0)
    if location is not None:
        qs = qs.filter(location=location)
    return qs.order_by(F('expiry_date').asc(nulls_last=True), 'received_at', 'id')


def _lot_cover_qty(product, location=None):
    qs = StockLot.objects.filter(product=product, quantity__gt=0)
    if location is not None:
        qs = qs.filter(location=location)
    return qty(qs.aggregate(s=Sum('quantity'))['s'] or 0)


def _assert_lot_cover(product, need, location=None):
    """Partiya yig‘indisi yetmasa — sun’iy lot yaratmaydi, audit uchun xato beradi."""
    lot_sum = _lot_cover_qty(product, location=location)
    gap = qty(need - lot_sum)
    if gap > 0:
        loc_name = getattr(location, 'name', None)
        raise StockError(
            f'{product.name}: partiyalar qoldiq bilan mos emas'
            f"{f' ({loc_name})' if loc_name else ''} "
            f'(partiyada {lot_sum} {product.unit}, kerak {need}). '
            f'Avval partiyalarni sinxronlang.'
        )


def _sync_product_avg_from_lots(product):
    """Qolgan partiyalar tannarxidan Product.avg_cost ni qayta hisoblaydi."""
    lots = StockLot.objects.filter(product=product, quantity__gt=0).only('quantity', 'unit_cost')
    total_qty = qty(0)
    total_val = Decimal('0')
    for lot in lots:
        lot_qty = qty(lot.quantity)
        total_qty = qty(total_qty + lot_qty)
        total_val += lot_qty * as_decimal(lot.unit_cost)
    if total_qty <= 0:
        return
    new_avg = money(total_val / total_qty)
    if product.avg_cost != new_avg:
        product.avg_cost = new_avg
        product.save(update_fields=['avg_cost'])


def preview_fefo_allocation(product, quantity, location=None):
    """Rasxod qilmasdan FEFO bo‘yicha qaysi partiyadan qancha ketishini ko‘rsatadi."""
    quantity = qty(quantity)
    if quantity <= 0:
        return {
            'quantity': quantity,
            'lines': [],
            'total_cost': money(0),
            'avg_unit_cost': money(0),
            'covered': qty(0),
            'missing': qty(0),
        }

    loc = location if location is not None else product.default_location
    remaining = quantity
    lines = []
    qs = StockLot.objects.filter(product=product, quantity__gt=0)
    if loc is not None:
        qs = qs.filter(location=loc)
    qs = qs.order_by(F('expiry_date').asc(nulls_last=True), 'received_at', 'id')

    for lot in qs:
        if remaining <= 0:
            break
        take = qty(min(lot.quantity, remaining))
        if take <= 0:
            continue
        lines.append(
            {
                'lot': lot,
                'lot_id': lot.pk,
                'quantity': take,
                'unit_cost': money(lot.unit_cost),
                'line_cost': money(take * lot.unit_cost),
                'expiry_date': lot.expiry_date,
                'synthetic': False,
            }
        )
        remaining = qty(remaining - take)

    covered = qty(quantity - remaining)
    total = sum((row['line_cost'] for row in lines), money(0))
    denom = covered if covered > 0 else quantity
    return {
        'quantity': quantity,
        'lines': lines,
        'total_cost': money(total),
        'avg_unit_cost': money(total / denom) if denom else money(0),
        'covered': covered,
        'missing': remaining,
        'mixed': len([row for row in lines if row['quantity'] > 0]) > 1,
    }


def allocation_rows_from_movement(movement):
    rows = []
    for alloc in movement.lot_allocations.select_related('lot').all():
        rows.append(
            {
                'lot': alloc.lot,
                'lot_id': alloc.lot_id,
                'quantity': qty(alloc.quantity),
                'unit_cost': money(alloc.unit_cost),
                'line_cost': money(alloc.quantity * alloc.unit_cost),
                'expiry_date': alloc.lot.expiry_date if alloc.lot else None,
                'synthetic': False,
            }
        )
    return rows


def _allocate_from_lots(*, product, quantity, movement, location=None):
    remaining = qty(quantity)
    allocations = []
    for lot in _fefo_lots_qs(product, location=location):
        if remaining <= 0:
            break
        take = qty(min(lot.quantity, remaining))
        if take <= 0:
            continue
        lot.quantity = qty(lot.quantity - take)
        lot.save(update_fields=['quantity'])
        allocations.append(
            StockLotAllocation(
                lot=lot,
                movement=movement,
                quantity=take,
                unit_cost=money(lot.unit_cost),
            )
        )
        remaining = qty(remaining - take)
    if remaining > 0:
        loc_name = getattr(location, 'name', None)
        raise StockError(
            f'{product.name}: partiyalarda yetarli emas'
            f"{f' ({loc_name})' if loc_name else ''} "
            f'(yetishmaydi {remaining} {product.unit}).'
        )
    StockLotAllocation.objects.bulk_create(allocations)
    return allocations


@transaction.atomic
def receive_stock(
    *,
    product,
    quantity,
    unit_cost,
    user=None,
    supplier=None,
    expiry_date=None,
    note='',
    created_at=None,
    location=None,
    movement_type=MovementType.IN,
):
    quantity = qty(quantity)
    unit_cost = money(unit_cost)
    if quantity <= 0:
        raise StockError('Miqdor 0 dan katta bo‘lishi kerak.')
    if unit_cost < 0:
        raise StockError('Narx manfiy bo‘lmasligi kerak.')

    product = Product.objects.select_for_update().get(pk=product.pk)
    loc = location or product.default_location
    new_avg, new_qty = weighted_avg(product.quantity, product.avg_cost, quantity, unit_cost)
    product.avg_cost = new_avg
    product.quantity = new_qty
    product.save(update_fields=['quantity', 'avg_cost'])

    movement = StockMovement.objects.create(
        movement_type=movement_type,
        product=product,
        quantity=quantity,
        unit_cost=unit_cost,
        total_cost=money(quantity * unit_cost),
        supplier=supplier,
        expiry_date=expiry_date,
        note=note,
        created_by=user,
        created_at=created_at or timezone.now(),
        location=loc,
    )
    StockLot.objects.create(
        product=product,
        location=loc,
        quantity=quantity,
        unit_cost=unit_cost,
        expiry_date=expiry_date,
        supplier=supplier,
        received_at=created_at or timezone.now(),
        source_movement=movement,
        note=note,
    )
    _sync_product_expiry(product)
    _after_stock_change()
    if movement_type == MovementType.IN:
        log_action(user, 'prixod', 'product', product.pk, f'+{quantity} {product.unit}')
    return movement


@transaction.atomic
def consume_stock(
    *,
    product,
    quantity,
    user=None,
    note='',
    cook_batch=None,
    movement_type=MovementType.OUT,
    allow_negative=False,
    locked_product=None,
    location=None,
):
    quantity = qty(quantity)
    if quantity <= 0:
        raise StockError('Miqdor 0 dan katta bo‘lishi kerak.')

    product = locked_product or Product.objects.select_for_update().get(pk=product.pk)
    have = qty(product.quantity)
    if not allow_negative and have < quantity:
        raise StockError(
            f'{product.name}: yetarli emas (qoldiq {have} {product.unit}, kerak {quantity}).'
        )

    movement_location = location or product.default_location
    _assert_lot_cover(product, quantity, location=movement_location)

    movement = StockMovement.objects.create(
        movement_type=movement_type,
        product=product,
        quantity=quantity,
        unit_cost=money(product.avg_cost),
        total_cost=money(quantity * product.avg_cost),
        note=note,
        cook_batch=cook_batch,
        created_by=user,
        location=movement_location,
    )
    try:
        allocations = _allocate_from_lots(
            product=product,
            quantity=quantity,
            movement=movement,
            location=movement_location,
        )
    except StockError:
        if not allow_negative:
            raise
        allocations = []

    if allocations:
        weighted = sum((a.quantity * a.unit_cost for a in allocations), money(0))
        movement.unit_cost = money(weighted / quantity) if quantity else money(0)
        movement.total_cost = money(weighted)
        movement.save(update_fields=['unit_cost', 'total_cost'])

    product.quantity = qty(max(have - quantity, 0))
    product.save(update_fields=['quantity'])
    _sync_product_avg_from_lots(product)
    _sync_product_expiry(product)
    _after_stock_change()
    return movement


@transaction.atomic
def adjust_stock(*, product, new_quantity, user=None, note='', location=None):
    new_quantity = qty(new_quantity)
    if new_quantity < 0:
        raise StockError('Qoldiq manfiy bo‘lmasligi kerak.')

    product = Product.objects.select_for_update().get(pk=product.pk)
    delta = qty(new_quantity - product.quantity)
    if delta == 0:
        return None

    if delta > 0:
        movement = receive_stock(
            product=product,
            quantity=delta,
            unit_cost=product.avg_cost,
            user=user,
            note=note or f'Tuzatish: {delta:+}',
            location=location,
            movement_type=MovementType.ADJUST,
        )
        log_action(user, 'tuzatish', 'product', product.pk, note or str(delta))
        return movement

    movement = consume_stock(
        product=product,
        quantity=abs(delta),
        user=user,
        note=note or f'Tuzatish: {delta:+}',
        movement_type=MovementType.ADJUST,
        locked_product=product,
        location=location,
    )
    log_action(user, 'tuzatish', 'product', product.pk, note or str(delta))
    return movement


@transaction.atomic
def restore_stock(*, product, quantity, unit_cost, user=None, note='', cook_batch=None, location=None):
    quantity = qty(quantity)
    unit_cost = money(unit_cost)
    if quantity <= 0:
        raise StockError('Miqdor 0 dan katta bo‘lishi kerak.')

    product = Product.objects.select_for_update().get(pk=product.pk)
    loc = location or product.default_location
    new_avg, new_qty = weighted_avg(product.quantity, product.avg_cost, quantity, unit_cost)
    product.avg_cost = new_avg
    product.quantity = new_qty
    product.save(update_fields=['quantity', 'avg_cost'])

    movement = StockMovement.objects.create(
        movement_type=MovementType.ADJUST,
        product=product,
        quantity=quantity,
        unit_cost=unit_cost,
        total_cost=money(quantity * unit_cost),
        note=note,
        cook_batch=cook_batch,
        created_by=user,
        location=loc,
    )

    restored_qty = qty(0)
    if cook_batch is not None:
        out_moves = StockMovement.objects.filter(
            cook_batch=cook_batch,
            product=product,
            movement_type=MovementType.OUT,
        ).prefetch_related('lot_allocations__lot')
        for out in out_moves:
            for alloc in out.lot_allocations.select_related('lot'):
                lot = StockLot.objects.select_for_update().get(pk=alloc.lot_id)
                lot.quantity = qty(lot.quantity + alloc.quantity)
                lot.save(update_fields=['quantity'])
                restored_qty = qty(restored_qty + alloc.quantity)

    if restored_qty < quantity:
        StockLot.objects.create(
            product=product,
            location=loc,
            quantity=qty(quantity - restored_qty),
            unit_cost=unit_cost,
            expiry_date=product.expiry_date,
            received_at=timezone.now(),
            source_movement=movement,
            note=note or 'Qaytarish',
        )
    _sync_product_avg_from_lots(product)
    _sync_product_expiry(product)
    _after_stock_change()
    return movement


@transaction.atomic
def record_waste(*, product, quantity, user=None, note='', location=None):
    movement = consume_stock(
        product=product,
        quantity=quantity,
        user=user,
        note=note,
        movement_type=MovementType.WASTE,
        location=location,
    )
    log_action(user, 'chiqindi', 'product', product.pk, note)
    return movement


@transaction.atomic
def ensure_lots_for_product(product):
    product = Product.objects.select_for_update().get(pk=product.pk)
    lot_sum = qty(
        StockLot.objects.filter(product=product).aggregate(s=Sum('quantity'))['s'] or 0
    )
    gap = qty(product.quantity - lot_sum)
    if gap > 0:
        StockLot.objects.create(
            product=product,
            location=product.default_location,
            quantity=gap,
            unit_cost=money(product.avg_cost),
            expiry_date=product.expiry_date,
            note='Migratsiya partiyasi',
        )
    return gap
