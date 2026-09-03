from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from kitchen.forms import (
    AdjustStockForm,
    ApplyTemplateForm,
    BudgetForm,
    CategoryForm,
    CookForm,
    DailyMenuItemFormSet,
    HeadcountForm,
    MenuTemplateForm,
    MenuTemplateItemFormSet,
    ProductForm,
    ReceiptForm,
    RecipeForm,
    RecipeItemFormSet,
    SupplierForm,
    WasteForm,
)
from kitchen.models import (
    AuditLog,
    Category,
    CookBatch,
    DailyHeadcount,
    DailyMenu,
    DailyMenuItem,
    MenuTemplate,
    MonthlyBudget,
    MovementType,
    Product,
    Recipe,
    StockMovement,
    Supplier,
)
from kitchen.services import (
    StockError,
    adjust_stock,
    budget_status,
    cancel_cook_batch,
    cook_recipe,
    log_action,
    recipe_nutrition,
    receive_stock,
    record_waste,
    shopping_list_for_date,
    shopping_list_for_range,
)
from kitchen.services.analytics import build_analytics, stock_snapshot
from kitchen.services.export import spreadsheet_download
from kitchen.services.pdf import cook_batch_pdf, recipe_pdf, report_pdf, shopping_pdf
from kitchen.utils import local_today, paginate, parse_date, parse_portions


def suggested_portions(day=None):
    day = day or timezone.localdate()
    people = (
        DailyHeadcount.objects.filter(date=day).aggregate(t=Sum('people_count'))['t'] or 0
    )
    return people


@login_required
def dashboard(request):
    today = timezone.localdate()
    today_batches = CookBatch.objects.filter(
        cooked_at__date=today,
        status=CookBatch.Status.DONE,
    ).select_related('recipe')
    today_cost = today_batches.aggregate(t=Sum('total_cost'))['t'] or Decimal('0')
    today_portions = today_batches.aggregate(t=Sum('portions'))['t'] or 0
    low_products = Product.objects.filter(is_active=True, quantity__lte=F('min_stock'))[:8]
    expiring = Product.objects.filter(
        is_active=True,
        expiry_date__isnull=False,
        expiry_date__lte=today + timedelta(days=7),
        quantity__gt=0,
    ).order_by('expiry_date')[:8]
    people = (
        DailyHeadcount.objects.filter(date=today).aggregate(t=Sum('people_count'))['t'] or 0
    )
    stock = stock_snapshot()
    return render(
        request,
        'kitchen/dashboard.html',
        {
            'today_batches': today_batches,
            'today_cost': today_cost,
            'today_portions': today_portions,
            'low_products': low_products,
            'expiring': expiring,
            'people': people,
            'stock_value': stock['value'],
            'budget': budget_status(),
            'recent': CookBatch.objects.select_related('recipe')[:6],
        },
    )


@login_required
def search(request):
    q = request.GET.get('q', '').strip()
    products = recipes = movements = []
    if q:
        products = Product.objects.filter(name__icontains=q)[:8]
        recipes = Recipe.objects.filter(name__icontains=q)[:8]
        movements = StockMovement.objects.filter(
            Q(product__name__icontains=q) | Q(note__icontains=q)
        ).select_related('product')[:8]
    return render(
        request,
        'kitchen/partials/search_results.html',
        {'q': q, 'products': products, 'recipes': recipes, 'movements': movements},
    )


@login_required
def product_list(request):
    q = request.GET.get('q', '').strip()
    category_id = request.GET.get('category')
    show = request.GET.get('show', 'active')
    products = Product.objects.select_related('category')
    if show == 'inactive':
        products = products.filter(is_active=False)
    elif show != 'all':
        products = products.filter(is_active=True)
    if q:
        products = products.filter(name__icontains=q)
    if category_id:
        products = products.filter(category_id=category_id)
    products = products.order_by('name')
    page_obj, querystring = paginate(request, products, per_page=25)
    return render(
        request,
        'kitchen/products/list.html',
        {
            'page_obj': page_obj,
            'products': page_obj,
            'querystring': querystring,
            'categories': Category.objects.all(),
            'q': q,
            'category_id': category_id or '',
            'show': show,
        },
    )


