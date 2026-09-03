from calendar import monthrange
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from kitchen.models import CookBatch, MovementType, Product, Recipe, StockLot, StockMovement
from kitchen.services.budget import budget_status
from kitchen.services.precision import money, qty
from kitchen.services.recipe_cost import recipe_nutrition
from kitchen.utils import local_date_span_bounds, parse_date


MONTH_NAMES = {
    1: 'Yanvar',
    2: 'Fevral',
    3: 'Mart',
    4: 'Aprel',
    5: 'May',
    6: 'Iyun',
    7: 'Iyul',
    8: 'Avgust',
    9: 'Sentabr',
    10: 'Oktabr',
    11: 'Noyabr',
    12: 'Dekabr',
}


def _period_bounds(mode, year, month, day=None):
    today = timezone.localdate()
    day = day or today
    if mode == 'day':
        start = day
        end = day
        label = day.strftime('%d.%m.%Y')
    elif mode == 'week':
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=6)
        label = f"{start.strftime('%d.%m')} — {end.strftime('%d.%m.%Y')}"
    else:
        start = day.replace(year=year, month=month, day=1)
        end = start.replace(day=monthrange(year, month)[1])
        label = f"{MONTH_NAMES.get(month, month)} {year}"
    return start, end, label


def _prev_period(mode, start, end):
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    return prev_start, prev_end


def _cook_qs(start, end):
    start_dt, end_dt = local_date_span_bounds(start, end)
    return CookBatch.objects.filter(
        cooked_at__gte=start_dt,
        cooked_at__lt=end_dt,
        status=CookBatch.Status.DONE,
    )


def _out_qs(start, end):
    start_dt, end_dt = local_date_span_bounds(start, end)
    return (
        StockMovement.objects.filter(
            movement_type=MovementType.OUT,
            created_at__gte=start_dt,
            created_at__lt=end_dt,
        ).exclude(cook_batch__status=CookBatch.Status.CANCELLED)
    )


def _in_qs(start, end):
    start_dt, end_dt = local_date_span_bounds(start, end)
    return StockMovement.objects.filter(
        movement_type=MovementType.IN,
        created_at__gte=start_dt,
        created_at__lt=end_dt,
        cook_batch__isnull=True,
    )


def _waste_qs(start, end):
    start_dt, end_dt = local_date_span_bounds(start, end)
    return StockMovement.objects.filter(
        movement_type=MovementType.WASTE,
        created_at__gte=start_dt,
        created_at__lt=end_dt,
    )


def _period_kpis(start, end):
    cooks = _cook_qs(start, end)
    cook_cost = money(cooks.aggregate(t=Sum('total_cost'))['t'] or 0)
    portions = cooks.aggregate(t=Sum('portions'))['t'] or 0
    batches = cooks.count()
    waste_cost = money(_waste_qs(start, end).aggregate(t=Sum('total_cost'))['t'] or 0)
    receipt_cost = money(_in_qs(start, end).aggregate(t=Sum('total_cost'))['t'] or 0)
    avg_portion = money(cook_cost / portions) if portions else money(0)
    return {
        'cook_cost': cook_cost,
        'waste_cost': waste_cost,
        'receipt_cost': receipt_cost,
        'portions': portions,
        'batches': batches,
        'avg_portion_cost': avg_portion,
        'total_spend': money(cook_cost + waste_cost),
    }


def _delta(current, previous):
    current = Decimal(str(current or 0))
    previous = Decimal(str(previous or 0))
    diff = current - previous
    if previous == 0:
        pct = Decimal('100') if current > 0 else Decimal('0')
    else:
        pct = (diff / previous * Decimal('100')).quantize(Decimal('0.1'))
    return {
        'diff': diff,
        'pct': float(pct),
        'up': diff > 0,
        'down': diff < 0,
        'flat': diff == 0,
    }


def daily_trend(start, end):
    rows = (
        _cook_qs(start, end)
        .annotate(day=TruncDate('cooked_at'))
        .values('day')
        .annotate(cost=Sum('total_cost'), portions=Sum('portions'), batches=Count('id'))
        .order_by('day')
    )
    by_day = {r['day']: r for r in rows}
    points = []
    max_cost = money(0)
    cursor = start
    while cursor <= end:
        row = by_day.get(cursor)
        cost = money(row['cost'] if row else 0)
        portions = row['portions'] if row else 0
        batches = row['batches'] if row else 0
        if cost > max_cost:
            max_cost = cost
        points.append(
            {
                'date': cursor,
                'label': cursor.strftime('%d.%m'),
                'cost': cost,
                'portions': portions or 0,
                'batches': batches or 0,
            }
        )
        cursor += timedelta(days=1)
    for p in points:
        p['bar'] = float((p['cost'] / max_cost * 100).quantize(Decimal('0.1'))) if max_cost else 0
    return points


