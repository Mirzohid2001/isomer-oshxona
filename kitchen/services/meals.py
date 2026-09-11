from calendar import monthrange
from collections import defaultdict
from datetime import date

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from kitchen.models import CookBatch, MealCheckin, MealType, Worker
from kitchen.utils import local_month_bounds


MEAL_ORDER = (MealType.BREAKFAST, MealType.LUNCH, MealType.DINNER, MealType.NIGHT)
MEAL_LABELS = dict(MealCheckin.MEAL_CHOICES)
MEAL_HINTS = {
    MealType.BREAKFAST: 'Ertalab',
    MealType.LUNCH: 'Tush payti',
    MealType.DINNER: 'Kechqurun',
    MealType.NIGHT: 'Tun',
}


def suggest_meal_type(now=None):
    now = now or timezone.localtime()
    hour = now.hour
    if hour < 10:
        return MealType.BREAKFAST
    if hour < 15:
        return MealType.LUNCH
    if hour < 20:
        return MealType.DINNER
    return MealType.NIGHT


def search_workers(query, limit=20):
    q = (query or '').strip()
    if not q:
        return Worker.objects.none()
    qs = Worker.objects.filter(is_active=True)
    for part in [p for p in q.split() if p]:
        qs = qs.filter(
            Q(first_name__icontains=part)
            | Q(last_name__icontains=part)
            | Q(employee_code__icontains=part)
            | Q(department__icontains=part)
        )
    return qs.order_by('last_name', 'first_name')[:limit]


@transaction.atomic
def record_meal_checkin(*, worker, meal_type, served_at=None, served_on=None, portions=1):
    """Bir ishchi / kun / mahal — faqat bir marta. Takror bo‘lsa ValueError."""
    from kitchen.services.worker_ops import resolve_served_at

    if meal_type not in MEAL_LABELS:
        raise ValueError('Noto‘g‘ri ovqat turi.')
    try:
        portions = int(portions)
    except (TypeError, ValueError):
        raise ValueError('Porsiya soni noto‘g‘ri.')
    if portions < 1:
        raise ValueError('Porsiya kamida 1 bo‘lishi kerak.')
    if portions > 500:
        raise ValueError('Porsiya juda katta (maks. 500).')

    worker = Worker.objects.select_for_update().get(pk=worker.pk)
    if not worker.is_active:
        raise ValueError('Ishchi faol emas.')

    when = resolve_served_at(served_on=served_on, served_at=served_at)
    day = timezone.localtime(when).date()
    if day > timezone.localdate():
        raise ValueError('Kelajak sanasini tanlab bo‘lmaydi.')

    try:
        return MealCheckin.objects.create(
            worker=worker,
            meal_type=meal_type,
            portions=portions,
            served_on=day,
            served_at=when,
        )
    except IntegrityError as exc:
        raise ValueError(
            f'{worker.full_name} · {day.strftime("%d.%m.%Y")} · '
            f'{MEAL_LABELS[meal_type]} allaqachon belgilangan. '
            f'Tahrirlash orqali porsiyani o‘zgartiring.'
        ) from exc


def month_span(year, month):
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    return start, end