@login_required
def product_create(request):
    form = ProductForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Mahsulot qo‘shildi.')
        return redirect('product_list')
    return render(request, 'kitchen/products/form.html', {'form': form, 'title': 'Yangi mahsulot'})


@login_required
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=product)
    if request.method == 'POST' and form.is_valid():
        form.save()
        log_action(request.user, 'tahrir', 'product', product.pk, product.name)
        messages.success(request, 'Mahsulot yangilandi.')
        return redirect('product_list')
    return render(
        request,
        'kitchen/products/form.html',
        {'form': form, 'title': product.name, 'product': product},
    )


@login_required
@require_POST
def product_toggle(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=['is_active'])
    log_action(
        request.user,
        'faol' if product.is_active else 'o‘chirish',
        'product',
        product.pk,
        product.name,
    )
    messages.success(
        request,
        f'«{product.name}» {"faollashtirildi" if product.is_active else "o‘chirildi (arxiv)"}.',
    )
    return redirect('product_list')


@login_required
def category_create(request):
    form = CategoryForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Kategoriya qo‘shildi.')
        return redirect('product_list')
    return render(request, 'kitchen/form_page.html', {'form': form, 'title': 'Kategoriya'})


@login_required
def supplier_list(request):
    return render(
        request,
        'kitchen/suppliers/list.html',
        {'suppliers': Supplier.objects.all()},
    )


@login_required
def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Yetkazib beruvchi qo‘shildi.')
        return redirect('supplier_list')
    return render(request, 'kitchen/form_page.html', {'form': form, 'title': 'Yetkazib beruvchi'})


