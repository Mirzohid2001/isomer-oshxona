from datetime import timedelta
from hashlib import sha1

from django.core.cache import cache
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from kitchen.models import Product, StockLot

NOTIF_VERSION_KEY = 'kit:notif:ver'


def bump_notification_cache():
    """Ombor o‘zgarganda nav_alerts keshi yangilansin."""
    try:
        cache.incr(NOTIF_VERSION_KEY)
    except ValueError:
        cache.set(NOTIF_VERSION_KEY, 1, None)


def build_notifications(request=None):
    today = timezone.localdate()
    dismissed = []
    if request is not None:
        dismissed = list(request.session.get('dismissed_alerts', []))

    version = cache.get(NOTIF_VERSION_KEY) or 0
    cache_key = 'kit:notif:' + sha1(
        (f'{version}|' + ','.join(sorted(dismissed)) + '|' + today.isoformat()).encode()
    ).hexdigest()[:24]
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    items = []
    soon = today + timedelta(days=7)

    # Lot asosida muddat (FEFO) + product fallback
    expired_lots = (
        StockLot.objects.filter(quantity__gt=0, expiry_date__lt=today)
        .select_related('product')
        .order_by('expiry_date')
    )
    seen_expired = set()
    for lot in expired_lots:
        p = lot.product
        if not p.is_active or p.pk in seen_expired:
            continue
        seen_expired.add(p.pk)
        key = f'expired:{p.pk}'
        if key in dismissed:
            continue
        items.append(
            {
                'key': key,
                'kind': 'expired',
                'severity': 'danger',
                'title': f'Muddati o‘tgan: {p.name}',
                'detail': f'{p.quantity} {p.unit} · muddat {lot.expiry_date:%d.%m.%Y}',
                'url': reverse('stock_list') + '?filter=expiring',
            }
        )

    expiring_lots = (
        StockLot.objects.filter(quantity__gt=0, expiry_date__gte=today, expiry_date__lte=soon)
        .select_related('product')
        .order_by('expiry_date')
    )
    seen_expiring = set()
    for lot in expiring_lots:
        p = lot.product
        if not p.is_active or p.pk in seen_expiring or p.pk in seen_expired:
            continue
        seen_expiring.add(p.pk)
        key = f'expiring:{p.pk}'
        if key in dismissed:
            continue
        days = (lot.expiry_date - today).days
        items.append(
            {
                'key': key,
                'kind': 'expiring',
                'severity': 'warn',
                'title': f'Muddat yaqin: {p.name}',
                'detail': f'{p.quantity} {p.unit} · {days} kun qoldi ({lot.expiry_date:%d.%m.%Y})',
                'url': reverse('stock_list') + '?filter=expiring',
            }
        )

    # Fallback: product.expiry_date (lot yo‘q bo‘lsa)
    for p in Product.objects.filter(
        is_active=True,
        quantity__gt=0,
        expiry_date__isnull=False,
    ).filter(Q(expiry_date__lt=today) | Q(expiry_date__gte=today, expiry_date__lte=soon)):
        if p.pk in seen_expired or p.pk in seen_expiring:
            continue
        if p.expiry_date < today:
            key = f'expired:{p.pk}'
            kind, severity = 'expired', 'danger'
            title = f'Muddati o‘tgan: {p.name}'
            detail = f'{p.quantity} {p.unit} · muddat {p.expiry_date:%d.%m.%Y}'
        else:
            key = f'expiring:{p.pk}'
            kind, severity = 'expiring', 'warn'
            days = (p.expiry_date - today).days
            title = f'Muddat yaqin: {p.name}'
            detail = f'{p.quantity} {p.unit} · {days} kun qoldi ({p.expiry_date:%d.%m.%Y})'
        if key in dismissed:
            continue
        items.append(
            {
                'key': key,
                'kind': kind,
                'severity': severity,
                'title': title,
                'detail': detail,
                'url': reverse('stock_list') + '?filter=expiring',
            }
        )

    low = Product.objects.filter(is_active=True, quantity__lte=F('min_stock')).order_by('quantity')
    for p in low:
        key = f'low:{p.pk}'
        if key in dismissed:
            continue
        items.append(
            {
                'key': key,
                'kind': 'low',
                'severity': 'danger' if p.quantity <= 0 else 'warn',
                'title': f'Kam qoldiq: {p.name}',
                'detail': f'{p.quantity} {p.unit} (min {p.min_stock})',
                'url': reverse('stock_list') + '?filter=low',
            }
        )

    result = {'notifications': items, 'notification_count': len(items)}
    cache.set(cache_key, result, 60)
    return result
