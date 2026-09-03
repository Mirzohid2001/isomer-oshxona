from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from kitchen.forms import AdjustStockForm, ReceiptForm, WasteForm
from kitchen.models import MovementType, Product, StockMovement
from kitchen.services import StockError, adjust_stock, receive_stock, record_waste
from kitchen.services.export import spreadsheet_download
from kitchen.utils import paginate, parse_date


@login_required
def stock_list(request):
    from django.db.models import F as DF

    products = Product.objects.select_related('category').filter(is_active=True)
    filter_mode = request.GET.get('filter', '')
    if filter_mode == 'low':
        products = products.filter(quantity__lte=DF('min_stock'))
    elif filter_mode == 'expiring':
        today = timezone.localdate()
        products = products.filter(
            expiry_date__isnull=False,
            expiry_date__lte=today + timedelta(days=7),
            quantity__gt=0,
        )
    return render(
        request,
        'kitchen/stock/list.html',
        {'products': products, 'filter_mode': filter_mode},
    )


@login_required
def stock_adjust(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = AdjustStockForm(request.POST or None, initial={'new_quantity': product.quantity})
    if request.method == 'POST' and form.is_valid():
        try:
            from kitchen.services.approvals import submit_adjust_request

            movement, req = submit_adjust_request(
                product=product,
                new_quantity=form.cleaned_data['new_quantity'],
                user=request.user,
                note=form.cleaned_data['note'],
            )
            if req:
                messages.success(request, 'Tuzatish so‘rovi yuborildi — admin tasdiqlashi kerak.')
            else:
                messages.success(request, 'Qoldiq tuzatildi.')
            return redirect('stock_list')
        except StockError as exc:
            messages.error(request, str(exc))
    return render(
        request,
        'kitchen/stock/adjust.html',
        {'form': form, 'product': product},
    )


@login_required
def receipt_list(request):
    movements = StockMovement.objects.filter(
        movement_type=MovementType.IN,
        cook_batch__isnull=True,
    ).select_related('product', 'supplier', 'created_by')
    product_id = request.GET.get('product', '')
    date_from = parse_date(request.GET.get('from'))
    date_to = parse_date(request.GET.get('to'))
    if product_id:
        movements = movements.filter(product_id=product_id)
    if date_from:
        movements = movements.filter(created_at__date__gte=date_from)
    if date_to:
        movements = movements.filter(created_at__date__lte=date_to)
    page_obj, querystring = paginate(request, movements, per_page=25)
    return render(
        request,
        'kitchen/receipts/list.html',
        {
            'page_obj': page_obj,
            'movements': page_obj,
            'querystring': querystring,
            'products': Product.objects.filter(is_active=True),
            'product_id': product_id,
            'date_from': date_from.isoformat() if date_from else '',
            'date_to': date_to.isoformat() if date_to else '',
        },
    )


@login_required
def receipt_create(request):
    form = ReceiptForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            receive_stock(
                product=form.cleaned_data['product'],
                quantity=form.cleaned_data['quantity'],
                unit_cost=form.cleaned_data['unit_cost'],
                supplier=form.cleaned_data['supplier'],
                expiry_date=form.cleaned_data['expiry_date'],
                location=form.cleaned_data.get('location'),
                note=form.cleaned_data['note'],
                user=request.user,
            )
            messages.success(request, 'Prixod kiritildi.')
            return redirect('receipt_list')
        except StockError as exc:
            messages.error(request, str(exc))
    return render(request, 'kitchen/receipts/form.html', {'form': form})


@login_required
def receipt_export(request):
    movements = StockMovement.objects.filter(
        movement_type=MovementType.IN,
        cook_batch__isnull=True,
    ).select_related('product', 'supplier')
    product_id = request.GET.get('product')
    date_from = parse_date(request.GET.get('from'))
    date_to = parse_date(request.GET.get('to'))
    if product_id:
        movements = movements.filter(product_id=product_id)
    if date_from:
        movements = movements.filter(created_at__date__gte=date_from)
    if date_to:
        movements = movements.filter(created_at__date__lte=date_to)
    rows = [
        [
            m.created_at.strftime('%d.%m.%Y %H:%M'),
            m.product.name,
            m.quantity,
            m.unit_cost,
            m.total_cost,
            m.supplier.name if m.supplier else '',
            m.note,
        ]
        for m in movements
    ]
    subtitle_parts = []
    if date_from:
        subtitle_parts.append(f'dan {date_from:%d.%m.%Y}')
    if date_to:
        subtitle_parts.append(f'gacha {date_to:%d.%m.%Y}')
    return spreadsheet_download(
        request,
        filename='prixodlar',
        title='Prixodlar hisoboti',
        subtitle=' · '.join(subtitle_parts) or 'Barcha prixodlar',
        headers=['Sana', 'Mahsulot', 'Miqdor', 'Narx', 'Jami', 'Yetkazuvchi', 'Izoh'],
        rows=rows,
        numeric_cols={2, 3, 4},
    )


@login_required
def waste_list(request):
    movements = StockMovement.objects.filter(movement_type=MovementType.WASTE).select_related(
        'product', 'created_by'
    )
    page_obj, querystring = paginate(request, movements, per_page=25)
    return render(
        request,
        'kitchen/waste/list.html',
        {'page_obj': page_obj, 'movements': page_obj, 'querystring': querystring},
    )


@login_required
def waste_create(request):
    form = WasteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            note = form.cleaned_data['reason']
            if form.cleaned_data['note']:
                note = f"{note}: {form.cleaned_data['note']}"
            from kitchen.services.approvals import submit_waste_request

            movement, req = submit_waste_request(
                product=form.cleaned_data['product'],
                quantity=form.cleaned_data['quantity'],
                user=request.user,
                note=note,
            )
            if req:
                messages.success(request, 'Chiqindi so‘rovi yuborildi — admin tasdiqlashi kerak.')
            else:
                messages.success(request, 'Chiqindi yozildi.')
            return redirect('waste_list')
        except StockError as exc:
            messages.error(request, str(exc))
    return render(request, 'kitchen/form_page.html', {'form': form, 'title': 'Chiqindi / buzilish'})
