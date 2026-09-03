from django.db import transaction

from kitchen.models import CookBatch, CookBatchItem, MovementType, Product
from kitchen.services.audit import log_action
from kitchen.services.precision import money
from kitchen.services.recipe_cost import recipe_nutrition
from kitchen.services.stock import StockError, consume_stock, restore_stock


@transaction.atomic
def cook_recipe(*, recipe, portions, user=None, note=''):
    try:
        portions = int(portions)
    except (TypeError, ValueError):
        raise StockError('Porsiya soni butun son bo‘lishi kerak.')
    if portions < 1:
        raise StockError('Porsiya soni kamida 1 bo‘lishi kerak.')

    recipe = type(recipe).objects.prefetch_related('items__product').get(pk=recipe.pk)
    product_ids = list(
        recipe.items.order_by('product_id').values_list('product_id', flat=True)
    )
    if not product_ids:
        raise StockError('Retseptda ingredient yo‘q.')

    locked = {
        p.pk: p
        for p in Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk')
    }
    preview = recipe_nutrition(recipe, portions, products=locked)
    if not preview['items']:
        raise StockError('Retseptda ingredient yo‘q.')
    if not preview['can_cook']:
        names = ', '.join(s['product'].name for s in preview['shortages'])
        raise StockError(f'Yetarli mahsulot yo‘q: {names}')

    batch = CookBatch.objects.create(
        recipe=recipe,
        portions=portions,
        total_cost=preview['total_cost'],
        cost_per_portion=preview['cost_per_portion'],
        total_kcal=preview['total_kcal'],
        kcal_per_portion=preview['kcal_per_portion'],
        protein_per_portion=preview['protein_per_portion'],
        fat_per_portion=preview['fat_per_portion'],
        carbs_per_portion=preview['carbs_per_portion'],
        note=note,
        created_by=user,
    )

    actual_total = money(0)
    for row in preview['items']:
        product = locked[row['product'].pk]
        movement = consume_stock(
            product=product,
            quantity=row['need'],
            user=user,
            note=f'Pishirish: {recipe.name} × {portions}',
            cook_batch=batch,
            movement_type=MovementType.OUT,
            locked_product=product,
        )
        CookBatchItem.objects.create(
            batch=batch,
            product=product,
            quantity=row['need'],
            unit_cost=movement.unit_cost,
            line_cost=movement.total_cost,
        )
        actual_total = money(actual_total + movement.total_cost)

    if actual_total != batch.total_cost:
        batch.total_cost = actual_total
        batch.cost_per_portion = money(actual_total / portions) if portions else money(0)
        batch.save(update_fields=['total_cost', 'cost_per_portion'])

    log_action(user, 'pishirish', 'cook_batch', batch.pk, f'{recipe.name} × {portions}')
    return batch


@transaction.atomic
def cancel_cook_batch(*, batch, user=None):
    batch = CookBatch.objects.select_for_update().get(pk=batch.pk)
    if batch.status == CookBatch.Status.CANCELLED:
        raise StockError('Allaqachon bekor qilingan.')

    for item in batch.items.select_related('product').order_by('product_id'):
        restore_stock(
            product=item.product,
            quantity=item.quantity,
            unit_cost=item.unit_cost,
            user=user,
            note=f'Bekor: {batch.recipe.name} #{batch.pk}',
            cook_batch=batch,
        )

    batch.status = CookBatch.Status.CANCELLED
    batch.save(update_fields=['status'])
    batch.menu_items.update(is_cooked=False, cook_batch=None)
    log_action(user, 'bekor', 'cook_batch', batch.pk, batch.recipe.name)
    return batch
