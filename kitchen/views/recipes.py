from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from kitchen.forms import RecipeCategoryForm, RecipeForm, RecipeItemFormSet
from kitchen.models import CookBatch, Recipe, RecipeCategory
from kitchen.services import recipe_nutrition
from kitchen.services.pdf import recipe_pdf
from kitchen.utils import paginate, parse_portions


@login_required
def recipe_list(request):
    q = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '').strip()
    recipes = Recipe.objects.filter(is_active=True).select_related('category').order_by('name')
    if q:
        recipes = recipes.filter(name__icontains=q)
    if category_id.isdigit():
        recipes = recipes.filter(category_id=int(category_id))
    page_obj, querystring = paginate(request, recipes, per_page=12)
    pks = [r.pk for r in page_obj]
    recipes_map = {
        r.pk: r
        for r in Recipe.objects.filter(pk__in=pks)
        .select_related('category')
        .prefetch_related('items__product')
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
            'category_id': category_id,
            'categories': RecipeCategory.objects.annotate(
                recipe_count=Count('recipes', filter=Q(recipes__is_active=True))
            ),
        },
    )


@login_required
def recipe_category_list(request):
    q = request.GET.get('q', '').strip()
    categories = RecipeCategory.objects.annotate(recipe_count=Count('recipes'))
    if q:
        categories = categories.filter(name__icontains=q)
    return render(
        request,
        'kitchen/recipe_categories/list.html',
        {'categories': categories, 'q': q},
    )


@login_required
def recipe_category_create(request):
    form = RecipeCategoryForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        cat = form.save()
        messages.success(request, f'Retsept kategoriyasi qo‘shildi: {cat.name}')
        return redirect('recipe_category_list')
    return render(
        request,
        'kitchen/form_page.html',
        {
            'form': form,
            'title': 'Yangi retsept kategoriyasi',
            'cancel_url': reverse('recipe_category_list'),
        },
    )


@login_required
def recipe_category_edit(request, pk):
    category = get_object_or_404(RecipeCategory, pk=pk)
    form = RecipeCategoryForm(request.POST or None, instance=category)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Retsept kategoriyasi yangilandi.')
        return redirect('recipe_category_list')
    return render(
        request,
        'kitchen/form_page.html',
        {
            'form': form,
            'title': f'Tahrir: {category.name}',
            'cancel_url': reverse('recipe_category_list'),
        },
    )


@login_required
def recipe_category_delete(request, pk):
    category = get_object_or_404(RecipeCategory, pk=pk)
    count = category.recipes.count()
    if count:
        messages.error(
            request,
            f'«{category.name}» o‘chirib bo‘lmaydi — {count} ta retsept bog‘langan.',
        )
        return redirect('recipe_category_list')
    name = category.name
    category.delete()
    messages.success(request, f'O‘chirildi: {name}')
    return redirect('recipe_category_list')


@login_required
def recipe_detail(request, pk):
    recipe = get_object_or_404(
        Recipe.objects.select_related('category').prefetch_related('items__product'),
        pk=pk,
    )
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
        {
            'form': form,
            'formset': formset,
            'title': f'{recipe.name} — tahrirlash',
            'recipe': recipe,
        },
    )
