from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from kitchen.models import DailyMenu, Product
from kitchen.services.precision import money, qty
from kitchen.services.recipe_cost import recipe_nutrition
from kitchen.utils import parse_date


def _build_rows(needs):
    rows = []
    total = money(0)
    if not needs:
        return {'rows': rows, 'total_est': total}
    products = {p.pk: p for p in Product.objects.filter(pk__in=needs.keys())}
    for product_id, need in needs.items():
        product = products[product_id]
        need = qty(need)
        have = qty(product.quantity)
        missing = qty(need - have)
        if missing <= 0:
            continue
        est = money(missing * product.avg_cost)
        total = money(total + est)
        rows.append(
            {
                'product': product,
                'need': need,
                'have': have,
                'buy': missing,
                'est_cost': est,
            }
        )
    rows.sort(key=lambda r: r['product'].name)
    return {'rows': rows, 'total_est': total}


def shopping_list_for_menu(menu):
    needs = defaultdict(lambda: Decimal('0'))
    items = menu.items.filter(is_cooked=False).select_related('recipe').prefetch_related(
        'recipe__items__product'
    )
    for item in items:
        preview = recipe_nutrition(item.recipe, item.portions)
        for row in preview['items']:
            needs[row['product'].pk] += row['need']
    return _build_rows(needs)


def shopping_list_for_date(date):
    date = parse_date(date)
    if not date:
        return {'rows': [], 'total_est': money(0)}
    try:
        menu = DailyMenu.objects.prefetch_related('items__recipe__items__product').get(date=date)
    except DailyMenu.DoesNotExist:
        return {'rows': [], 'total_est': money(0)}
    return shopping_list_for_menu(menu)


def shopping_list_for_range(start_date, days=7):
    start_date = parse_date(start_date)
    if not start_date:
        return {'rows': [], 'total_est': money(0), 'start': None, 'end': None}
    needs = defaultdict(lambda: Decimal('0'))
    end = start_date + timedelta(days=days - 1)
    menus = DailyMenu.objects.filter(date__gte=start_date, date__lte=end).prefetch_related(
        'items__recipe__items__product'
    )
    for menu in menus:
        for item in menu.items.filter(is_cooked=False):
            preview = recipe_nutrition(item.recipe, item.portions)
            for row in preview['items']:
                needs[row['product'].pk] += row['need']
    data = _build_rows(needs)
    data['start'] = start_date
    data['end'] = end
    return data
