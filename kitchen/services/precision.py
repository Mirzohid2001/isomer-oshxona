from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

MONEY_STEP = Decimal('0.01')
QTY_STEP = Decimal('0.001')
NUTRI_STEP = Decimal('0.01')


def as_decimal(value):
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InvalidOperation(str(exc)) from exc


def money(value):
    return as_decimal(value).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def qty(value):
    return as_decimal(value).quantize(QTY_STEP, rounding=ROUND_HALF_UP)


def nutri(value):
    return as_decimal(value).quantize(NUTRI_STEP, rounding=ROUND_HALF_UP)


def money_div(numerator, denominator):
    denominator = as_decimal(denominator)
    if denominator == 0:
        return money(0)
    return money(as_decimal(numerator) / denominator)


def nutri_div(numerator, denominator):
    denominator = as_decimal(denominator)
    if denominator == 0:
        return nutri(0)
    return nutri(as_decimal(numerator) / denominator)


def weighted_avg(old_qty, old_avg, add_qty, add_cost):
    old_qty = qty(old_qty)
    add_qty = qty(add_qty)
    old_avg = as_decimal(old_avg)
    add_cost = as_decimal(add_cost)
    new_qty = old_qty + add_qty
    if new_qty <= 0:
        return money(0), qty(0)
    total_value = (old_qty * old_avg) + (add_qty * add_cost)
    return money(total_value / new_qty), qty(new_qty)
