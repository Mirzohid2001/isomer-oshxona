from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST


@login_required
@require_POST
def notification_dismiss(request):
    key = (request.POST.get('key') or '').strip()
    if key and len(key) < 80:
        dismissed = list(request.session.get('dismissed_alerts', []))
        if key not in dismissed:
            dismissed.append(key)
            request.session['dismissed_alerts'] = dismissed[-300:]
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/'
    return redirect(next_url)


@login_required
@require_POST
def notification_dismiss_all(request):
    from kitchen.services.notifications import build_notifications

    keys = [n['key'] for n in build_notifications(None)['notifications']]
    dismissed = list(request.session.get('dismissed_alerts', []))
    for key in keys:
        if key not in dismissed:
            dismissed.append(key)
    request.session['dismissed_alerts'] = dismissed[-300:]
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/'
    return redirect(next_url)
