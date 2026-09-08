from io import BytesIO

import qrcode
from PIL import Image, ImageDraw, ImageFont
from django.http import HttpResponse
from django.urls import reverse


def checkin_absolute_url(request):
    return request.build_absolute_uri(reverse('meal_checkin'))


def make_qr_image(data, box_size=14, border=2):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color='#12201A', back_color='white').convert('RGB')


def qr_png_bytes(data, box_size=14, border=2):
    img = make_qr_image(data, box_size=box_size, border=border)
    buf = BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def _load_font(size, bold=False):
    candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf' if bold else '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/Library/Fonts/Arial.ttf',
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def door_poster_png_bytes(data, logo_path=None):
    """Oshxona eshigi uchun yuqori sifatli plakat (PNG)."""
    width, height = 1240, 1754  # ~A5 @ 150dpi, eshik uchun qulay
    bg = Image.new('RGB', (width, height), '#F7FBF8')
    draw = ImageDraw.Draw(bg)

    # Brand strip
    draw.rectangle((0, 0, width, 28), fill='#1A5542')
    draw.rectangle((0, height - 28, width, height), fill='#1A5542')

    y = 80
    if logo_path:
        try:
            logo = Image.open(logo_path).convert('RGBA')
            logo.thumbnail((160, 160), Image.Resampling.LANCZOS)
            lx = (width - logo.width) // 2
            bg.paste(logo, (lx, y), logo)
            y += logo.height + 28
        except OSError:
            pass

    title_font = _load_font(64, bold=True)
    sub_font = _load_font(30, bold=False)
    tip_font = _load_font(26, bold=False)
    url_font = _load_font(22, bold=False)

    def center_text(text, font, yy, fill='#12201A'):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((width - tw) // 2, yy), text, font=font, fill=fill)
        return yy + (bbox[3] - bbox[1]) + 14

    y = center_text('Isomer Oil', title_font, y, '#1A5542')
    y = center_text('Ovqatlanish', title_font, y)
    y = center_text('QR kodni skaner qiling', sub_font, y + 8, '#5A6F63')

    qr = make_qr_image(data, box_size=18, border=2)
    qr_size = 780
    qr = qr.resize((qr_size, qr_size), Image.Resampling.NEAREST)
    qx = (width - qr_size) // 2
    qy = y + 24
    # white frame
    pad = 28
    draw.rounded_rectangle(
        (qx - pad, qy - pad, qx + qr_size + pad, qy + qr_size + pad),
        radius=28,
        fill='white',
        outline='#C5D4CB',
        width=3,
    )
    bg.paste(qr, (qx, qy))
    y = qy + qr_size + pad + 36

    y = center_text('1) Ismni toping', tip_font, y, '#12201A')
    y = center_text('2) Mahalni tanlang', tip_font, y, '#12201A')
    y = center_text('3) Yuborish', tip_font, y, '#12201A')
    center_text(data, url_font, y + 18, '#5A6F63')

    buf = BytesIO()
    bg.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def png_download_response(payload, filename):
    response = HttpResponse(payload, content_type='image/png')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Content-Length'] = str(len(payload))
    return response
