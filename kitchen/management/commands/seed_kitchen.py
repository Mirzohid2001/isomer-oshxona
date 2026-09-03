from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from kitchen.models import (
    Category,
    MealType,
    MonthlyBudget,
    Product,
    Recipe,
    RecipeItem,
    Supplier,
    Unit,
)
from kitchen.services import receive_stock


class Command(BaseCommand):
    help = 'Namuna ma’lumotlar'

    def handle(self, *args, **options):
        User = get_user_model()
        if not User.objects.filter(username='oshpaz').exists():
            User.objects.create_superuser('oshpaz', 'oshpaz@local', 'oshpaz123')
            self.stdout.write(self.style.SUCCESS('User: oshpaz / oshpaz123'))

        cats = {}
        for name in ['Go‘sht', 'Sabzavot', 'Don', 'Sut', 'Yog‘', 'Ziravor']:
            cats[name], _ = Category.objects.get_or_create(name=name)

        supplier, _ = Supplier.objects.get_or_create(
            name='Markaziy bozor',
            defaults={'phone': '+998901112233'},
        )

        products_data = [
            # nomi, cat, unit, tannarx, kkal/birlik, oqsil g/birlik, yog' g/birlik, uglevod g/birlik, min
            ('Mol go‘shti', 'Go‘sht', Unit.KG, '45000', '2500', '260', '150', '0', '5'),
            ('Tovuq', 'Go‘sht', Unit.KG, '32000', '2150', '270', '100', '0', '8'),
            ('Kartoshka', 'Sabzavot', Unit.KG, '4000', '770', '20', '1', '170', '20'),
            ('Sabzi', 'Sabzavot', Unit.KG, '5000', '410', '9', '2', '100', '15'),
            ('Piyoz', 'Sabzavot', Unit.KG, '3500', '400', '11', '1', '90', '15'),
            ('Guruch', 'Don', Unit.KG, '18000', '3500', '70', '5', '780', '30'),
            ('Un', 'Don', Unit.KG, '9000', '3640', '100', '10', '760', '20'),
            ('Sut', 'Sut', Unit.L, '10000', '640', '32', '35', '47', '10'),
            ('Yog‘', 'Yog‘', Unit.L, '22000', '8840', '0', '920', '0', '5'),
            ('Tuz', 'Ziravor', Unit.KG, '3000', '0', '0', '0', '0', '2'),
        ]

        products = {}
        for name, cat, unit, cost, kcal, protein, fat, carbs, min_stock in products_data:
            product, created = Product.objects.get_or_create(
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
            )
            product.refresh_from_db()
            products[name] = product
            if created or product.quantity == 0:
                receive_stock(
                    product=product,
                    quantity=Decimal('50'),
                    unit_cost=Decimal(cost),
                    supplier=supplier,
                    note='Boshlang‘ich qoldiq',
                )

        recipe, created = Recipe.objects.get_or_create(
            name='Osh',
            defaults={
                'description': 'Klassik palov',
                'meal_type': MealType.LUNCH,
                'allergens': '',
            },
        )
        if created:
            RecipeItem.objects.create(recipe=recipe, product=products['Guruch'], quantity_per_portion=Decimal('0.120'))
            RecipeItem.objects.create(recipe=recipe, product=products['Mol go‘shti'], quantity_per_portion=Decimal('0.080'))
            RecipeItem.objects.create(recipe=recipe, product=products['Sabzi'], quantity_per_portion=Decimal('0.050'))
            RecipeItem.objects.create(recipe=recipe, product=products['Piyoz'], quantity_per_portion=Decimal('0.030'))
            RecipeItem.objects.create(recipe=recipe, product=products['Yog‘'], quantity_per_portion=Decimal('0.025'))
            RecipeItem.objects.create(recipe=recipe, product=products['Tuz'], quantity_per_portion=Decimal('0.003'))

        soup, created = Recipe.objects.get_or_create(
            name='Mastava',
            defaults={'meal_type': MealType.LUNCH, 'description': 'Go‘shtli sho‘rva'},
        )
        if created:
            RecipeItem.objects.create(recipe=soup, product=products['Mol go‘shti'], quantity_per_portion=Decimal('0.060'))
            RecipeItem.objects.create(recipe=soup, product=products['Guruch'], quantity_per_portion=Decimal('0.040'))
            RecipeItem.objects.create(recipe=soup, product=products['Kartoshka'], quantity_per_portion=Decimal('0.080'))
            RecipeItem.objects.create(recipe=soup, product=products['Sabzi'], quantity_per_portion=Decimal('0.030'))
            RecipeItem.objects.create(recipe=soup, product=products['Piyoz'], quantity_per_portion=Decimal('0.020'))

        today = timezone.localdate()
        MonthlyBudget.objects.get_or_create(
            year=today.year,
            month=today.month,
            defaults={'limit_amount': Decimal('50000000')},
        )

        from kitchen.models import DailyHeadcount, MenuTemplate, MenuTemplateItem, Shift

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

        template, created = MenuTemplate.objects.get_or_create(name='Oddiy ish haftasi')
        if created:
            for weekday in range(5):
                MenuTemplateItem.objects.create(
                    template=template,
                    weekday=weekday,
                    recipe=recipe,
                    meal_type=MealType.LUNCH,
                    portions=100,
                )
                MenuTemplateItem.objects.create(
                    template=template,
                    weekday=weekday,
                    recipe=soup,
                    meal_type=MealType.LUNCH,
                    portions=100,
                )

        self.stdout.write(self.style.SUCCESS('Seed tayyor'))
