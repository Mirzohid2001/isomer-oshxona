from decimal import Decimal, InvalidOperation

from kitchen.models import Recipe
from kitchen.services.precision import as_decimal, money, money_div, nutri, nutri_div, qty
from kitchen.services.stock import preview_fefo_allocation


def _empty(recipe):
    return {
        'recipe': recipe,
        'portions': 0,
        'items': [],
        'shortages': [],
        'can_cook': False,
        'total_cost': money(0),
        'cost_per_portion': money(0),
        'total_kcal': nutri(0),
        'kcal_per_portion': nutri(0),
        'protein_per_portion': nutri(0),
        'fat_per_portion': nutri(0),
        'carbs_per_portion': nutri(0),
        'allergens': [],
    }


def recipe_nutrition(recipe, portions=1, *, products=None):
    try:
        portions_dec = as_decimal(portions)
    except (InvalidOperation, TypeError, ValueError):
        return _empty(recipe)

    if portions_dec < 1 or portions_dec != portions_dec.to_integral_value():
        return _empty(recipe)

    portions_int = int(portions_dec)
    portions_dec = Decimal(portions_int)
    base_portions = Decimal(recipe.base_portions or 1)
    if base_portions <= 0:
        base_portions = Decimal('1')
    scale = portions_dec / base_portions

    items = []
    total_cost_exact = Decimal('0')
    total_kcal_exact = Decimal('0')
    total_protein_exact = Decimal('0')
    total_fat_exact = Decimal('0')
    total_carbs_exact = Decimal('0')
    shortages = []
    allergen_set = set()

    if recipe.allergens:
        allergen_set.update(a.strip() for a in recipe.allergens.split(',') if a.strip())

    for item in recipe.items.select_related('product').order_by('product_id'):
        product = products[item.product_id] if products else item.product
        if product.allergens:
            allergen_set.update(a.strip() for a in product.allergens.split(',') if a.strip())

        need = qty(as_decimal(item.quantity_per_portion) * scale)
        allocation = preview_fefo_allocation(product, need)
        line_cost_exact = as_decimal(allocation['total_cost'])
        line_cost = money(line_cost_exact)
        unit_cost = allocation['avg_unit_cost']

        line_kcal_exact = need * as_decimal(product.kcal_per_unit)
        line_protein_exact = need * as_decimal(product.protein)
        line_fat_exact = need * as_decimal(product.fat)
        line_carbs_exact = need * as_decimal(product.carbs)

        have = qty(product.quantity)
        enough = have >= need
        if not enough:
            shortages.append(
                {
                    'product': product,
                    'need': need,
                    'have': have,
                    'missing': qty(need - have),
                }
            )

        items.append(
            {
                'product': product,
                'need': need,
                'have': have,
                'unit_cost': money(unit_cost),
                'line_cost': line_cost,
                'line_kcal': nutri(line_kcal_exact),
                'enough': enough,
                'allocations': allocation['lines'],
                'mixed_cost': allocation['mixed'],
            }
        )
        total_cost_exact += line_cost_exact
        total_kcal_exact += line_kcal_exact
        total_protein_exact += line_protein_exact
        total_fat_exact += line_fat_exact
        total_carbs_exact += line_carbs_exact

    if items:
        rounded_sum = sum((row['line_cost'] for row in items), money(0))
        exact_total = money(total_cost_exact)
        drift = exact_total - rounded_sum
        if drift != 0:
            items[-1]['line_cost'] = money(items[-1]['line_cost'] + drift)
        total_cost = exact_total
    else:
        total_cost = money(0)

    return {
        'recipe': recipe,
        'portions': portions_int,
        'items': items,
        'shortages': shortages,
        'can_cook': len(shortages) == 0 and len(items) > 0,
        'total_cost': total_cost,
        'cost_per_portion': money_div(total_cost, portions_dec),
        'total_kcal': nutri(total_kcal_exact),
        'kcal_per_portion': nutri_div(total_kcal_exact, portions_dec),
        'protein_per_portion': nutri_div(total_protein_exact, portions_dec),
        'fat_per_portion': nutri_div(total_fat_exact, portions_dec),
        'carbs_per_portion': nutri_div(total_carbs_exact, portions_dec),
        'allergens': sorted(allergen_set),
    }


def recipe_cost_snapshot(recipe_id, portions=1):
    recipe = Recipe.objects.prefetch_related('items__product').get(pk=recipe_id)
    return recipe_nutrition(recipe, portions)
