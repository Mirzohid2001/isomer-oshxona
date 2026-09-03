from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from kitchen.forms import (
    ApplyTemplateForm,
    DailyMenuItemFormSet,
    HeadcountForm,
    MenuTemplateForm,
    MenuTemplateItemFormSet,
)
from kitchen.models import DailyHeadcount, DailyMenu, DailyMenuItem, MenuTemplate
from kitchen.services import StockError, cook_recipe, log_action, recipe_nutrition
from kitchen.utils import local_today, parse_date
from kitchen.views.common import suggested_portions


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
    return render(
        request,
        'kitchen/form_page.html',
        {'form': form, 'title': 'Shablonni qo‘llash', 'cancel_url': f"{reverse('menu_day')}?date={local_today().isoformat()}"},
    )


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
