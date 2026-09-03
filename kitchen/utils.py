from datetime import date, datetime, time, timedelta

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


def _aware(dt):
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def local_day_bounds(day):
    """Kalendar kun -> [start, next_day) aware datetime (index-friendly)."""
    start = _aware(datetime.combine(day, time.min))
    end = _aware(datetime.combine(day + timedelta(days=1), time.min))
    return start, end


def local_date_span_bounds(start_date, end_date):
    """Inklyuziv sanalar -> [start, end+1kun)."""
    start, _ = local_day_bounds(start_date)
    _, end = local_day_bounds(end_date)
    return start, end


def local_month_bounds(year, month):
    start_d = date(year, month, 1)
    if month == 12:
        end_d = date(year + 1, 1, 1)
    else:
        end_d = date(year, month + 1, 1)
    start = _aware(datetime.combine(start_d, time.min))
    end = _aware(datetime.combine(end_d, time.min))
    return start, end


def filter_dt_range(qs, field, start_date=None, end_date=None):
    """created_at__date o‘rniga gte/lt oralig‘i."""
    if start_date:
        start, _ = local_day_bounds(start_date)
        qs = qs.filter(**{f'{field}__gte': start})
    if end_date:
        _, end = local_day_bounds(end_date)
        qs = qs.filter(**{f'{field}__lt': end})
    return qs


def paginate(request, queryset, per_page=25, page_param='page'):
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get(page_param))
    params = request.GET.copy()
    params.pop(page_param, None)
    querystring = params.urlencode()
    return page_obj, querystring
