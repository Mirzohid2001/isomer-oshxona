from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from kitchen.forms import HygieneCheckForm, PurchaseOrderForm, PurchaseOrderLineForm
from kitchen.models import (
    ApprovalStatus,
    CookBatch,
    HygieneCheck,
    PurchaseOrder,
    PurchaseOrderLine,
    StockChangeRequest,
    StockLot,
)
from kitchen.services.approvals import receive_purchase_order, review_change_request
from kitchen.services.cook import queue_cook, start_queued_cook
from kitchen.services.stock import StockError
from kitchen.utils import local_day_bounds, paginate


PurchaseOrderLineFormSet = inlineformset_factory(
    PurchaseOrder,
    PurchaseOrderLine,
    form=PurchaseOrderLineForm,
    extra=3,
    can_delete=True,
)


@login_required
def approval_list(request):
    pending = StockChangeRequest.objects.filter(status=ApprovalStatus.PENDING).select_related(
        'product', 'requested_by'
    )
    return render(request, 'kitchen/ops/approvals.html', {'pending': pending})


@login_required
@require_POST
def approval_review(request, pk):
    req = get_object_or_404(StockChangeRequest, pk=pk)
    approve = request.POST.get('decision') == 'approve'
    try:
        review_change_request(request_obj=req, reviewer=request.user, approve=approve)
        messages.success(request, 'Tasdiqlandi.' if approve else 'Rad etildi.')
    except StockError as exc:
        messages.error(request, str(exc))
    return redirect('approval_list')


@login_required
def lot_list(request):
    lots = StockLot.objects.filter(quantity__gt=0).select_related(
        'product', 'location', 'supplier'
    )
    page_obj, querystring = paginate(request, lots, per_page=40)
    return render(
        request,
        'kitchen/ops/lots.html',
        {'page_obj': page_obj, 'lots': page_obj, 'querystring': querystring},
    )


@login_required
def kds_board(request):
    from kitchen.forms import CookForm

    today = timezone.localdate()
    day_start, day_end = local_day_bounds(today)
    queued = CookBatch.objects.filter(
        status__in=[CookBatch.Status.QUEUED, CookBatch.Status.COOKING],
    ).select_related('recipe', 'created_by')
    done_today = CookBatch.objects.filter(
        status=CookBatch.Status.DONE,
        cooked_at__gte=day_start,
        cooked_at__lt=day_end,
    ).select_related('recipe')[:20]
    return render(
        request,
        'kitchen/ops/kds.html',
        {
            'queued': queued,
            'done_today': done_today,
            'queue_form': CookForm(),
        },
    )


@login_required
@require_POST
def kds_start(request, pk):
    batch = get_object_or_404(CookBatch, pk=pk)
    try:
        real = start_queued_cook(batch=batch, user=request.user)
        messages.success(request, f'Pishirish boshlandi: {real.recipe.name}')
        return redirect('cook_detail', pk=real.pk)
    except StockError as exc:
        messages.error(request, str(exc))
    return redirect('kds_board')


@login_required
@require_POST
def kds_queue(request):
    from kitchen.forms import CookForm

    form = CookForm(request.POST)
    if form.is_valid():
        try:
            batch = queue_cook(
                recipe=form.cleaned_data['recipe'],
                portions=form.cleaned_data['portions'],
                user=request.user,
                note=form.cleaned_data.get('note') or '',
                shift=form.cleaned_data.get('shift') or '',
            )
            messages.success(request, f'Navbatga qo‘yildi va ombor rezerv qilindi: {batch.recipe.name}')
        except StockError as exc:
            messages.error(request, str(exc))
    else:
            messages.error(request, form.errors.as_text() or 'Forma xato.')
    return redirect('kds_board')


@login_required
def purchase_order_list(request):
    orders = PurchaseOrder.objects.select_related('supplier', 'created_by')
    page_obj, querystring = paginate(request, orders, per_page=25)
    return render(
        request,
        'kitchen/ops/po_list.html',
        {'page_obj': page_obj, 'orders': page_obj, 'querystring': querystring},
    )


@login_required
def purchase_order_create(request):
    order = PurchaseOrder(created_by=request.user)
    form = PurchaseOrderForm(request.POST or None, instance=order)
    formset = PurchaseOrderLineFormSet(request.POST or None, instance=order)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        lines = [
            f
            for f in formset.forms
            if f.cleaned_data
            and not f.cleaned_data.get('DELETE')
            and f.cleaned_data.get('product')
            and f.cleaned_data.get('quantity')
        ]
        if not lines:
            messages.error(request, 'Kamida bitta mahsulot qatori kerak.')
        else:
            order = form.save(commit=False)
            order.created_by = request.user
            order.status = PurchaseOrder.Status.ORDERED
            order.save()
            formset.instance = order
            formset.save()
            messages.success(request, f'Buyurtma yaratildi: PO-{order.pk}')
            return redirect('purchase_order_list')
    return render(
        request,
        'kitchen/ops/po_form.html',
        {'form': form, 'formset': formset, 'title': 'Yangi xarid buyurtmasi'},
    )


@login_required
@require_POST
def purchase_order_receive(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk)
    try:
        receive_purchase_order(order=order, user=request.user)
        messages.success(request, f'PO-{order.pk} omborga qabul qilindi.')
    except StockError as exc:
        messages.error(request, str(exc))
    return redirect('purchase_order_list')


@login_required
def hygiene_list(request):
    rows = HygieneCheck.objects.select_related('location', 'checked_by')
    page_obj, querystring = paginate(request, rows, per_page=30)
    form = HygieneCheckForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.checked_by = request.user
        obj.save()
        messages.success(request, 'Gigiyena yozuvi saqlandi.')
        return redirect('hygiene_list')
    return render(
        request,
        'kitchen/ops/hygiene.html',
        {'page_obj': page_obj, 'rows': page_obj, 'form': form, 'querystring': querystring},
    )
