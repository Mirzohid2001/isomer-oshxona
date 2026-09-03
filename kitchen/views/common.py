from django.db.models import Sum
from django.utils import timezone

from kitchen.models import DailyHeadcount


def suggested_portions(day=None):
    day = day or timezone.localdate()
    people = (
        DailyHeadcount.objects.filter(date=day).aggregate(t=Sum('people_count'))['t'] or 0
    )
    return people
