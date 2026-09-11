from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from kitchen.models import (
    Category,
    CookBatch,
    DailyHeadcount,
    DailyMenu,
    DailyMenuItem,
    MealType,
    MenuTemplate,
    MenuTemplateItem,
    MonthlyBudget,
    Product,
    Recipe,
    RecipeCategory,
    RecipeItem,
    Shift,
    StockLot,
    StockLotAllocation,
    StockMovement,
    Supplier,
    Unit,
    Worker,
)
from kitchen.services import receive_stock


class Command(BaseCommand):
    help = 'Namuna ma’lumotlar (mahsulot, ombor, retsept, ishchi)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fresh',
            action='store_true',
            help='Eski demo/test ma’lumotni tozalab, qayta to‘ldiradi',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['fresh']:
            self._wipe_demo()
            self.stdout.write(self.style.WARNING('Eski ombor/retsept ma’lumoti tozalandi'))

        User = get_user_model()
        if not User.objects.filter(username='oshpaz').exists():
            User.objects.create_superuser('oshpaz', 'oshpaz@local', 'oshpaz123')
            self.stdout.write(self.style.SUCCESS('User: oshpaz / oshpaz123'))

        main_cat, _ = RecipeCategory.objects.get_or_create(
            name='Asosiy ovqat',
            defaults={'include_in_meal_sverka': True},
        )
        side_cat, _ = RecipeCategory.objects.get_or_create(
            name='Qo‘shimcha',
            defaults={'include_in_meal_sverka': False},
        )
        RecipeCategory.objects.filter(pk=main_cat.pk).update(include_in_meal_sverka=True)
        RecipeCategory.objects.filter(pk=side_cat.pk).update(include_in_meal_sverka=False)

        cats = {}
        for name in ['Go‘sht', 'Sabzavot', 'Don', 'Sut', 'Yog‘', 'Ziravor', 'Ichimlik', 'Meva']:
            cats[name], _ = Category.objects.get_or_create(name=name)

        supplier, _ = Supplier.objects.get_or_create(
            name='Markaziy bozor',
            defaults={'phone': '+998901112233'},
        )

        products_data = [
            # nomi, cat, unit, tannarx, kkal, oqsil, yog', uglevod, min, boshlang'ich qoldiq
            ('Mol go‘shti', 'Go‘sht', Unit.KG, '45000', '2500', '260', '150', '0', '5', '200'),
            ('Tovuq', 'Go‘sht', Unit.KG, '32000', '2150', '270', '100', '0', '8', '150'),
            ('Kartoshka', 'Sabzavot', Unit.KG, '4000', '770', '20', '1', '170', '20', '300'),
            ('Sabzi', 'Sabzavot', Unit.KG, '5000', '410', '9', '2', '100', '15', '150'),
            ('Piyoz', 'Sabzavot', Unit.KG, '3500', '400', '11', '1', '90', '15', '120'),
            ('Bodring', 'Sabzavot', Unit.KG, '6000', '150', '7', '1', '30', '5', '80'),
            ('Pomidor', 'Sabzavot', Unit.KG, '8000', '180', '9', '2', '40', '5', '80'),
            ('Guruch', 'Don', Unit.KG, '18000', '3500', '70', '5', '780', '30', '250'),
            ('Un', 'Don', Unit.KG, '9000', '3640', '100', '10', '760', '20', '100'),
            ('Sut', 'Sut', Unit.L, '10000', '640', '32', '35', '47', '10', '100'),
            ('Kefir', 'Sut', Unit.L, '12000', '560', '30', '25', '40', '10', '120'),
            ('Yog‘', 'Yog‘', Unit.L, '22000', '8840', '0', '920', '0', '5', '60'),
            ('Tuz', 'Ziravor', Unit.KG, '3000', '0', '0', '0', '0', '2', '30'),
            ('Shakar', 'Ziravor', Unit.KG, '12000', '3870', '0', '0', '1000', '5', '50'),
            ('Olma', 'Meva', Unit.KG, '10000', '520', '3', '2', '140', '10', '100'),
            ('Kompot (tayyor)', 'Ichimlik', Unit.L, '5000', '400', '0', '0', '100', '10', '150'),
        ]

        products = {}
        for name, cat, unit, cost, kcal, protein, fat, carbs, min_stock, qty in products_data:
            product, _ = Product.objects.get_or_create(
                name=name,
                defaults={
                    'category': cats[cat],
                    'unit': unit,
                    'kcal_per_unit': Decimal(kcal),
                    'protein': Decimal(protein),
                    'fat': Decimal(fat),
                    'carbs': Decimal(carbs),
                    'min_stock': Decimal(min_stock),
                },
            )
            Product.objects.filter(pk=product.pk).update(
                category=cats[cat],
                unit=unit,
                kcal_per_unit=Decimal(kcal),
                protein=Decimal(protein),
                fat=Decimal(fat),
                carbs=Decimal(carbs),
                min_stock=Decimal(min_stock),
                is_active=True,
            )
            product.refresh_from_db()
            products[name] = product
            target = Decimal(qty)
            if product.quantity < target:
                receive_stock(
                    product=product,
                    quantity=target - product.quantity,
                    unit_cost=Decimal(cost),
                    supplier=supplier,
                    note='Seed qoldiq',
                )
                product.refresh_from_db()

        # Boshqa (test) mahsulotlarga ham ombor qo‘shib qo‘yamiz — pishirish sinovi uchun
        for product in Product.objects.filter(is_active=True):
            if product.quantity < Decimal('100'):
                receive_stock(
                    product=product,
                    quantity=Decimal('200'),
                    unit_cost=product.avg_cost or Decimal('1000'),
                    supplier=supplier,
                    note='Seed to‘ldirish',
                )

        recipes_spec = [
            (
                'Osh',
                main_cat,
                MealType.LUNCH,
                'Klassik palov',
                [
                    ('Guruch', '0.120'),
                    ('Mol go‘shti', '0.080'),
                    ('Sabzi', '0.050'),
                    ('Piyoz', '0.030'),
                    ('Yog‘', '0.025'),
                    ('Tuz', '0.003'),
                ],
            ),
            (
                'Mastava',
                main_cat,
                MealType.LUNCH,
                'Go‘shtli sho‘rva',
                [
                    ('Mol go‘shti', '0.060'),
                    ('Guruch', '0.040'),
                    ('Kartoshka', '0.080'),
                    ('Sabzi', '0.030'),
                    ('Piyoz', '0.020'),
                ],
            ),
            (
                'Tovuq sho‘rva',
                main_cat,
                MealType.LUNCH,
                'Yengil sho‘rva',
                [
                    ('Tovuq', '0.070'),
                    ('Kartoshka', '0.060'),
                    ('Sabzi', '0.025'),
                    ('Piyoz', '0.020'),
                    ('Tuz', '0.003'),
                ],
            ),
            (
                'Salat',
                side_cat,
                MealType.LUNCH,
                'Achchiq-chuchuk / sabzavot salat',
                [
                    ('Pomidor', '0.040'),
                    ('Bodring', '0.040'),
                    ('Piyoz', '0.010'),
                ],
            ),
            (
                'Kefir porsiya',
                side_cat,
                MealType.LUNCH,
                'Ichimlik — sverkaga kirmaydi',
                [('Kefir', '0.200')],
            ),
            (
                'Kompot',
                side_cat,
                MealType.LUNCH,
                'Ichimlik — sverkaga kirmaydi',
                [
                    ('Kompot (tayyor)', '0.200'),
                    ('Shakar', '0.010'),
                ],
            ),
        ]

        for name, category, meal_type, description, items in recipes_spec:
            recipe, created = Recipe.objects.get_or_create(
                name=name,
                defaults={
                    'description': description,
                    'meal_type': meal_type,
                    'category': category,
                    'base_portions': 1,
                    'is_active': True,
                },
            )
            Recipe.objects.filter(pk=recipe.pk).update(
                description=description,
                meal_type=meal_type,
                category=category,
                is_active=True,
            )
            if created or recipe.items.count() == 0:
                recipe.items.all().delete()
                for product_name, qty in items:
                    RecipeItem.objects.create(
                        recipe=recipe,
                        product=products[product_name],
                        quantity_per_portion=Decimal(qty),
                    )

        Recipe.objects.filter(category__isnull=True).update(category=main_cat)
        for recipe in Recipe.objects.filter(name__in=['Kefir', 'Salat', 'Kompot', 'Kefir porsiya']):
            if recipe.category_id != side_cat.pk:
                recipe.category = side_cat
                recipe.save(update_fields=['category'])

        today = timezone.localdate()
        MonthlyBudget.objects.get_or_create(
            year=today.year,
            month=today.month,
            defaults={'limit_amount': Decimal('50000000')},
        )

        DailyHeadcount.objects.update_or_create(
            date=today,
            shift=Shift.ONE,
            defaults={'people_count': 120},
        )
        DailyHeadcount.objects.update_or_create(
            date=today,
            shift=Shift.TWO,
            defaults={'people_count': 80},
        )

        osh = Recipe.objects.get(name='Osh')
        mastava = Recipe.objects.get(name='Mastava')
        template, created = MenuTemplate.objects.get_or_create(name='Oddiy ish haftasi')
        if created:
            for weekday in range(5):
                MenuTemplateItem.objects.create(
                    template=template,
                    weekday=weekday,
                    recipe=osh,
                    meal_type=MealType.LUNCH,
                    portions=100,
                )
                MenuTemplateItem.objects.create(
                    template=template,
                    weekday=weekday,
                    recipe=mastava,
                    meal_type=MealType.LUNCH,
                    portions=100,
                )

        workers = [
            ('Ali', 'Karimov', 'Sex-1'),
            ('Vali', 'Sobirov', 'Sex-1'),
            ('Hasan', 'Tursunov', 'Sex-2'),
            ('Malika', 'Yoqubova', 'Ofis'),
            ('Dilnoza', 'Rahimova', 'Ofis'),
        ]
        for first, last, dept in workers:
            Worker.objects.get_or_create(
                first_name=first,
                last_name=last,
                defaults={'department': dept, 'is_active': True},
            )

        self.stdout.write(self.style.SUCCESS(
            f'Seed tayyor: {Product.objects.count()} mahsulot, '
            f'{Recipe.objects.count()} retsept, '
            f'{Worker.objects.count()} ishchi'
        ))

    def _wipe_demo(self):
        StockLotAllocation.objects.all().delete()
        StockLot.objects.all().delete()
        StockMovement.objects.all().delete()
        CookBatch.objects.all().delete()
        DailyMenuItem.objects.all().delete()
        DailyMenu.objects.all().delete()
        MenuTemplateItem.objects.all().delete()
        MenuTemplate.objects.all().delete()
        RecipeItem.objects.all().delete()
        Recipe.objects.all().delete()
        Product.objects.all().delete()
