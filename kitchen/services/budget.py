from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from kitchen.models import CookBatch, MonthlyBudget, MovementType, StockMovement
from kitchen.services.precision import money


def budget_status(year=None, month=None):
    today = timezone.localdate()
    year = year or today.year
    month = month or today.month
    try:
        budget = MonthlyBudget.objects.get(year=year, month=month)
    except MonthlyBudget.DoesNotExist:
        return None

    spent = money(
        CookBatch.objects.filter(
            cooked_at__year=year,
            cooked_at__month=month,
            status=CookBatch.Status.DONE,
        ).aggregate(total=Sum('total_cost'))['total']
        or 0
    )
    waste = money(
        StockMovement.objects.filter(
            movement_type=MovementType.WASTE,
            created_at__year=year,
            created_at__month=month,
        ).aggregate(total=Sum('total_cost'))['total']
        or 0
    )
    total_spent = money(spent + waste)
    limit = money(budget.limit_amount)
    remaining = money(limit - total_spent)
    if limit > 0:
        percent = (total_spent / limit * Decimal('100')).quantize(Decimal('0.1'))
        percent_f = float(min(percent, Decimal('100')))
    else:
        percent_f = 0.0
    return {
        'budget': budget,
        'spent': total_spent,
        'cook_spent': spent,
        'waste_spent': waste,
        'remaining': remaining,
        'percent': percent_f,
        'over': remaining < 0,
    }
