from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from kitchen.forms import SupplierPaymentForm
from kitchen.models import Supplier, SupplierPayment
from kitchen.services.debts import (
    delete_supplier_payment,
    debt_summary,
    record_supplier_payment,
    supplier_debt_detail,
)
from kitchen.services.precision import money


@login_required
def debt_list(request):
    summary = debt_summary()
    return render(
        request,
        'kitchen/debts/list.html',
        {
            'summary': summary,
            'rows': summary['rows'],
        },
    )


@login_required
def debt_supplier_detail(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    detail = supplier_debt_detail(supplier)
    return render(
        request,
        'kitchen/debts/supplier.html',
        {'detail': detail, 'supplier': supplier},
    )


@login_required
def debt_payment_create(request):
    supplier = None
    remaining = None
    sid = request.GET.get('supplier') or request.POST.get('supplier_lock')
    if sid:
        supplier = get_object_or_404(Supplier, pk=sid)
        remaining = supplier_debt_detail(supplier)['remaining']

    form = SupplierPaymentForm(
        request.POST or None,
        supplier=supplier,
        remaining=remaining,
    )
    if request.method == 'POST' and form.is_valid():
        try:
            payment = record_supplier_payment(
                supplier=form.cleaned_data['supplier'],
                amount=form.cleaned_data['amount'],
                paid_on=form.cleaned_data['paid_on'],
                note=form.cleaned_data.get('note') or '',
                user=request.user,
            )
            pay_amount = money(payment.amount)
            if remaining is not None and pay_amount > remaining:
                messages.warning(
                    request,
                    f'To‘lov qoldiqdan ({remaining} so‘m) ko‘p — oldindan to‘lov sifatida saqlandi.',
                )
            messages.success(
                request,
                f'To‘lov yozildi: {payment.supplier.name} — {payment.amount} so‘m',
            )
            return redirect('debt_supplier_detail', pk=payment.supplier_id)
        except ValueError as exc:
            messages.error(request, str(exc))
    return render(
        request,
        'kitchen/debts/payment_form.html',
        {
            'form': form,
            'supplier': supplier,
            'remaining': remaining,
            'title': 'Qarz to‘lovi',
            'cancel_url': (
                reverse('debt_supplier_detail', args=[supplier.pk])
                if supplier
                else reverse('debt_list')
            ),
        },
    )


@login_required
@require_POST
def debt_payment_delete(request, pk):
    payment = get_object_or_404(SupplierPayment.objects.select_related('supplier'), pk=pk)
    amount = payment.amount
    supplier = delete_supplier_payment(payment=payment, user=request.user)
    messages.success(request, f'To‘lov o‘chirildi ({supplier.name}: {amount}).')
    return redirect('debt_supplier_detail', pk=supplier.pk)