@login_required
def supplier_edit(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Saqlandi.')
        return redirect('supplier_list')
    return render(request, 'kitchen/form_page.html', {'form': form, 'title': supplier.name})


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
            adjust_stock(
                product=product,
                new_quantity=form.cleaned_data['new_quantity'],
                user=request.user,
                note=form.cleaned_data['note'],
            )
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
def recipe_list(request):
    q = request.GET.get('q', '').strip()
    recipes = Recipe.objects.filter(is_active=True).order_by('name')
    if q:
        recipes = recipes.filter(name__icontains=q)
    page_obj, querystring = paginate(request, recipes, per_page=12)
    pks = [r.pk for r in page_obj]
    recipes_map = {
        r.pk: r
        for r in Recipe.objects.filter(pk__in=pks).prefetch_related('items__product')
    }
    cards = []
    for pk in pks:
        recipe = recipes_map[pk]
        cards.append({'recipe': recipe, 'info': recipe_nutrition(recipe, 1)})
    return render(
        request,
        'kitchen/recipes/list.html',
        {
            'page_obj': page_obj,
            'cards': cards,
            'querystring': querystring,
            'q': q,
        },
    )


@login_required
def recipe_detail(request, pk):
    recipe = get_object_or_404(Recipe.objects.prefetch_related('items__product'), pk=pk)
    portions = parse_portions(request.GET.get('portions'), recipe.base_portions or 1)
    info = recipe_nutrition(recipe, portions)
    if request.headers.get('HX-Request'):
        return render(
            request,
            'kitchen/recipes/partials/calc.html',
            {'recipe': recipe, 'info': info, 'portions': portions},
        )
    history = list(
        CookBatch.objects.filter(recipe=recipe, status=CookBatch.Status.DONE)
        .order_by('cooked_at')
        .values('cooked_at', 'cost_per_portion', 'kcal_per_portion', 'portions')[:40]
    )
    cost_chart = [
        {
            'label': row['cooked_at'].strftime('%d.%m'),
            'date': row['cooked_at'].strftime('%d.%m.%Y %H:%M'),
            'cost': float(row['cost_per_portion']),
            'portions': row['portions'],
            'kcal': float(row['kcal_per_portion']),
        }
        for row in history
    ]
    return render(
        request,
        'kitchen/recipes/detail.html',
        {
            'recipe': recipe,
            'info': info,
            'portions': portions,
            'cost_history': history,
            'cost_chart': cost_chart,
            'current_cost': info['cost_per_portion'],
        },
    )


@login_required
def recipe_print(request, pk):
    recipe = get_object_or_404(Recipe.objects.prefetch_related('items__product'), pk=pk)
    portions = parse_portions(request.GET.get('portions'), recipe.base_portions or 1)
    info = recipe_nutrition(recipe, portions)
    return render(
        request,
        'kitchen/recipes/print.html',
        {'recipe': recipe, 'info': info, 'portions': portions},
    )


@login_required
def recipe_pdf_view(request, pk):
    recipe = get_object_or_404(Recipe.objects.prefetch_related('items__product'), pk=pk)
    portions = parse_portions(request.GET.get('portions'), recipe.base_portions or 1)
    info = recipe_nutrition(recipe, portions)
    return recipe_pdf(recipe, info, portions)


@login_required
def recipe_create(request):
    form = RecipeForm(request.POST or None)
    formset = RecipeItemFormSet(request.POST or None)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        recipe = form.save()
        formset.instance = recipe
        formset.save()
        messages.success(request, 'Retsept yaratildi.')
        return redirect('recipe_detail', pk=recipe.pk)
    return render(
        request,
        'kitchen/recipes/form.html',
        {'form': form, 'formset': formset, 'title': 'Yangi retsept'},
    )


@login_required
def recipe_edit(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    form = RecipeForm(request.POST or None, instance=recipe)
    formset = RecipeItemFormSet(request.POST or None, instance=recipe)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        form.save()
        formset.save()
        messages.success(request, 'Retsept yangilandi.')
        return redirect('recipe_detail', pk=recipe.pk)
    return render(
        request,
        'kitchen/recipes/form.html',
        {'form': form, 'formset': formset, 'title': recipe.name, 'recipe': recipe},
    )


@login_required
def cook_create(request):
    suggest = suggested_portions()
    default_portions = suggest or 50
    form = CookForm(request.POST or None, initial={'portions': default_portions})
    preview = None
    if request.method == 'POST':
        if 'preview' in request.POST and form.is_valid():
            preview = recipe_nutrition(form.cleaned_data['recipe'], form.cleaned_data['portions'])
        elif 'confirm' in request.POST and form.is_valid():
            try:
                batch = cook_recipe(
                    recipe=form.cleaned_data['recipe'],
                    portions=form.cleaned_data['portions'],
                    user=request.user,
                    note=form.cleaned_data['note'],
                )
                messages.success(
                    request,
                    f'Pishirildi: {batch.recipe.name} × {batch.portions}. Tannarx: {batch.total_cost} so‘m',
                )
                return redirect('cook_detail', pk=batch.pk)
            except StockError as exc:
                messages.error(request, str(exc))
                preview = recipe_nutrition(
                    form.cleaned_data['recipe'], form.cleaned_data['portions']
                )
    recipe_id = request.GET.get('recipe')
    if recipe_id and not request.POST:
        portions = request.GET.get('portions') or default_portions
        form = CookForm(initial={'recipe': recipe_id, 'portions': portions})
        try:
            recipe = Recipe.objects.get(pk=recipe_id)
            preview = recipe_nutrition(recipe, int(portions))
        except (Recipe.DoesNotExist, ValueError, TypeError):
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
        preview = recipe_nutrition(form.cleaned_data['recipe'], form.cleaned_data['portions'])
    return render(request, 'kitchen/cook/partials/preview.html', {'preview': preview, 'form': form})


@login_required
def cook_history(request):
    batches = CookBatch.objects.select_related('recipe', 'created_by')
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
    return render(request, 'kitchen/cook/detail.html', {'batch': batch})


@login_required
@require_POST
def cook_cancel(request, pk):
    batch = get_object_or_404(CookBatch, pk=pk)
    if not request.user.is_staff:
        messages.error(request, 'Faqat admin bekor qila oladi.')
        return redirect('cook_detail', pk=pk)
    try:
        cancel_cook_batch(batch=batch, user=request.user)
        messages.success(request, 'Pishirish bekor qilindi, mahsulotlar qaytarildi.')
    except StockError as exc:
        messages.error(request, str(exc))
    return redirect('cook_detail', pk=pk)


@login_required
def cook_print(request, pk):
    batch = get_object_or_404(
        CookBatch.objects.select_related('recipe').prefetch_related('items__product'),
        pk=pk,
    )
    return render(request, 'kitchen/cook/print.html', {'batch': batch})


@login_required
def cook_pdf_view(request, pk):
    batch = get_object_or_404(
        CookBatch.objects.select_related('recipe').prefetch_related('items__product'),
        pk=pk,
    )
    return cook_batch_pdf(batch)


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
            record_waste(
                product=form.cleaned_data['product'],
                quantity=form.cleaned_data['quantity'],
                user=request.user,
                note=note,
            )
            messages.success(request, 'Chiqindi yozildi.')
            return redirect('waste_list')
        except StockError as exc:
            messages.error(request, str(exc))
    return render(request, 'kitchen/form_page.html', {'form': form, 'title': 'Chiqindi / buzilish'})


@login_required
def menu_day(request):
    day = parse_date(request.GET.get('date'), local_today())
    day_str = day.isoformat()
    menu, _ = DailyMenu.objects.get_or_create(date=day)
    people = suggested_portions(day)
    if request.method == 'POST':
        formset = DailyMenuItemFormSet(request.POST, instance=menu)
        if formset.is_valid():
            formset.save()
            log_action(request.user, 'menyu', 'daily_menu', menu.pk, str(menu.date))
            messages.success(request, 'Menyu saqlandi.')
            return redirect(f'{request.path}?date={day_str}')
    else:
        formset = DailyMenuItemFormSet(instance=menu)
    menu_rows = []
    for item in menu.items.select_related('recipe').prefetch_related('recipe__items__product'):
        info = recipe_nutrition(item.recipe, item.portions)
        menu_rows.append({'item': item, 'allergens': info['allergens']})
    return render(
        request,
        'kitchen/menu/day.html',
        {
            'menu': menu,
            'formset': formset,
            'day': day_str,
            'people': people,
            'menu_rows': menu_rows,
        },
    )


@login_required
@require_POST
def menu_fill_portions(request):
    day = parse_date(request.POST.get('date'), local_today())
    day_str = day.isoformat()
    people = suggested_portions(day)
    if not people:
        messages.error(request, 'Avval smena odam sonini kiriting.')
        return redirect(f'/menu/?date={day_str}')
    menu, _ = DailyMenu.objects.get_or_create(date=day)
    updated = menu.items.filter(is_cooked=False).update(portions=people)
    messages.success(request, f'{updated} ta ovqat porsiyasi {people} ga o‘rnatildi.')
    return redirect(f'/menu/?date={day_str}')


@login_required
@require_POST
def menu_cook_item(request, pk):
    item = get_object_or_404(DailyMenuItem, pk=pk)
    if item.is_cooked:
        messages.info(request, 'Allaqachon pishirilgan.')
        return redirect(f"/menu/?date={item.menu.date.isoformat()}")
    try:
        batch = cook_recipe(
            recipe=item.recipe,
            portions=item.portions,
            user=request.user,
            note=f'Menyu {item.menu.date}',
        )
        item.is_cooked = True
        item.cook_batch = batch
        item.save(update_fields=['is_cooked', 'cook_batch'])
        messages.success(request, f'{item.recipe.name} pishirildi.')
    except StockError as exc:
        messages.error(request, str(exc))
    return redirect(f"/menu/?date={item.menu.date.isoformat()}")


@login_required
def menu_templates(request):
    return render(
        request,
        'kitchen/menu/templates_list.html',
        {'templates': MenuTemplate.objects.all()},
    )


@login_required
def menu_template_create(request):
    form = MenuTemplateForm(request.POST or None)
    formset = MenuTemplateItemFormSet(request.POST or None)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        template = form.save()
        formset.instance = template
        formset.save()
        messages.success(request, 'Shablon yaratildi.')
        return redirect('menu_templates')
    return render(
        request,
        'kitchen/menu/template_form.html',
        {'form': form, 'formset': formset, 'title': 'Yangi shablon'},
    )


@login_required
def menu_template_edit(request, pk):
    template = get_object_or_404(MenuTemplate, pk=pk)
    form = MenuTemplateForm(request.POST or None, instance=template)
    formset = MenuTemplateItemFormSet(request.POST or None, instance=template)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        form.save()
        formset.save()
        messages.success(request, 'Shablon yangilandi.')
        return redirect('menu_templates')
    return render(
        request,
        'kitchen/menu/template_form.html',
        {'form': form, 'formset': formset, 'title': template.name},
    )


@login_required
def menu_apply_template(request):
    from django.urls import reverse

    form = ApplyTemplateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        template = form.cleaned_data['template']
        week_start = form.cleaned_data['week_start']
        items = list(template.items.select_related('recipe'))
        days = {week_start + timedelta(days=item.weekday) for item in items}
        for day in days:
            menu, _ = DailyMenu.objects.get_or_create(date=day)
            menu.items.all().delete()
        created = 0
        for item in items:
            day = week_start + timedelta(days=item.weekday)
            menu, _ = DailyMenu.objects.get_or_create(date=day)
            people = suggested_portions(day)
            portions = people if people else item.portions
            DailyMenuItem.objects.create(
                menu=menu,
                recipe=item.recipe,
                meal_type=item.meal_type,
                portions=portions,
            )
            created += 1
        log_action(request.user, 'shablon', 'menu_template', template.pk, str(week_start))
        messages.success(request, f'Shablon qo‘llandi (eski menyu almashtirildi): {created} ta ovqat.')
        return redirect(f"{reverse('menu_day')}?date={week_start.isoformat()}")
    return render(request, 'kitchen/form_page.html', {'form': form, 'title': 'Shablonni qo‘llash'})


@login_required
def headcount_list(request):
    form = HeadcountForm(request.POST or None, initial={'date': timezone.localdate()})
    if request.method == 'POST' and form.is_valid():
        obj, _ = DailyHeadcount.objects.update_or_create(
            date=form.cleaned_data['date'],
            shift=form.cleaned_data['shift'],
            defaults={'people_count': form.cleaned_data['people_count']},
        )
        messages.success(request, f'{obj} saqlandi.')
        return redirect('headcount_list')
    rows = DailyHeadcount.objects.all()[:60]
    return render(request, 'kitchen/headcount/list.html', {'form': form, 'rows': rows})


@login_required
def shopping_list(request):
    day_str = request.GET.get('date') or timezone.localdate().isoformat()
    mode = request.GET.get('mode', 'day')
    from datetime import date as date_cls

    day = date_cls.fromisoformat(day_str) if isinstance(day_str, str) else day_str
    if mode == 'week':
        data = shopping_list_for_range(day, 7)
        title_range = f"{data['start']} — {data['end']}"
    else:
        data = shopping_list_for_date(day)
        title_range = str(day)
    return render(
        request,
        'kitchen/shopping/list.html',
        {
            'day': day_str,
            'mode': mode,
            'title_range': title_range,
            'rows': data['rows'],
            'total_est': data['total_est'],
        },
    )


@login_required
def shopping_export(request):
    day_str = request.GET.get('date') or timezone.localdate().isoformat()
    mode = request.GET.get('mode', 'day')
    from datetime import date as date_cls

    day = date_cls.fromisoformat(day_str)
    data = shopping_list_for_range(day, 7) if mode == 'week' else shopping_list_for_date(day)
    rows = [
        [r['product'].name, r['product'].unit, r['need'], r['have'], r['buy'], r['est_cost']]
        for r in data['rows']
    ]
    mode_label = 'Haftalik' if mode == 'week' else 'Kunlik'
    title_range = (
        f"{data.get('start', day)} — {data.get('end', day)}" if mode == 'week' else str(day)
    )
    return spreadsheet_download(
        request,
        filename='xarid',
        title='Xarid ro‘yxati',
        subtitle=f'{title_range} · {mode_label}',
        headers=['Mahsulot', 'Birlik', 'Kerak', 'Bor', 'Sotib olish', 'Taxminiy summa'],
        rows=rows,
        totals=['JAMI', '', '', '', '', data['total_est']],
        numeric_cols={2, 3, 4, 5},
    )


@login_required
def shopping_print(request):
    day_str = request.GET.get('date') or timezone.localdate().isoformat()
    mode = request.GET.get('mode', 'day')
    from datetime import date as date_cls

    day = date_cls.fromisoformat(day_str)
    data = shopping_list_for_range(day, 7) if mode == 'week' else shopping_list_for_date(day)
    return render(
        request,
        'kitchen/shopping/print.html',
        {
            'day': day_str,
            'mode': mode,
            'rows': data['rows'],
            'total_est': data['total_est'],
            'title_range': (
                f"{data.get('start', day)} — {data.get('end', day)}" if mode == 'week' else str(day)
            ),
        },
    )


@login_required
def shopping_pdf_view(request):
    day_str = request.GET.get('date') or timezone.localdate().isoformat()
    mode = request.GET.get('mode', 'day')
    from datetime import date as date_cls

    day = date_cls.fromisoformat(day_str)
    data = shopping_list_for_range(day, 7) if mode == 'week' else shopping_list_for_date(day)
    title = (
        f"{data.get('start', day)} — {data.get('end', day)}" if mode == 'week' else str(day)
    )
    mode_label = 'Haftalik' if mode == 'week' else 'Kunlik'
    return shopping_pdf(title, data['rows'], data['total_est'], mode_label)


@login_required
def budget_page(request):
    today = timezone.localdate()
    form = BudgetForm(
        request.POST or None,
        initial={'year': today.year, 'month': today.month},
    )
    if request.method == 'POST' and form.is_valid():
        MonthlyBudget.objects.update_or_create(
            year=form.cleaned_data['year'],
            month=form.cleaned_data['month'],
            defaults={'limit_amount': form.cleaned_data['limit_amount']},
        )
        messages.success(request, 'Byudjet saqlandi.')
        return redirect('budget_page')
    return render(
        request,
        'kitchen/budget/page.html',
        {
            'form': form,
            'status': budget_status(),
            'history': MonthlyBudget.objects.all()[:12],
        },
    )


@login_required
def audit_list(request):
    logs = AuditLog.objects.select_related('user')
    action = request.GET.get('action', '').strip()
    date_from = parse_date(request.GET.get('from'))
    date_to = parse_date(request.GET.get('to'))
    if action:
        logs = logs.filter(action__icontains=action)
    if date_from:
        logs = logs.filter(created_at__date__gte=date_from)
    if date_to:
        logs = logs.filter(created_at__date__lte=date_to)
    actions = (
        AuditLog.objects.order_by('action').values_list('action', flat=True).distinct()
    )
    page_obj, querystring = paginate(request, logs, per_page=30)
    return render(
        request,
        'kitchen/audit/list.html',
        {
            'page_obj': page_obj,
            'logs': page_obj,
            'querystring': querystring,
            'action': action,
            'date_from': date_from.isoformat() if date_from else '',
            'date_to': date_to.isoformat() if date_to else '',
            'actions': actions,
        },
    )


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


@login_required
@require_POST
def notification_dismiss(request):
    key = (request.POST.get('key') or '').strip()
    if key and len(key) < 80:
        dismissed = list(request.session.get('dismissed_alerts', []))
        if key not in dismissed:
            dismissed.append(key)
            request.session['dismissed_alerts'] = dismissed[-300:]
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/'
    return redirect(next_url)


@login_required
@require_POST
def notification_dismiss_all(request):
    from kitchen.services.notifications import build_notifications

    keys = [n['key'] for n in build_notifications(None)['notifications']]
    dismissed = list(request.session.get('dismissed_alerts', []))
    for key in keys:
        if key not in dismissed:
            dismissed.append(key)
    request.session['dismissed_alerts'] = dismissed[-300:]
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/'
    return redirect(next_url)
