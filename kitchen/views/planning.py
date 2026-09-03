from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from kitchen.forms import BudgetForm
from kitchen.models import AuditLog, MonthlyBudget
from kitchen.services import budget_status, shopping_list_for_date, shopping_list_for_range
from kitchen.services.export import spreadsheet_download
from kitchen.services.pdf import shopping_pdf
from kitchen.utils import local_today, paginate, parse_date


@login_required
def shopping_list(request):
    day_str = request.GET.get('date') or timezone.localdate().isoformat()
    mode = request.GET.get('mode', 'day')
    day = parse_date(day_str, local_today())
    if mode == 'week':
        data = shopping_list_for_range(day, 7)
        title_range = f"{data['start']} — {data['end']}"
    else:
        data = shopping_list_for_date(day)
        title_range = str(day)
    return render(
        request,
        'kitchen/shopping/list.html',
        {
            'day': day_str,
            'mode': mode,
            'title_range': title_range,
            'rows': data['rows'],
            'total_est': data['total_est'],
        },
    )


@login_required
def shopping_export(request):
    day_str = request.GET.get('date') or timezone.localdate().isoformat()
    mode = request.GET.get('mode', 'day')
    day = parse_date(day_str, local_today())
    data = shopping_list_for_range(day, 7) if mode == 'week' else shopping_list_for_date(day)
    rows = [
        [r['product'].name, r['product'].unit, r['need'], r['have'], r['buy'], r['est_cost']]
        for r in data['rows']
    ]
    mode_label = 'Haftalik' if mode == 'week' else 'Kunlik'
    title_range = (
        f"{data.get('start', day)} — {data.get('end', day)}" if mode == 'week' else str(day)
    )
    return spreadsheet_download(
        request,
        filename='xarid',
        title='Xarid ro‘yxati',
        subtitle=f'{title_range} · {mode_label}',
        headers=['Mahsulot', 'Birlik', 'Kerak', 'Bor', 'Sotib olish', 'Taxminiy summa'],
        rows=rows,
        totals=['JAMI', '', '', '', '', data['total_est']],
        numeric_cols={2, 3, 4, 5},
    )


@login_required
def shopping_print(request):
    day_str = request.GET.get('date') or timezone.localdate().isoformat()
    mode = request.GET.get('mode', 'day')
    day = parse_date(day_str, local_today())
    data = shopping_list_for_range(day, 7) if mode == 'week' else shopping_list_for_date(day)
    return render(
        request,
        'kitchen/shopping/print.html',
        {
            'day': day_str,
            'mode': mode,
            'rows': data['rows'],
            'total_est': data['total_est'],
            'title_range': (
                f"{data.get('start', day)} — {data.get('end', day)}" if mode == 'week' else str(day)
            ),
        },
    )


@login_required
def shopping_pdf_view(request):
    day_str = request.GET.get('date') or timezone.localdate().isoformat()
    mode = request.GET.get('mode', 'day')
    day = parse_date(day_str, local_today())
    data = shopping_list_for_range(day, 7) if mode == 'week' else shopping_list_for_date(day)
    title = (
        f"{data.get('start', day)} — {data.get('end', day)}" if mode == 'week' else str(day)
    )
    mode_label = 'Haftalik' if mode == 'week' else 'Kunlik'
    return shopping_pdf(title, data['rows'], data['total_est'], mode_label)


@login_required
def budget_page(request):
    today = timezone.localdate()
    form = BudgetForm(
        request.POST or None,
        initial={'year': today.year, 'month': today.month},
    )
    if request.method == 'POST' and form.is_valid():
        MonthlyBudget.objects.update_or_create(
            year=form.cleaned_data['year'],
            month=form.cleaned_data['month'],
            defaults={'limit_amount': form.cleaned_data['limit_amount']},
        )
        messages.success(request, 'Byudjet saqlandi.')
        return redirect('budget_page')
    return render(
        request,
        'kitchen/budget/page.html',
        {
            'form': form,
            'status': budget_status(),
            'history': MonthlyBudget.objects.all()[:12],
        },
    )


@login_required
def audit_list(request):
    logs = AuditLog.objects.select_related('user')
    action = request.GET.get('action', '').strip()
    date_from = parse_date(request.GET.get('from'))
    date_to = parse_date(request.GET.get('to'))
    if action:
        logs = logs.filter(action__icontains=action)
    if date_from:
        logs = logs.filter(created_at__date__gte=date_from)
    if date_to:
        logs = logs.filter(created_at__date__lte=date_to)
    actions = (
        AuditLog.objects.order_by('action').values_list('action', flat=True).distinct()
    )
    page_obj, querystring = paginate(request, logs, per_page=30)
    return render(
        request,
        'kitchen/audit/list.html',
        {
            'page_obj': page_obj,
            'logs': page_obj,
            'querystring': querystring,
            'action': action,
            'date_from': date_from.isoformat() if date_from else '',
            'date_to': date_to.isoformat() if date_to else '',
            'actions': actions,
        },
    )
