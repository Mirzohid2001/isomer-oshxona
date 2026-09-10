from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from kitchen.forms import (
    MealCheckinEditForm,
    StaffMealCheckinForm,
    WorkerForm,
    WorkerImportForm,
)
from kitchen.models import MealCheckin, Worker
from kitchen.services.meal_export import meal_report_excel
from kitchen.services.meals import (
    MEAL_HINTS,
    MEAL_LABELS,
    MEAL_ORDER,
    build_meal_report,
    record_meal_checkin,
    search_workers,
    suggest_meal_type,
)
from kitchen.services.qr_poster import (
    checkin_absolute_url,
    door_poster_png_bytes,
    png_download_response,
    qr_png_bytes,
)
from kitchen.services.worker_ops import (
    build_today_board,
    delete_meal_checkin,
    import_workers_from_workbook,
    update_meal_checkin,
    worker_import_template_response,
)
from kitchen.utils import paginate, parse_date


def _meal_choice_cards():
    return [
        {
            'value': value,
            'label': label,
            'hint': MEAL_HINTS.get(value, ''),
        }
        for value, label in MealCheckin.MEAL_CHOICES
    ]


# --- Public QR flow (login yo‘q) ---


@require_GET
def meal_checkin(request):
    return render(
        request,
        'kitchen/meals/checkin.html',
        {
            'meal_choices': _meal_choice_cards(),
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
    served_on = parse_date(request.POST.get('served_on'), timezone.localdate())
    portions_raw = request.POST.get('portions') or '1'
    worker = get_object_or_404(Worker, pk=worker_id, is_active=True)
    try:
        checkin = record_meal_checkin(
            worker=worker,
            meal_type=meal_type,
            served_on=served_on,
            portions=portions_raw,
        )
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


# --- Staff: workers + QR + report + board ---


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
def worker_import(request):
    form = WorkerImportForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            result = import_workers_from_workbook(form.cleaned_data['file'])
        except Exception as exc:  # noqa: BLE001 — user-facing import errors
            messages.error(request, f'Import xato: {exc}')
        else:
            messages.success(
                request,
                f'Import: yangi {result["created"]}, yangilangan {result["updated"]}, '
                f'o‘tkazib yuborilgan {result["skipped"]}.',
            )
            for err in result.get('errors') or []:
                messages.warning(request, err)
            return redirect('worker_list')
    return render(request, 'kitchen/meals/worker_import.html', {'form': form})


@login_required
@require_GET
def worker_import_template(request):
    return worker_import_template_response()


@login_required
def meal_qr_poster(request):
    url = checkin_absolute_url(request)
    return render(
        request,
        'kitchen/meals/qr_poster.html',
        {
            'checkin_url': url,
            'qr_preview_url': reverse('meal_qr_image'),
        },
    )


@login_required
@require_GET
def meal_qr_image(request):
    payload = qr_png_bytes(checkin_absolute_url(request), box_size=12, border=2)
    return HttpResponse(payload, content_type='image/png')


@login_required
@require_GET
def meal_qr_download(request):
    payload = qr_png_bytes(checkin_absolute_url(request), box_size=20, border=2)
    return png_download_response(payload, 'isomer-ovqatlanish-qr.png')


@login_required
@require_GET
def meal_qr_door_download(request):
    logo = Path(settings.BASE_DIR) / 'static' / 'img' / 'isomer-logo.png'
    payload = door_poster_png_bytes(
        checkin_absolute_url(request),
        logo_path=str(logo) if logo.exists() else None,
    )
    return png_download_response(payload, 'isomer-ovqatlanish-eshik-plakat.png')


@login_required
def meal_today(request):
    day = parse_date(request.GET.get('date'), timezone.localdate())
    board = build_today_board(day)
    form = StaffMealCheckinForm(initial={'served_on': day, 'meal_type': suggest_meal_type()})
    if request.headers.get('HX-Request') == 'true' and request.GET.get('partial') == '1':
        return render(request, 'kitchen/meals/partials/today_live.html', {'board': board})
    return render(
        request,
        'kitchen/meals/today.html',
        {'board': board, 'form': form, 'selected_day': day},
    )


@login_required
@require_POST
def meal_checkin_manual(request):
    form = StaffMealCheckinForm(request.POST)
    next_day = request.POST.get('next_date') or ''
    if form.is_valid():
        try:
            record_meal_checkin(
                worker=form.cleaned_data['worker'],
                meal_type=form.cleaned_data['meal_type'],
                served_on=form.cleaned_data['served_on'],
                portions=form.cleaned_data['portions'],
            )
            messages.success(request, 'Ovqatlanish belgilandi.')
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, form.errors.as_text() or 'Forma xato.')
    if next_day:
        return redirect(f'{reverse("meal_today")}?date={next_day}')
    return redirect('meal_today')


@login_required
def meal_checkin_list(request):
    day = parse_date(request.GET.get('date'), None)
    meal_type = request.GET.get('meal_type', '').strip()
    q = request.GET.get('q', '').strip()
    rows = MealCheckin.objects.select_related('worker').all()
    if day:
        rows = rows.filter(served_on=day)
    if meal_type in MEAL_LABELS:
        rows = rows.filter(meal_type=meal_type)
    if q:
        rows = rows.filter(
            Q(worker__first_name__icontains=q)
            | Q(worker__last_name__icontains=q)
            | Q(worker__employee_code__icontains=q)
        )
    page_obj, querystring = paginate(request, rows, per_page=40)
    return render(
        request,
        'kitchen/meals/checkin_list.html',
        {
            'page_obj': page_obj,
            'rows': page_obj,
            'querystring': querystring,
            'selected_day': day,
            'selected_meal': meal_type,
            'q': q,
            'meal_choices': MealCheckin.MEAL_CHOICES,
        },
    )


@login_required
def meal_checkin_edit(request, pk):
    checkin = get_object_or_404(MealCheckin.objects.select_related('worker'), pk=pk)
    initial = {
        'worker': checkin.worker_id,
        'meal_type': checkin.meal_type,
        'portions': checkin.portions,
        'served_on': checkin.served_on,
    }
    form = MealCheckinEditForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            update_meal_checkin(
                checkin=checkin,
                worker=form.cleaned_data['worker'],
                meal_type=form.cleaned_data['meal_type'],
                served_on=form.cleaned_data['served_on'],
                portions=form.cleaned_data['portions'],
            )
            messages.success(request, 'Yozuv yangilandi.')
            return redirect('meal_checkin_list')
        except ValueError as exc:
            messages.error(request, str(exc))
    return render(
        request,
        'kitchen/meals/checkin_edit.html',
        {'form': form, 'checkin': checkin},
    )


@login_required
@require_POST
def meal_checkin_delete(request, pk):
    checkin = get_object_or_404(MealCheckin, pk=pk)
    label = f'{checkin.worker.full_name} · {checkin.served_on} · {checkin.get_meal_type_display()}'
    delete_meal_checkin(checkin=checkin)
    messages.success(request, f'O‘chirildi: {label}')
    next_url = request.POST.get('next') or ''
    if next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect('meal_checkin_list')


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
