from datetime import date, datetime

from django.core.paginator import Paginator
from django.utils import timezone


def parse_date(value, fallback=None):
    if value is None or value == '':
        return fallback
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def parse_portions(value, fallback=1):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return max(1, fallback)
    return n if n >= 1 else max(1, fallback)


def local_today():
    return timezone.localdate()


def paginate(request, queryset, per_page=25, page_param='page'):
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get(page_param))
    params = request.GET.copy()
    params.pop(page_param, None)
    querystring = params.urlencode()
    return page_obj, querystring