def top_dishes(start, end, limit=10):
    rows = (
        _cook_qs(start, end)
        .values('recipe_id', 'recipe__name')
        .annotate(
            portions=Sum('portions'),
            cost=Sum('total_cost'),
            times=Count('id'),
        )
        .order_by('-cost')[:limit]
    )
    result = []
    for r in rows:
        portions = r['portions'] or 0
        cost = money(r['cost'] or 0)
        result.append(
            {
                'recipe_id': r['recipe_id'],
                'name': r['recipe__name'],
                'portions': portions,
                'times': r['times'],
                'cost': cost,
                'avg_portion': money(cost / portions) if portions else money(0),
            }
        )
    return result


def product_consumption(start, end, limit=15):
    rows = (
        _out_qs(start, end)
        .values('product__name', 'product__unit')
        .annotate(qty=Sum('quantity'), cost=Sum('total_cost'))
        .order_by('-cost')[:limit]
    )
    return [
        {
            'name': r['product__name'],
            'unit': r['product__unit'],
            'qty': qty(r['qty'] or 0),
            'cost': money(r['cost'] or 0),
        }
        for r in rows
    ]


def supplier_stats(start, end, limit=10):
    rows = (
        _in_qs(start, end)
        .filter(supplier__isnull=False)
        .values('supplier__name')
        .annotate(total=Sum('total_cost'), qty=Sum('quantity'), times=Count('id'))
        .order_by('-total')[:limit]
    )
    return [
        {
            'name': r['supplier__name'],
            'total': money(r['total'] or 0),
            'qty': qty(r['qty'] or 0),
            'times': r['times'],
        }
        for r in rows
    ]


def waste_stats(start, end, limit=10):
    rows = (
        _waste_qs(start, end)
        .values('product__name', 'product__unit', 'note')
        .annotate(qty=Sum('quantity'), cost=Sum('total_cost'))
        .order_by('-cost')[:limit]
    )
    return [
        {
            'name': r['product__name'],
            'unit': r['product__unit'],
            'note': r['note'] or '—',
            'qty': qty(r['qty'] or 0),
            'cost': money(r['cost'] or 0),
        }
        for r in rows
    ]


def recipe_catalog_costs(limit=10):
    costly = []
    for recipe in Recipe.objects.filter(is_active=True).prefetch_related('items__product'):
        info = recipe_nutrition(recipe, 1)
        if info['items']:
            costly.append(
                {
                    'recipe': recipe,
                    'cost': info['cost_per_portion'],
                    'kcal': info['kcal_per_portion'],
                    'protein': info['protein_per_portion'],
                    'fat': info['fat_per_portion'],
                    'carbs': info['carbs_per_portion'],
                }
            )
    costly.sort(key=lambda x: x['cost'], reverse=True)
    return costly[:limit]


def stock_snapshot():
    products = Product.objects.filter(is_active=True).only('quantity', 'avg_cost', 'min_stock')
    lot_totals = {
        row['product_id']: money(row['total'] or 0)
        for row in StockLot.objects.filter(quantity__gt=0)
        .values('product_id')
        .annotate(total=Sum(F('quantity') * F('unit_cost')))
    }
    value = money(0)
    low = 0
    for p in products:
        value = money(value + lot_totals.get(p.pk, money(0)))
        if p.quantity <= p.min_stock:
            low += 1
    return {'value': value, 'low_count': low, 'sku_count': products.count()}


def build_analytics(mode='month', year=None, month=None, day=None):
    today = timezone.localdate()
    year = year or today.year
    month = month or today.month
    day = parse_date(day, today)
    if mode not in ('day', 'week', 'month'):
        mode = 'month'

    start, end, label = _period_bounds(mode, year, month, day)
    prev_start, prev_end = _prev_period(mode, start, end)

    current = _period_kpis(start, end)
    previous = _period_kpis(prev_start, prev_end)
    stock = stock_snapshot()

    return {
        'mode': mode,
        'year': year,
        'month': month,
        'day': day,
        'start': start,
        'end': end,
        'label': label,
        'prev_label': f"{prev_start.strftime('%d.%m')} — {prev_end.strftime('%d.%m')}",
        'kpis': current,
        'compare': {
            'cook_cost': _delta(current['cook_cost'], previous['cook_cost']),
            'portions': _delta(current['portions'], previous['portions']),
            'waste_cost': _delta(current['waste_cost'], previous['waste_cost']),
            'receipt_cost': _delta(current['receipt_cost'], previous['receipt_cost']),
        },
        'trend': daily_trend(start, end),
        'top_dishes': top_dishes(start, end),
        'product_out': product_consumption(start, end),
        'suppliers': supplier_stats(start, end),
        'waste_rows': waste_stats(start, end),
        'catalog_costly': recipe_catalog_costs(),
        'stock': stock,
        'budget': budget_status(start.year, start.month),
        'month_name': MONTH_NAMES.get(month, month),
    }
