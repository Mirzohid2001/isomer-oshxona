from datetime import timedelta

from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from kitchen.models import Product


def build_notifications(request=None):
    today = timezone.localdate()
    dismissed = set()
    if request is not None:
        dismissed = set(request.session.get('dismissed_alerts', []))

    items = []

    expired = Product.objects.filter(
        is_active=True,
        expiry_date__isnull=False,
        expiry_date__lt=today,
        quantity__gt=0,
    ).order_by('expiry_date')
    for p in expired:
        key = f'expired:{p.pk}'
        if key in dismissed:
            continue
        items.append(
            {
                'key': key,
                'kind': 'expired',
                'severity': 'danger',
                'title': f'Muddati o‘tgan: {p.name}',
                'detail': f'{p.quantity} {p.unit} · muddat {p.expiry_date:%d.%m.%Y}',
                'url': reverse('stock_list') + '?filter=expiring',
            }
        )

    expiring = Product.objects.filter(
        is_active=True,
        expiry_date__isnull=False,
        expiry_date__gte=today,
        expiry_date__lte=today + timedelta(days=7),
        quantity__gt=0,
    ).order_by('expiry_date')
    for p in expiring:
        key = f'expiring:{p.pk}'
        if key in dismissed:
            continue
        days = (p.expiry_date - today).days
        items.append(
            {
                'key': key,
                'kind': 'expiring',
                'severity': 'warn',
                'title': f'Muddat yaqin: {p.name}',
                'detail': f'{p.quantity} {p.unit} · {days} kun qoldi ({p.expiry_date:%d.%m.%Y})',
                'url': reverse('stock_list') + '?filter=expiring',
            }
        )

    low = Product.objects.filter(
        is_active=True,
        quantity__lte=F('min_stock'),
    ).order_by('quantity')
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

    return {
        'notifications': items,
        'notification_count': len(items),
    }
