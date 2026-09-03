from django.db import transaction
from django.utils import timezone

from kitchen.models import MovementType, Product, StockMovement
from kitchen.services.audit import log_action
from kitchen.services.precision import money, qty, weighted_avg


class StockError(Exception):
    pass


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
):
    quantity = qty(quantity)
    unit_cost = money(unit_cost)
    if quantity <= 0:
        raise StockError('Miqdor 0 dan katta bo‘lishi kerak.')
    if unit_cost < 0:
        raise StockError('Narx manfiy bo‘lmasligi kerak.')

    product = Product.objects.select_for_update().get(pk=product.pk)
    new_avg, new_qty = weighted_avg(product.quantity, product.avg_cost, quantity, unit_cost)
    product.avg_cost = new_avg
    product.quantity = new_qty
    if expiry_date:
        if not product.expiry_date or expiry_date < product.expiry_date:
            product.expiry_date = expiry_date
    product.save(update_fields=['quantity', 'avg_cost', 'expiry_date'])

    movement = StockMovement.objects.create(
        movement_type=MovementType.IN,
        product=product,
        quantity=quantity,
        unit_cost=unit_cost,
        total_cost=money(quantity * unit_cost),
        supplier=supplier,
        expiry_date=expiry_date,
        note=note,
        created_by=user,
        created_at=created_at or timezone.now(),
    )
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

    unit_cost = money(product.avg_cost)
    product.quantity = qty(have - quantity)
    if product.quantity < 0:
        product.quantity = qty(0)
    product.save(update_fields=['quantity'])

    movement = StockMovement.objects.create(
        movement_type=movement_type,
        product=product,
        quantity=quantity,
        unit_cost=unit_cost,
        total_cost=money(quantity * unit_cost),
        note=note,
        cook_batch=cook_batch,
        created_by=user,
    )
    return movement


@transaction.atomic
def adjust_stock(*, product, new_quantity, user=None, note=''):
    new_quantity = qty(new_quantity)
    if new_quantity < 0:
        raise StockError('Qoldiq manfiy bo‘lmasligi kerak.')

    product = Product.objects.select_for_update().get(pk=product.pk)
    delta = qty(new_quantity - product.quantity)
    product.quantity = new_quantity
    product.save(update_fields=['quantity'])

    movement = StockMovement.objects.create(
        movement_type=MovementType.ADJUST,
        product=product,
        quantity=abs(delta),
        unit_cost=money(product.avg_cost),
        total_cost=money(abs(delta) * product.avg_cost),
        note=note or f'Tuzatish: {delta:+}',
        created_by=user,
    )
    log_action(user, 'tuzatish', 'product', product.pk, note or str(delta))
    return movement


@transaction.atomic
def restore_stock(*, product, quantity, unit_cost, user=None, note='', cook_batch=None):
    quantity = qty(quantity)
    unit_cost = money(unit_cost)
    if quantity <= 0:
        raise StockError('Miqdor 0 dan katta bo‘lishi kerak.')

    product = Product.objects.select_for_update().get(pk=product.pk)
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
    )
    return movement


@transaction.atomic
def record_waste(*, product, quantity, user=None, note=''):
    movement = consume_stock(
        product=product,
        quantity=quantity,
        user=user,
        note=note,
        movement_type=MovementType.WASTE,
    )
    log_action(user, 'chiqindi', 'product', product.pk, note)
    return movement
