from kitchen.services.notifications import build_notifications


def nav_alerts(request):
    if not request.user.is_authenticated:
        return {}
    data = build_notifications(request)
    low = sum(1 for n in data['notifications'] if n['kind'] == 'low')
    expiring = sum(1 for n in data['notifications'] if n['kind'] == 'expiring')
    expired = sum(1 for n in data['notifications'] if n['kind'] == 'expired')
    return {
        'nav_low_stock': low,
        'nav_expiring': expiring,
        'nav_expired': expired,
        'nav_alert_total': data['notification_count'],
        'notifications': data['notifications'],
        'notification_count': data['notification_count'],
    }
