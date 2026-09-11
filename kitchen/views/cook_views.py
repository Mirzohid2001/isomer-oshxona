from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from kitchen.forms import CookForm
from kitchen.models import CookBatch, Recipe, recipe_in_meal_sverka_q
from kitchen.services import StockError, cancel_cook_batch, cook_recipes, recipes_nutrition
from kitchen.services.pdf import cook_batch_pdf
from kitchen.services.stock import allocation_rows_from_movement
from kitchen.utils import paginate
from kitchen.views.common import suggested_portions


def _cook_preview_from_form(form):
    recipes = form.cleaned_data.get('all_recipes') or [form.cleaned_data['recipe']]
    return recipes_nutrition(recipes, form.cleaned_data['portions'])


@login_required
def cook_create(request):
    suggest = suggested_portions()
    default_portions = suggest or 50
    form = CookForm(request.POST or None, initial={'portions': default_portions})
    preview = None
    if request.method == 'POST':
        if 'preview' in request.POST and form.is_valid():
            preview = _cook_preview_from_form(form)
        elif 'confirm' in request.POST and form.is_valid():
            try:
                batches = cook_recipes(
                    recipes=form.cleaned_data['all_recipes'],
                    portions=form.cleaned_data['portions'],
                    user=request.user,
                    note=form.cleaned_data['note'],
                    cooked_at=form.cleaned_data.get('cooked_on'),
                )
                names = ', '.join(f'{b.recipe.name} × {b.portions}' for b in batches)
                total = sum((b.total_cost for b in batches), Decimal('0'))
                messages.success(request, f'Pishirildi: {names}. Jami: {total} so‘m')
                return redirect('cook_detail', pk=batches[0].pk)
            except StockError as exc:
                messages.error(request, str(exc))
                preview = _cook_preview_from_form(form)
    recipe_id = request.GET.get('recipe')
    if recipe_id and not request.POST:
        portions = request.GET.get('portions') or default_portions
        initial = {'portions': portions}
        try:
            recipe = Recipe.objects.select_related('category').get(pk=recipe_id)
            if recipe.counts_in_meal_sverka:
                initial['recipe'] = recipe.pk
                form = CookForm(initial=initial)
                preview = recipes_nutrition([recipe], int(portions))
            else:
                main = (
                    Recipe.objects.filter(is_active=True)
                    .filter(recipe_in_meal_sverka_q())
                    .order_by('name')
                    .first()
                )
                if main:
                    initial['recipe'] = main.pk
                initial['sides'] = [recipe.pk]
                form = CookForm(initial=initial)
                recipes = ([main] if main else []) + [recipe]
                preview = recipes_nutrition(recipes, int(portions)) if recipes else None
        except (Recipe.DoesNotExist, ValueError, TypeError):
            form = CookForm(initial={'portions': portions})
            preview = None
    return render(
        request,
        'kitchen/cook/form.html',
        {'form': form, 'preview': preview, 'suggested': suggest},
    )


@login_required
def cook_preview_htmx(request):
    form = CookForm(request.GET or None)
    preview = None
    if form.is_valid():
        preview = _cook_preview_from_form(form)
    return render(request, 'kitchen/cook/partials/preview.html', {'preview': preview, 'form': form})


@login_required
def cook_history(request):
    batches = CookBatch.objects.select_related('recipe', 'recipe__category', 'created_by')
    page_obj, querystring = paginate(request, batches, per_page=25)
    return render(
        request,
        'kitchen/cook/history.html',
        {'page_obj': page_obj, 'batches': page_obj, 'querystring': querystring},
    )


@login_required
def cook_detail(request, pk):
    batch = get_object_or_404(
        CookBatch.objects.select_related('recipe').prefetch_related('items__product'),
        pk=pk,
    )
    movements = {
        m.product_id: m
        for m in batch.movements.prefetch_related('lot_allocations__lot').select_related('product')
    }
    item_rows = []
    for item in batch.items.all():
        movement = movements.get(item.product_id)
        allocations = allocation_rows_from_movement(movement) if movement else []
        item_rows.append(
            {
                'item': item,
                'allocations': allocations,
                'mixed': len(allocations) > 1,
                'movement_id': movement.pk if movement else None,
            }
        )
    return render(
        request,
        'kitchen/cook/detail.html',
        {'batch': batch, 'item_rows': item_rows},
    )


@login_required
@require_POST
def cook_cancel(request, pk):
    batch = get_object_or_404(CookBatch, pk=pk)
    open_statuses = {CookBatch.Status.QUEUED, CookBatch.Status.COOKING}
    if batch.status == CookBatch.Status.DONE and not request.user.is_staff:
        messages.error(request, 'Faqat admin yakunlangan pishirishni bekor qila oladi.')
        return redirect('cook_detail', pk=pk)
    if batch.status not in open_statuses | {CookBatch.Status.DONE}:
        messages.error(request, 'Bu holatda bekor qilib bo‘lmaydi.')
        return redirect('cook_detail', pk=pk)
    try:
        cancel_cook_batch(batch=batch, user=request.user)
        messages.success(request, 'Bekor qilindi, mahsulotlar omborga qaytarildi.')
    except StockError as exc:
        messages.error(request, str(exc))
    next_url = request.POST.get('next') or ''
    if next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect('cook_detail', pk=pk)


@login_required
def cook_print(request, pk):
    batch = get_object_or_404(
        CookBatch.objects.select_related('recipe').prefetch_related('items__product'),
        pk=pk,
    )
    movements = {
        m.product_id: m
        for m in batch.movements.prefetch_related('lot_allocations__lot')
    }
    item_rows = []
    for item in batch.items.all():
        movement = movements.get(item.product_id)
        allocations = allocation_rows_from_movement(movement) if movement else []
        item_rows.append({'item': item, 'allocations': allocations})
    return render(
        request,
        'kitchen/cook/print.html',
        {'batch': batch, 'item_rows': item_rows},
    )


@login_required
def cook_pdf_view(request, pk):
    batch = get_object_or_404(
        CookBatch.objects.select_related('recipe').prefetch_related('items__product'),
        pk=pk,
    )
    return cook_batch_pdf(batch)
