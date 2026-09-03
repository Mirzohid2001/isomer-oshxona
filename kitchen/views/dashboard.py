from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import F, Q, Sum
from django.shortcuts import render
from django.utils import timezone

from kitchen.models import CookBatch, DailyHeadcount, Product, Recipe, StockMovement
from kitchen.services import budget_status
from kitchen.services.analytics import stock_snapshot
from kitchen.utils import local_day_bounds


@login_required
def dashboard(request):
    today = timezone.localdate()
    day_start, day_end = local_day_bounds(today)
    today_batches = CookBatch.objects.filter(
        cooked_at__gte=day_start,
        cooked_at__lt=day_end,
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
