from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from kitchen.forms import CategoryForm, ProductForm, SupplierForm
from kitchen.models import Category, Product, Supplier
from kitchen.services import log_action
from kitchen.services.nutrition_lookup import suggest_nutrition
from kitchen.utils import paginate


@login_required
@require_GET
def product_nutrition_suggest(request):
    name = (request.GET.get('name') or '').strip()
    unit = (request.GET.get('unit') or '').strip() or None
    data = suggest_nutrition(name, unit=unit)
    return JsonResponse(data)


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
    category_id = request.GET.get('category')
    if category_id and not request.POST:
        form = ProductForm(initial={'category': category_id})
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Mahsulot qo‘shildi.')
        return redirect('product_list')
    return render(request, 'kitchen/products/form.html', {'form': form, 'title': 'Yangi mahsulot'})


@login_required
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=product)
    if request.method != 'POST':
        category_id = request.GET.get('category')
        if category_id:
            try:
                form.instance.category_id = int(category_id)
                form.initial['category'] = category_id
            except (TypeError, ValueError):
                pass
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
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    if request.method == 'POST' and form.is_valid():
        cat = form.save()
        log_action(request.user, 'kategoriya', 'category', cat.pk, cat.name)
        messages.success(request, f'Kategoriya qo‘shildi: {cat.name}')
        if next_url.startswith('/'):
            sep = '&' if '?' in next_url else '?'
            return redirect(f'{next_url}{sep}category={cat.pk}')
        return redirect('product_list')
    return render(
        request,
        'kitchen/form_page.html',
        {
            'form': form,
            'title': 'Yangi kategoriya',
            'next': next_url,
        },
    )


@login_required
@require_POST
def category_quick_create(request):
    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'ok': False, 'error': 'Nom bo‘sh bo‘lmasin.'}, status=400)
    if len(name) > 120:
        return JsonResponse({'ok': False, 'error': 'Nom juda uzun.'}, status=400)
    try:
        cat, created = Category.objects.get_or_create(name=name)
    except IntegrityError:
        cat = Category.objects.filter(name__iexact=name).first()
        created = False
        if cat is None:
            return JsonResponse({'ok': False, 'error': 'Saqlab bo‘lmadi.'}, status=400)
    if created:
        log_action(request.user, 'kategoriya', 'category', cat.pk, cat.name)
    return JsonResponse({'ok': True, 'id': cat.pk, 'name': cat.name, 'created': created})


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
