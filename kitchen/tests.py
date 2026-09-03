from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from kitchen.models import Category, Product, Recipe, RecipeItem, Unit
from kitchen.services.cook import cancel_cook_batch, cook_recipe
from kitchen.services.precision import money, qty, weighted_avg
from kitchen.services.recipe_cost import recipe_nutrition
from kitchen.services.stock import receive_stock


class PrecisionTests(TestCase):
    def test_weighted_avg_exact(self):
        avg, q = weighted_avg('10', '1000', '10', '2000')
        self.assertEqual(q, Decimal('20.000'))
        self.assertEqual(avg, Decimal('1500.00'))

    def test_money_half_up(self):
        self.assertEqual(money('1.005'), Decimal('1.01'))
        self.assertEqual(money('1.004'), Decimal('1.00'))


class StockCalcTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('t', 't@t.t', 'x')
        self.cat = Category.objects.create(name='Test')
        self.product = Product.objects.create(
            name='GuruchT',
            category=self.cat,
            unit=Unit.KG,
            kcal_per_unit=Decimal('3500'),
            protein=Decimal('70'),
            fat=Decimal('5'),
            carbs=Decimal('780'),
        )

    def test_receive_updates_avg(self):
        receive_stock(
            product=self.product,
            quantity=Decimal('10'),
            unit_cost=Decimal('10000'),
            user=self.user,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal('10.000'))
        self.assertEqual(self.product.avg_cost, Decimal('10000.00'))
        receive_stock(
            product=self.product,
            quantity=Decimal('10'),
            unit_cost=Decimal('20000'),
            user=self.user,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal('20.000'))
        self.assertEqual(self.product.avg_cost, Decimal('15000.00'))
        self.assertEqual(self.product.stock_value, Decimal('300000.00'))


class RecipeCalcTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('t2', 't2@t.t', 'x')
        self.cat = Category.objects.create(name='Cat2')
        self.rice = Product.objects.create(
            name='Rice',
            category=self.cat,
            unit=Unit.KG,
            kcal_per_unit=Decimal('3500'),
            protein=Decimal('70'),
            fat=Decimal('5'),
            carbs=Decimal('780'),
        )
        self.meat = Product.objects.create(
            name='Meat',
            category=self.cat,
            unit=Unit.KG,
            kcal_per_unit=Decimal('2500'),
            protein=Decimal('260'),
            fat=Decimal('150'),
            carbs=Decimal('0'),
        )
        receive_stock(product=self.rice, quantity=Decimal('100'), unit_cost=Decimal('18000'), user=self.user)
        receive_stock(product=self.meat, quantity=Decimal('100'), unit_cost=Decimal('45000'), user=self.user)
        self.recipe = Recipe.objects.create(name='TestOsh', base_portions=1)
        RecipeItem.objects.create(
            recipe=self.recipe,
            product=self.rice,
            quantity_per_portion=Decimal('0.120'),
        )
        RecipeItem.objects.create(
            recipe=self.recipe,
            product=self.meat,
            quantity_per_portion=Decimal('0.080'),
        )

    def test_one_vs_ninety_scales(self):
        one = recipe_nutrition(self.recipe, 1)
        ninety = recipe_nutrition(self.recipe, 90)
        self.assertEqual(ninety['portions'], 90)
        self.assertEqual(qty(one['items'][0]['need'] * 90), ninety['items'][0]['need'])
        expected_total = money(one['total_cost'] * 90)
        self.assertEqual(ninety['total_cost'], expected_total)
        self.assertEqual(
            sum((i['line_cost'] for i in ninety['items']), money(0)),
            ninety['total_cost'],
        )

    def test_nutrition_per_portion(self):
        info = recipe_nutrition(self.recipe, 1)
        self.assertEqual(info['kcal_per_portion'], Decimal('620.00'))
        self.assertEqual(info['protein_per_portion'], Decimal('29.20'))
        self.assertEqual(info['fat_per_portion'], Decimal('12.60'))
        self.assertEqual(info['carbs_per_portion'], Decimal('93.60'))

    def test_invalid_portions(self):
        self.assertFalse(recipe_nutrition(self.recipe, 0)['can_cook'])
        self.assertFalse(recipe_nutrition(self.recipe, 'x')['can_cook'])
        self.assertFalse(recipe_nutrition(self.recipe, Decimal('1.5'))['can_cook'])

    def test_cook_matches_preview_and_cancel(self):
        preview = recipe_nutrition(self.recipe, 10)
        rice_before = Product.objects.get(pk=self.rice.pk).quantity
        meat_before = Product.objects.get(pk=self.meat.pk).quantity
        batch = cook_recipe(recipe=self.recipe, portions=10, user=self.user)
        self.assertEqual(batch.total_cost, preview['total_cost'])
        self.assertEqual(
            sum((i.line_cost for i in batch.items.all()), money(0)),
            batch.total_cost,
        )
        self.rice.refresh_from_db()
        self.meat.refresh_from_db()
        self.assertEqual(self.rice.quantity, qty(rice_before - Decimal('1.200')))
        self.assertEqual(self.meat.quantity, qty(meat_before - Decimal('0.800')))
        cancel_cook_batch(batch=batch, user=self.user)
        self.rice.refresh_from_db()
        self.meat.refresh_from_db()
        self.assertEqual(self.rice.quantity, rice_before)
        self.assertEqual(self.meat.quantity, meat_before)
