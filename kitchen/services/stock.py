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
from kitchen.services.precision import money, qty, weighted_avg


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


def _fefo_lots_qs(product):
    return (
        StockLot.objects.select_for_update()
        .filter(product=product, quantity__gt=0)
        .order_by(F('expiry_date').asc(nulls_last=True), 'received_at', 'id')
    )


def _ensure_lot_cover(product, need):
    """Agar product.quantity > lot sum — yetishmaganini FEFO oxiriga qo‘shadi."""
    lot_sum = qty(
        StockLot.objects.filter(product=product, quantity__gt=0).aggregate(s=Sum('quantity'))['s']
        or 0
    )
    gap = qty(need - lot_sum)
    if gap > 0:
        StockLot.objects.create(
            product=product,
            location=product.default_location,
            quantity=gap,
            unit_cost=money(product.avg_cost),
            expiry_date=product.expiry_date,
            note='Avto-partiya (sync)',
        )


def _allocate_from_lots(*, product, quantity, movement):
    remaining = qty(quantity)
    allocations = []
    for lot in _fefo_lots_qs(product):
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
        raise StockError(
            f'{product.name}: partiyalarda yetarli emas (yetishmaydi {remaining} {product.unit}).'
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

    _ensure_lot_cover(product, quantity)

    movement = StockMovement.objects.create(
        movement_type=movement_type,
        product=product,
        quantity=quantity,
        unit_cost=money(product.avg_cost),
        total_cost=money(quantity * product.avg_cost),
        note=note,
        cook_batch=cook_batch,
        created_by=user,
        location=location or product.default_location,
    )
    try:
        allocations = _allocate_from_lots(product=product, quantity=quantity, movement=movement)
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
