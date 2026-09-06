from django import template

from kitchen.services.precision import as_decimal, qty

register = template.Library()


@register.filter(name='smart_qty')
def smart_qty(value):
    """Miqdorni ortiqcha nolllarsiz ko‘rsatadi: 2.000 → 2, 1.500 → 1.5"""
    if value is None or value == '':
        return ''
    try:
        number = qty(as_decimal(value))
    except Exception:
        return value
    text = format(number.normalize(), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'