def build_meal_report(year, month):
    """Oy uchun aniq otchot: jurnal, ishchi, kunlik sverka (pishirilgan vs yeyilgan)."""
    start, end = month_span(year, month)
    start_dt, end_dt = local_month_bounds(year, month)

    checkins = list(
        MealCheckin.objects.filter(served_on__gte=start, served_on__lte=end)
        .select_related('worker')
        .order_by('served_on', 'meal_type', 'worker__last_name', 'worker__first_name')
    )

    cooked_by_day_meal = defaultdict(int)
    main_by_recipe = defaultdict(int)
    main_detail_rows = []
    side_by_recipe = defaultdict(int)
    side_detail_rows = []

    all_batches = (
        CookBatch.objects.filter(
            status=CookBatch.Status.DONE,
            cooked_at__gte=start_dt,
            cooked_at__lt=end_dt,
        )
        .select_related('recipe', 'recipe__category')
        .only(
            'portions',
            'cooked_at',
            'recipe__name',
            'recipe__meal_type',
            'recipe__category_id',
            'recipe__category__name',
            'recipe__category__include_in_meal_sverka',
        )
        .order_by('cooked_at', 'id')
    )
    for batch in all_batches:
        day = timezone.localtime(batch.cooked_at).date()
        meal = batch.recipe.meal_type
        portions = int(batch.portions)
        is_main = (
            batch.recipe.category_id is None
            or batch.recipe.category.include_in_meal_sverka
        )
        row = {
            'date': day,
            'cooked_at': batch.cooked_at,
            'meal_type': meal,
            'meal_label': MEAL_LABELS.get(meal, meal),
            'recipe': batch.recipe.name,
            'category': batch.recipe.category.name if batch.recipe.category_id else '—',
            'portions': portions,
        }
        if is_main:
            if meal in MEAL_LABELS:
                cooked_by_day_meal[(day, meal)] += portions
            main_by_recipe[batch.recipe.name] += portions
            main_detail_rows.append(row)
        else:
            side_by_recipe[batch.recipe.name] += portions
            side_detail_rows.append(row)

    main_recipe_rows = [
        {'recipe': name, 'portions': qty}
        for name, qty in sorted(main_by_recipe.items(), key=lambda x: (-x[1], x[0]))
    ]
    side_recipe_rows = [
        {'recipe': name, 'portions': qty}
        for name, qty in sorted(side_by_recipe.items(), key=lambda x: (-x[1], x[0]))
    ]
    side_total = sum(side_by_recipe.values())

    eaten_by_day_meal = defaultdict(int)
    for row in checkins:
        eaten_by_day_meal[(row.served_on, row.meal_type)] += int(row.portions or 1)

    daily_rows = []
    totals = {
        'cooked': 0,
        'eaten': 0,
        'diff': 0,
        'by_meal': {m: {'cooked': 0, 'eaten': 0, 'diff': 0} for m in MEAL_ORDER},
    }
    cursor = start
    while cursor <= end:
        for meal in MEAL_ORDER:
            cooked = cooked_by_day_meal.get((cursor, meal), 0)
            eaten = eaten_by_day_meal.get((cursor, meal), 0)
            if cooked == 0 and eaten == 0:
                continue
            diff = cooked - eaten
            daily_rows.append(
                {
                    'date': cursor,
                    'meal_type': meal,
                    'meal_label': MEAL_LABELS[meal],
                    'cooked': cooked,
                    'eaten': eaten,
                    'diff': diff,
                }
            )
            totals['cooked'] += cooked
            totals['eaten'] += eaten
            totals['diff'] += diff
            totals['by_meal'][meal]['cooked'] += cooked
            totals['by_meal'][meal]['eaten'] += eaten
            totals['by_meal'][meal]['diff'] += diff
        cursor = date.fromordinal(cursor.toordinal() + 1)

    worker_map = {}
    for row in checkins:
        w = row.worker
        qty = int(row.portions or 1)
        bucket = worker_map.setdefault(
            w.pk,
            {
                'worker': w,
                'full_name': w.full_name,
                'department': w.department,
                'employee_code': w.employee_code,
                'meals': {m: 0 for m in MEAL_ORDER},
                'total': 0,
                'days': set(),
            },
        )
        bucket['meals'][row.meal_type] += qty
        bucket['total'] += qty
        bucket['days'].add(row.served_on)

    worker_rows = []
    for bucket in worker_map.values():
        worker_rows.append(
            {
                'worker': bucket['worker'],
                'full_name': bucket['full_name'],
                'department': bucket['department'],
                'employee_code': bucket['employee_code'],
                'breakfast': bucket['meals'][MealType.BREAKFAST],
                'lunch': bucket['meals'][MealType.LUNCH],
                'dinner': bucket['meals'][MealType.DINNER],
                'night': bucket['meals'][MealType.NIGHT],
                'total': bucket['total'],
                'days_count': len(bucket['days']),
            }
        )
    worker_rows.sort(key=lambda r: (-r['total'], r['full_name']))

    journal = [
        {
            'served_on': row.served_on,
            'served_at': row.served_at,
            'meal_type': row.meal_type,
            'meal_label': row.get_meal_type_display(),
            'portions': int(row.portions or 1),
            'worker': row.worker.full_name,
            'department': row.worker.department,
            'employee_code': row.worker.employee_code,
        }
        for row in checkins
    ]

    portion_total = sum(int(c.portions or 1) for c in checkins)
    return {
        'year': year,
        'month': month,
        'start': start,
        'end': end,
        'daily_rows': daily_rows,
        'worker_rows': worker_rows,
        'journal': journal,
        'totals': totals,
        'main_recipe_rows': main_recipe_rows,
        'main_detail_rows': main_detail_rows,
        'side_recipe_rows': side_recipe_rows,
        'side_detail_rows': side_detail_rows,
        'side_total': side_total,
        'unique_workers': len(worker_rows),
        'checkin_count': len(checkins),
        'portion_total': portion_total,
        'meal_labels': MEAL_LABELS,
        'meal_order': MEAL_ORDER,
        'meal_hints': MEAL_HINTS,
    }
