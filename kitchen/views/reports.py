from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from kitchen.services.analytics import build_analytics
from kitchen.services.export import spreadsheet_download
from kitchen.services.pdf import report_pdf
from kitchen.utils import parse_date


@login_required
def reports(request):
    today = timezone.localdate()
    mode = request.GET.get('mode', 'month')
    try:
        year = int(request.GET.get('year') or today.year)
        month = int(request.GET.get('month') or today.month)
        if month < 1 or month > 12:
            month = today.month
    except ValueError:
        year, month = today.year, today.month
    day = parse_date(request.GET.get('day'), today)
    data = build_analytics(mode=mode, year=year, month=month, day=day)
    return render(request, 'kitchen/reports/index.html', data)


@login_required
def reports_export_out(request):
    today = timezone.localdate()
    mode = request.GET.get('mode', 'month')
    try:
        year = int(request.GET.get('year') or today.year)
        month = int(request.GET.get('month') or today.month)
    except ValueError:
        year, month = today.year, today.month
    day = parse_date(request.GET.get('day'), today)
    data = build_analytics(mode=mode, year=year, month=month, day=day)
    rows = [
        [r['name'], r['unit'], r['qty'], r['cost']]
        for r in data['product_out']
    ]
    return spreadsheet_download(
        request,
        filename=f"rasxod_{data['start']}_{data['end']}",
        title='Mahsulot rasxodi',
        subtitle=f'{data["label"]} · {data["start"]} — {data["end"]}',
        headers=['Mahsulot', 'Birlik', 'Miqdor', 'Summa'],
        rows=rows,
        numeric_cols={2, 3},
    )


@login_required
def reports_export_cooks(request):
    today = timezone.localdate()
    mode = request.GET.get('mode', 'month')
    try:
        year = int(request.GET.get('year') or today.year)
        month = int(request.GET.get('month') or today.month)
    except ValueError:
        year, month = today.year, today.month
    day = parse_date(request.GET.get('day'), today)
    data = build_analytics(mode=mode, year=year, month=month, day=day)
    rows = [
        [r['name'], r['times'], r['portions'], r['cost'], r['avg_portion']]
        for r in data['top_dishes']
    ]
    return spreadsheet_download(
        request,
        filename=f"ovqatlar_{data['start']}_{data['end']}",
        title='Ovqatlar hisoboti',
        subtitle=f'{data["label"]} · {data["start"]} — {data["end"]}',
        headers=['Ovqat', 'Necha marta', 'Porsiya', 'Jami tannarx', '1 porsiya'],
        rows=rows,
        numeric_cols={1, 2, 3, 4},
    )


@login_required
def reports_print(request):
    today = timezone.localdate()
    mode = request.GET.get('mode', 'month')
    try:
        year = int(request.GET.get('year') or today.year)
        month = int(request.GET.get('month') or today.month)
    except ValueError:
        year, month = today.year, today.month
    day = parse_date(request.GET.get('day'), today)
    data = build_analytics(mode=mode, year=year, month=month, day=day)
    return render(request, 'kitchen/reports/print.html', data)


@login_required
def reports_pdf_view(request):
    today = timezone.localdate()
    mode = request.GET.get('mode', 'month')
    try:
        year = int(request.GET.get('year') or today.year)
        month = int(request.GET.get('month') or today.month)
    except ValueError:
        year, month = today.year, today.month
    day = parse_date(request.GET.get('day'), today)
    data = build_analytics(mode=mode, year=year, month=month, day=day)
    return report_pdf(data)
