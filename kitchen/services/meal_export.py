from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from kitchen.services.export import (
    ALT_FILL,
    BODY_FONT,
    BORDER,
    HEADER_FILL,
    HEADER_FONT,
    META_FONT,
    SUB_FONT,
    TITLE_FONT,
    TOTAL_FILL,
    TOTAL_FONT,
)


def _style_header_row(ws, row, col_count):
    for col in range(1, col_count + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.border = BORDER
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)


def _autosize(ws, col_count, min_w=10, max_w=36):
    for col in range(1, col_count + 1):
        letter = get_column_letter(col)
        width = min_w
        for cell in ws[letter]:
            if cell.value is None:
                continue
            width = max(width, min(max_w, len(str(cell.value)) + 2))
        ws.column_dimensions[letter].width = width


def meal_report_excel(report):
    """Varaqlar: Sverka, Asosiy ovqat, Qo‘shimchalar, Ishchilar, Jurnal."""
    wb = Workbook()
    year, month = report['year'], report['month']
    subtitle = f'{month:02d}.{year} · {report["start"].strftime("%d.%m.%Y")} — {report["end"].strftime("%d.%m.%Y")}'
    generated = timezone.localtime().strftime('%d.%m.%Y %H:%M')

    # --- Sheet 1: Sverka ---
    ws = wb.active
    ws.title = 'Sverka'
    ws['A1'] = 'Ovqatlanish sverkasi (pishirilgan vs yeyilgan)'
    ws['A1'].font = TITLE_FONT
    ws.merge_cells('A1:F1')
    ws['A2'] = subtitle
    ws['A2'].font = SUB_FONT
    ws.merge_cells('A2:F2')
    ws['A3'] = f'Yaratilgan: {generated}'
    ws['A3'].font = META_FONT

    headers = ['Sana', 'Mahal', 'Pishirilgan porsiya', 'Yeyilgan porsiya', 'Farq', 'Izoh']
    for i, h in enumerate(headers, 1):
        ws.cell(row=5, column=i, value=h)
    _style_header_row(ws, 5, len(headers))

    r = 6
    for row in report['daily_rows']:
        note = ''
        if row['diff'] > 0:
            note = 'Ortib pishirilgan'
        elif row['diff'] < 0:
            note = 'Pishirishdan ko‘p yeyilgan'
        else:
            note = 'Mos'
        values = [
            row['date'].strftime('%d.%m.%Y'),
            row['meal_label'],
            row['cooked'],
            row['eaten'],
            row['diff'],
            note,
        ]
        for c, val in enumerate(values, 1):
            cell = ws.cell(row=r, column=c, value=val)
            cell.font = BODY_FONT
            cell.border = BORDER
            if r % 2 == 0:
                cell.fill = ALT_FILL
        r += 1

    totals = report['totals']
    for c, val in enumerate(
        ['JAMI', '', totals['cooked'], totals['eaten'], totals['diff'], ''],
        1,
    ):
        cell = ws.cell(row=r, column=c, value=val)
        cell.font = TOTAL_FONT
        cell.fill = TOTAL_FILL
        cell.border = BORDER
    r += 2
    ws.cell(row=r, column=1, value='Mahal bo‘yicha jami').font = SUB_FONT
    r += 1
    for meal in report['meal_order']:
        m = totals['by_meal'][meal]
        ws.cell(row=r, column=1, value=report['meal_labels'][meal]).font = BODY_FONT
        ws.cell(row=r, column=3, value=m['cooked']).font = BODY_FONT
        ws.cell(row=r, column=4, value=m['eaten']).font = BODY_FONT
        ws.cell(row=r, column=5, value=m['diff']).font = BODY_FONT
        r += 1
    ws.cell(row=r, column=1, value='Eslatma: pishirilgan — faqat asosiy ovqat (qo‘shimchalar alohida varaqda)').font = META_FONT
    _autosize(ws, len(headers))

    # --- Sheet: Asosiy retseptlar ---
    ws_main = wb.create_sheet('Asosiy ovqat')
    ws_main['A1'] = 'Asosiy ovqat (sverkaga kiradi)'
    ws_main['A1'].font = TITLE_FONT
    ws_main.merge_cells('A1:D1')
    ws_main['A2'] = subtitle
    ws_main['A2'].font = SUB_FONT
    headers_main = ['Sana', 'Mahal', 'Retsept', 'Porsiya']
    for i, h in enumerate(headers_main, 1):
        ws_main.cell(row=4, column=i, value=h)
    _style_header_row(ws_main, 4, len(headers_main))
    r = 5
    for row in report.get('main_detail_rows', []):
        values = [
            row['date'].strftime('%d.%m.%Y'),
            row['meal_label'],
            row['recipe'],
            row['portions'],
        ]
        for c, val in enumerate(values, 1):
            cell = ws_main.cell(row=r, column=c, value=val)
            cell.font = BODY_FONT
            cell.border = BORDER
            if r % 2 == 0:
                cell.fill = ALT_FILL
        r += 1
    for c, val in enumerate(['JAMI', '', '', report['totals']['cooked']], 1):
        cell = ws_main.cell(row=r, column=c, value=val)
        cell.font = TOTAL_FONT
        cell.fill = TOTAL_FILL
        cell.border = BORDER
    _autosize(ws_main, len(headers_main))

    # --- Sheet: Qo‘shimchalar ---
    ws_side = wb.create_sheet('Qoshimchalar')
    ws_side['A1'] = 'Qo‘shimchalar (salat, kefir, kompot — sverkaga kirmaydi)'
    ws_side['A1'].font = TITLE_FONT
    ws_side.merge_cells('A1:E1')
    ws_side['A2'] = subtitle
    ws_side['A2'].font = SUB_FONT
    ws_side['A3'] = f'Jami porsiya: {report.get("side_total", 0)}'
    ws_side['A3'].font = META_FONT
    headers_side = ['Sana', 'Mahal', 'Retsept', 'Kategoriya', 'Porsiya']
    for i, h in enumerate(headers_side, 1):
        ws_side.cell(row=5, column=i, value=h)
    _style_header_row(ws_side, 5, len(headers_side))
    r = 6
    for row in report.get('side_detail_rows', []):
        values = [
            row['date'].strftime('%d.%m.%Y'),
            row['meal_label'],
            row['recipe'],
            row['category'],
            row['portions'],
        ]
        for c, val in enumerate(values, 1):
            cell = ws_side.cell(row=r, column=c, value=val)
            cell.font = BODY_FONT
            cell.border = BORDER
            if r % 2 == 0:
                cell.fill = ALT_FILL
        r += 1
    for c, val in enumerate(['JAMI', '', '', '', report.get('side_total', 0)], 1):
        cell = ws_side.cell(row=r, column=c, value=val)
        cell.font = TOTAL_FONT
        cell.fill = TOTAL_FILL
        cell.border = BORDER
    _autosize(ws_side, len(headers_side))

    # --- Sheet 2: Ishchilar ---
    ws2 = wb.create_sheet('Ishchilar')
    ws2['A1'] = 'Ishchi bo‘yicha ovqatlanish'
    ws2['A1'].font = TITLE_FONT
    ws2.merge_cells('A1:H1')
    ws2['A2'] = subtitle
    ws2['A2'].font = SUB_FONT
    headers2 = [
        '№',
        'Familiya Ism',
        'Bo‘lim',
        'Kod',
        'Nonushta',
        'Tushlik',
        'Kunduzgi',
        'Kechki ovqat',
        'Jami',
        'Kunlar',
    ]
    for i, h in enumerate(headers2, 1):
        ws2.cell(row=4, column=i, value=h)
    _style_header_row(ws2, 4, len(headers2))
    r = 5
    for idx, row in enumerate(report['worker_rows'], 1):
        values = [
            idx,
            row['full_name'],
            row['department'] or '—',
            row['employee_code'] or '—',
            row['breakfast'],
            row['lunch'],
            row['afternoon'],
            row['dinner'],
            row['total'],
            row['days_count'],
        ]
        for c, val in enumerate(values, 1):
            cell = ws2.cell(row=r, column=c, value=val)
            cell.font = BODY_FONT
            cell.border = BORDER
            if r % 2 == 0:
                cell.fill = ALT_FILL
        r += 1
    for c, val in enumerate(
        [
            '',
            f'Jami ishchi: {report["unique_workers"]}',
            '',
            '',
            '',
            '',
            '',
            '',
            report.get('portion_total', report['checkin_count']),
            '',
        ],
        1,
    ):
        cell = ws2.cell(row=r, column=c, value=val)
        cell.font = TOTAL_FONT
        cell.fill = TOTAL_FILL
        cell.border = BORDER
    _autosize(ws2, len(headers2))

    # --- Sheet 3: Jurnal ---
    ws3 = wb.create_sheet('Jurnal')
    ws3['A1'] = 'Batafsil jurnal (har bir QR belgilash)'
    ws3['A1'].font = TITLE_FONT
    ws3.merge_cells('A1:G1')
    ws3['A2'] = subtitle
    ws3['A2'].font = SUB_FONT
    headers3 = ['Sana', 'Vaqt', 'Mahal', 'Porsiya', 'Ishchi', 'Bo‘lim', 'Kod']
    for i, h in enumerate(headers3, 1):
        ws3.cell(row=4, column=i, value=h)
    _style_header_row(ws3, 4, len(headers3))
    r = 5
    for row in report['journal']:
        local_at = timezone.localtime(row['served_at'])
        values = [
            row['served_on'].strftime('%d.%m.%Y'),
            local_at.strftime('%H:%M:%S'),
            row['meal_label'],
            row.get('portions', 1),
            row['worker'],
            row['department'] or '—',
            row['employee_code'] or '—',
        ]
        for c, val in enumerate(values, 1):
            cell = ws3.cell(row=r, column=c, value=val)
            cell.font = BODY_FONT
            cell.border = BORDER
            if r % 2 == 0:
                cell.fill = ALT_FILL
        r += 1
    _autosize(ws3, len(headers3))

    buf = BytesIO()
    wb.save(buf)
    payload = buf.getvalue()
    filename = f'ovqatlanish_{year}_{month:02d}.xlsx'
    response = HttpResponse(
        payload,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
