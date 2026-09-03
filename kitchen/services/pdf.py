from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from fpdf import FPDF

FONT_DIR = Path(settings.BASE_DIR) / 'static' / 'fonts'


class KitchenPDF(FPDF):
    BRAND = (26, 85, 66)
    BRAND_DEEP = (15, 50, 40)
    ACCENT = (181, 106, 40)
    INK = (18, 32, 26)
    MUTED = (90, 111, 99)
    LINE = (197, 212, 203)
    ROW_ALT = (244, 248, 245)
    WHITE = (255, 255, 255)

    def __init__(self, title='Oshxona'):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.doc_title = title
        self.cover_done = False
        self.add_font('DejaVu', '', str(FONT_DIR / 'DejaVuSans.ttf'))
        self.add_font('DejaVu', 'B', str(FONT_DIR / 'DejaVuSans-Bold.ttf'))
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(14, 14, 14)
        self.alias_nb_pages()

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font('DejaVu', 'B', 9)
        self.set_text_color(*self.BRAND)
        self.cell(0, 6, 'Oshxona', new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(*self.LINE)
        self.line(14, self.get_y(), 196, self.get_y())
        self.ln(3)

    def footer(self):
        self.set_y(-14)
        self.set_font('DejaVu', '', 8)
        self.set_text_color(*self.MUTED)
        stamp = timezone.localtime().strftime('%d.%m.%Y %H:%M')
        self.cell(95, 6, stamp)
        self.cell(0, 6, f'Sahifa {self.page_no()}/{{nb}}', align='R')

    def cover(self, title, subtitle=None):
        self.add_page()
        self.set_fill_color(*self.BRAND_DEEP)
        self.rect(0, 0, 210, 36, style='F')
        self.set_xy(14, 11)
        self.set_font('DejaVu', 'B', 15)
        self.set_text_color(*self.WHITE)
        self.cell(0, 7, 'Oshxona')
        self.set_xy(14, 19)
        self.set_font('DejaVu', '', 9)
        self.set_text_color(210, 230, 220)
        self.cell(0, 5, 'Neft zavodi oshpaz tizimi')

        self.set_y(44)
        self.set_font('DejaVu', 'B', 20)
        self.set_text_color(*self.INK)
        self.multi_cell(0, 9, title)
        if subtitle:
            self.ln(1)
            self.set_font('DejaVu', '', 10)
            self.set_text_color(*self.MUTED)
            self.multi_cell(0, 5.5, subtitle)
        self.ln(2)
        self.set_draw_color(*self.LINE)
        y = self.get_y()
        self.set_line_width(0.4)
        self.line(14, y, 196, y)
        self.ln(8)
        self.cover_done = True

    def section(self, text):
        self.ln(2)
        self.set_font('DejaVu', 'B', 12)
        self.set_text_color(*self.BRAND)
        self.cell(0, 8, text, new_x='LMARGIN', new_y='NEXT')
        self.set_draw_color(*self.ACCENT)
        self.set_line_width(0.8)
        self.line(14, self.get_y(), 42, self.get_y())
        self.ln(4)

    def kpis(self, items):
        usable = self.w - self.l_margin - self.r_margin
        w = usable / len(items)
        y0 = self.get_y()
        for label, value in items:
            x = self.get_x()
            self.set_fill_color(*self.ROW_ALT)
            self.set_draw_color(*self.LINE)
            self.rect(x, y0, w - 2, 18, style='DF')
            self.set_xy(x + 3, y0 + 3)
            self.set_font('DejaVu', '', 8)
            self.set_text_color(*self.MUTED)
            self.cell(w - 6, 4, label)
            self.set_xy(x + 3, y0 + 8)
            self.set_font('DejaVu', 'B', 12)
            self.set_text_color(*self.INK)
            self.cell(w - 6, 7, str(value))
            self.set_xy(x + w, y0)
        self.ln(20)

    def table(self, headers, rows, col_widths=None, aligns=None, money_cols=None):
        money_cols = money_cols or set()
        aligns = aligns or ['L'] * len(headers)
        usable = self.w - self.l_margin - self.r_margin
        if not col_widths:
            col_widths = [usable / len(headers)] * len(headers)

        def draw_header():
            self.set_font('DejaVu', 'B', 9)
            self.set_fill_color(*self.BRAND)
            self.set_text_color(*self.WHITE)
            self.set_draw_color(*self.BRAND)
            for i, h in enumerate(headers):
                self.cell(col_widths[i], 9, str(h), border=1, fill=True, align='C')
            self.ln()

        draw_header()
        self.set_font('DejaVu', '', 9)
        for r_idx, row in enumerate(rows):
            if self.get_y() > 268:
                self.add_page()
                draw_header()
            fill = self.ROW_ALT if r_idx % 2 else self.WHITE
            self.set_fill_color(*fill)
            self.set_text_color(*self.INK)
            self.set_draw_color(*self.LINE)
            for i, cell in enumerate(row):
                text = money_txt(cell) if i in money_cols else str(cell)
                if i in money_cols and text != '0':
                    text = f'{text} so‘m'
                self.cell(col_widths[i], 8, text[:48], border=1, fill=True, align=aligns[i])
            self.ln()

    def total_row(self, label, value):
        self.ln(2)
        self.set_fill_color(*self.BRAND)
        self.set_text_color(*self.WHITE)
        self.set_font('DejaVu', 'B', 11)
        self.cell(130, 10, label, fill=True, align='L')
        self.cell(52, 10, f'{money_txt(value)} so‘m', fill=True, align='R')
        self.ln(12)


def pdf_response(pdf: KitchenPDF, filename: str) -> HttpResponse:
    payload = bytes(pdf.output())
    response = HttpResponse(payload, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def money_txt(value):
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return '0'
    return f'{n:,.0f}'.replace(',', ' ')


def cook_batch_pdf(batch):
    pdf = KitchenPDF()
    pdf.cover(
        'Pishirish varaqasi',
        f'{batch.recipe.name} · {batch.portions} porsiya · {batch.cooked_at.strftime("%d.%m.%Y %H:%M")}',
    )
    pdf.kpis([
        ('1 porsiya', f'{money_txt(batch.cost_per_portion)} so‘m'),
        ('Kkal / porsiya', money_txt(batch.kcal_per_portion)),
        ('Jami tannarx', f'{money_txt(batch.total_cost)} so‘m'),
    ])
    if batch.recipe.allergens:
        pdf.set_font('DejaVu', '', 9)
        pdf.set_text_color(*pdf.MUTED)
        pdf.cell(0, 6, f'Allergenlar: {batch.recipe.allergens}', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(4)
    pdf.section('Rasxod qilingan mahsulotlar')
    rows = [
        [item.product.name, f'{item.quantity} {item.product.unit}', item.line_cost]
        for item in batch.items.select_related('product')
    ]
    pdf.table(
        ['Mahsulot', 'Miqdor', 'Summa'],
        rows,
        [92, 38, 50],
        aligns=['L', 'C', 'R'],
        money_cols={2},
    )
    pdf.total_row('JAMI', batch.total_cost)
    return pdf_response(pdf, f'pishirish_{batch.pk}.pdf')


def recipe_pdf(recipe, info, portions):
    pdf = KitchenPDF()
    pdf.cover(
        recipe.name,
        f'{recipe.get_meal_type_display()} · {portions} porsiya',
    )
    pdf.kpis([
        ('1 porsiya', f'{money_txt(info["cost_per_portion"])} so‘m'),
        ('Kkal', money_txt(info['kcal_per_portion'])),
        (
            'BJU (O/Y/U)',
            f'{float(info["protein_per_portion"]):.1f} / {float(info["fat_per_portion"]):.1f} / {float(info["carbs_per_portion"]):.1f}',
        ),
    ])
    if recipe.allergens:
        pdf.set_font('DejaVu', '', 9)
        pdf.set_text_color(*pdf.MUTED)
        pdf.cell(0, 6, f'Allergenlar: {recipe.allergens}', new_x='LMARGIN', new_y='NEXT')
        pdf.ln(3)
    if recipe.description:
        pdf.set_font('DejaVu', '', 10)
        pdf.set_text_color(*pdf.INK)
        pdf.multi_cell(0, 5.5, recipe.description)
        pdf.ln(4)
    pdf.section('Ingredientlar')
    rows = [
        [row['product'].name, f'{row["need"]} {row["product"].unit}', row['line_cost']]
        for row in info['items']
    ]
    pdf.table(
        ['Mahsulot', 'Miqdor', 'Summa'],
        rows,
        [92, 38, 50],
        aligns=['L', 'C', 'R'],
        money_cols={2},
    )
    pdf.total_row('JAMI', info['total_cost'])
    return pdf_response(pdf, f'retsept_{recipe.pk}.pdf')


def shopping_pdf(title, rows, total_est, mode_label):
    pdf = KitchenPDF()
    pdf.cover('Xarid ro‘yxati', f'{title} · {mode_label}')
    table_rows = [
        [r['product'].name, f'{r["buy"]} {r["product"].unit}', r['est_cost']]
        for r in rows
    ]
    if not table_rows:
        pdf.set_font('DejaVu', '', 10)
        pdf.set_text_color(*pdf.MUTED)
        pdf.cell(0, 8, 'Ro‘yxat bo‘sh — barcha mahsulot yetarli')
    else:
        pdf.section('Sotib olish ro‘yxati')
        pdf.table(
            ['Mahsulot', 'Miqdor', 'Taxminiy summa'],
            table_rows,
            [92, 38, 50],
            aligns=['L', 'C', 'R'],
            money_cols={2},
        )
        pdf.total_row('JAMI TAXMINIY', total_est)
    return pdf_response(pdf, 'xarid.pdf')


def report_pdf(data):
    pdf = KitchenPDF()
    pdf.cover('Oshxona hisoboti', f'{data["label"]} · {data["start"]} — {data["end"]}')
    k = data['kpis']
    pdf.kpis([
        ('Porsiyalar', k['portions']),
        ('Pishirish', f'{money_txt(k["cook_cost"])} so‘m'),
        ('Chiqindi', f'{money_txt(k["waste_cost"])} so‘m'),
        ('Prixod', f'{money_txt(k["receipt_cost"])} so‘m'),
    ])
    pdf.section('Eng ko‘p xarajatli ovqatlar')
    dishes = [
        [r['name'], r['portions'], r['cost'], r['avg_portion']]
        for r in data['top_dishes']
    ]
    if dishes:
        pdf.table(
            ['Ovqat', 'Porsiya', 'Jami', '1 porsiya'],
            dishes,
            [72, 28, 40, 40],
            aligns=['L', 'C', 'R', 'R'],
            money_cols={2, 3},
        )
    else:
        pdf.set_font('DejaVu', '', 10)
        pdf.set_text_color(*pdf.MUTED)
        pdf.cell(0, 8, 'Bu davrda pishirish yo‘q')
        pdf.ln(4)
    pdf.section('Mahsulot rasxodi')
    products = [
        [r['name'], f'{r["qty"]} {r["unit"]}', r['cost']]
        for r in data['product_out']
    ]
    if products:
        pdf.table(
            ['Mahsulot', 'Miqdor', 'Summa'],
            products,
            [92, 38, 50],
            aligns=['L', 'C', 'R'],
            money_cols={2},
        )
    else:
        pdf.set_font('DejaVu', '', 10)
        pdf.set_text_color(*pdf.MUTED)
        pdf.cell(0, 8, 'Rasxod yo‘q')
    if data.get('budget'):
        b = data['budget']
        pdf.ln(4)
        pdf.section('Oylik byudjet')
        pdf.kpis([
            ('Limit', f'{money_txt(b["budget"].limit_amount)} so‘m'),
            ('Sarflangan', f'{money_txt(b["spent"])} so‘m'),
            ('Qoldi', f'{money_txt(b["remaining"])} so‘m'),
        ])
    return pdf_response(pdf, f'hisobot_{data["start"]}_{data["end"]}.pdf')
