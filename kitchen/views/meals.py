from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from kitchen.forms import WorkerForm
from kitchen.models import MealCheckin, Worker
from kitchen.services.meal_export import meal_report_excel
from kitchen.services.meals import (
    MEAL_LABELS,
    MEAL_ORDER,
    build_meal_report,
    record_meal_checkin,
    search_workers,
    suggest_meal_type,
)
from kitchen.utils import paginate


def _checkin_url(request):
    return request.build_absolute_uri(reverse('meal_checkin'))


def _qr_image_url(data, size=320):
    return (
        'https://api.qrserver.com/v1/create-qr-code/'
        f'?size={size}x{size}&margin=12&data={quote(data, safe="")}'
    )


# --- Public QR flow (login yo‘q) ---


@require_GET
def meal_checkin(request):
    return render(
        request,
        'kitchen/meals/checkin.html',
        {
            'meal_choices': MealCheckin.MEAL_CHOICES,
            'suggested_meal': suggest_meal_type(),
            'today': timezone.localdate(),
        },
    )


@require_GET
def meal_checkin_search(request):
    q = request.GET.get('q', '')
    workers = search_workers(q, limit=25)
    return render(
        request,
        'kitchen/meals/partials/worker_results.html',
        {'workers': workers, 'q': q.strip()},
    )


@require_POST
def meal_checkin_submit(request):
    worker_id = request.POST.get('worker_id')
    meal_type = request.POST.get('meal_type')
    worker = get_object_or_404(Worker, pk=worker_id, is_active=True)
    try:
        checkin = record_meal_checkin(worker=worker, meal_type=meal_type)
    except ValueError as exc:
        return render(
            request,
            'kitchen/meals/checkin_result.html',
            {
                'ok': False,
                'error': str(exc),
                'worker': worker,
                'meal_label': MEAL_LABELS.get(meal_type, meal_type),
            },
            status=400,
        )
    return render(
        request,
        'kitchen/meals/checkin_result.html',
        {
            'ok': True,
            'checkin': checkin,
            'worker': worker,
            'meal_label': checkin.get_meal_type_display(),
        },
    )


# --- Staff: workers + QR + report ---


@login_required
def worker_list(request):
    q = request.GET.get('q', '').strip()
    workers = Worker.objects.all()
    if q:
        workers = workers.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(employee_code__icontains=q)
            | Q(department__icontains=q)
        )
    page_obj, querystring = paginate(request, workers, per_page=40)
    return render(
        request,
        'kitchen/meals/workers.html',
        {
            'page_obj': page_obj,
            'workers': page_obj,
            'q': q,
            'querystring': querystring,
            'active_count': Worker.objects.filter(is_active=True).count(),
        },
    )


@login_required
def worker_create(request):
    form = WorkerForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Ishchi qo‘shildi.')
        return redirect('worker_list')
    return render(
        request,
        'kitchen/meals/worker_form.html',
        {'form': form, 'title': 'Yangi ishchi'},
    )


@login_required
def worker_edit(request, pk):
    worker = get_object_or_404(Worker, pk=pk)
    form = WorkerForm(request.POST or None, instance=worker)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Ishchi yangilandi.')
        return redirect('worker_list')
    return render(
        request,
        'kitchen/meals/worker_form.html',
        {'form': form, 'title': worker.full_name, 'worker': worker},
    )


@login_required
def meal_qr_poster(request):
    url = _checkin_url(request)
    return render(
        request,
        'kitchen/meals/qr_poster.html',
        {
            'checkin_url': url,
            'qr_image_url': _qr_image_url(url, size=360),
        },
    )


@login_required
def meal_report(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get('year') or today.year)
        month = int(request.GET.get('month') or today.month)
    except (TypeError, ValueError):
        return HttpResponseBadRequest('Yil/oy noto‘g‘ri.')
    if month < 1 or month > 12 or year < 2020 or year > today.year + 1:
        return HttpResponseBadRequest('Yil/oy oralig‘i noto‘g‘ri.')

    report = build_meal_report(year, month)
    years = list(range(today.year, today.year - 4, -1))
    return render(
        request,
        'kitchen/meals/report.html',
        {
            'report': report,
            'years': years,
            'months': list(range(1, 13)),
            'selected_year': year,
            'selected_month': month,
            'meal_order': MEAL_ORDER,
            'meal_labels': MEAL_LABELS,
        },
    )


@login_required
def meal_report_export(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get('year') or today.year)
        month = int(request.GET.get('month') or today.month)
    except (TypeError, ValueError):
        return HttpResponseBadRequest('Yil/oy noto‘g‘ri.')
    report = build_meal_report(year, month)
    return meal_report_excel(report)
