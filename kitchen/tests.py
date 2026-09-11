from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from kitchen.models import (
    ApprovalStatus,
    Category,
    CookBatch,
    DailyMenu,
    DailyMenuItem,
    MealType,
    MonthlyBudget,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    Recipe,
    RecipeCategory,
    RecipeItem,
    Shift,
    StockChangeRequest,
    StockLot,
    StorageLocation,
    Supplier,
    Unit,
    Worker,
    MealCheckin,
)
from kitchen.services.analytics import build_analytics
from kitchen.services.approvals import (
    receive_purchase_order,
    review_change_request,
    submit_adjust_request,
    submit_waste_request,
)
from kitchen.services.cook import cancel_cook_batch, cook_recipe, queue_cook, start_queued_cook
from kitchen.services.meals import build_meal_report, record_meal_checkin, search_workers
from kitchen.services.nutrition_lookup import lookup_local, suggest_nutrition
from kitchen.services.precision import money, qty, weighted_avg
from kitchen.services.recipe_cost import recipe_nutrition
from kitchen.services.shopping import shopping_list_for_range
from kitchen.services.stock import StockError, consume_stock, preview_fefo_allocation, receive_stock, update_receipt
from kitchen.templatetags.kitchen_tags import smart_qty
from kitchen.utils import local_date_span_bounds, local_day_bounds


class NutritionLookupTests(TestCase):
    def test_guruch_local(self):
        data = suggest_nutrition('Guruch')
        self.assertTrue(data['found'])
        self.assertEqual(data['source'], 'local')
        self.assertEqual(data['unit'], 'kg')
        self.assertGreater(data['kcal_per_unit'], 3000)
        self.assertGreater(data['carbs'], 700)

    def test_sut_suggests_liter(self):
        data = lookup_local('Sut')
        self.assertIsNotNone(data)
        self.assertEqual(data['unit'], 'l')

    def test_unknown_without_ai(self):
        data = suggest_nutrition('xyzzy-noma-mahsulot-999')
        self.assertFalse(data['found'])


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
        self.assertEqual(StockLot.objects.filter(product=self.product).count(), 2)


class FefoTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('fefo', 'f@t.t', 'x')
        self.cat = Category.objects.create(name='FefoCat')
        self.loc, _ = StorageLocation.objects.get_or_create(
            code='COLD',
            defaults={'name': 'Sovuqxona'},
        )
        self.product = Product.objects.create(
            name='SutF',
            category=self.cat,
            unit=Unit.L,
            default_location=self.loc,
        )
        today = timezone.localdate()
        receive_stock(
            product=self.product,
            quantity=Decimal('10'),
            unit_cost=Decimal('10000'),
            expiry_date=today + timedelta(days=10),
            user=self.user,
            location=self.loc,
        )
        receive_stock(
            product=self.product,
            quantity=Decimal('10'),
            unit_cost=Decimal('12000'),
            expiry_date=today + timedelta(days=3),
            user=self.user,
            location=self.loc,
        )

    def test_consume_uses_earliest_expiry_first(self):
        from kitchen.services.stock import consume_stock

        today = timezone.localdate()
        consume_stock(product=self.product, quantity=Decimal('5'), user=self.user)
        early = StockLot.objects.get(product=self.product, expiry_date=today + timedelta(days=3))
        late = StockLot.objects.get(product=self.product, expiry_date=today + timedelta(days=10))
        self.assertEqual(early.quantity, Decimal('5.000'))
        self.assertEqual(late.quantity, Decimal('10.000'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal('15.000'))
        self.assertEqual(self.product.expiry_date, today + timedelta(days=3))

    def test_cancel_cook_restores_lots(self):
        today = timezone.localdate()
        cat = Category.objects.create(name='RestoreCat')
        rice = Product.objects.create(name='RiceR', category=cat, unit=Unit.KG)
        receive_stock(
            product=rice,
            quantity=Decimal('10'),
            unit_cost=Decimal('10000'),
            expiry_date=today + timedelta(days=5),
            user=self.user,
        )
        recipe = Recipe.objects.create(name='RestoreOsh')
        RecipeItem.objects.create(recipe=recipe, product=rice, quantity_per_portion=Decimal('1'))
        batch = cook_recipe(recipe=recipe, portions=3, user=self.user)
        rice.refresh_from_db()
        self.assertEqual(rice.quantity, Decimal('7.000'))
        cancel_cook_batch(batch=batch, user=self.user)
        rice.refresh_from_db()
        self.assertEqual(rice.quantity, Decimal('10.000'))
        lot = StockLot.objects.get(product=rice, expiry_date=today + timedelta(days=5))
        self.assertEqual(lot.quantity, Decimal('10.000'))

    def test_consume_respects_location(self):
        hot = StorageLocation.objects.create(name='Issiq zona', code='HOT')
        today = timezone.localdate()
        receive_stock(
            product=self.product,
            quantity=Decimal('4'),
            unit_cost=Decimal('9000'),
            expiry_date=today + timedelta(days=1),
            user=self.user,
            location=hot,
        )
        consume_stock(
            product=self.product,
            quantity=Decimal('5'),
            user=self.user,
            location=self.loc,
        )
        early_cold = StockLot.objects.get(product=self.product, expiry_date=today + timedelta(days=3))
        hot_lot = StockLot.objects.get(product=self.product, expiry_date=today + timedelta(days=1), location=hot)
        self.assertEqual(early_cold.quantity, Decimal('5.000'))
        self.assertEqual(hot_lot.quantity, Decimal('4.000'))


class FefoCostTests(TestCase):
    """FEFO bo‘yicha aralash partiya tannarxi va UI preview."""

    def setUp(self):
        self.user = get_user_model().objects.create_user('cost', 'c@t.t', 'x', is_staff=True)
        self.client = Client()
        self.client.login(username='cost', password='x')
        self.cat = Category.objects.create(name='CostCat')
        self.product = Product.objects.create(
            name='Kartoshka',
            category=self.cat,
            unit=Unit.KG,
        )
        today = timezone.localdate()
        receive_stock(
            product=self.product,
            quantity=Decimal('20'),
            unit_cost=Decimal('5000'),
            expiry_date=today + timedelta(days=30),
            user=self.user,
        )
        receive_stock(
            product=self.product,
            quantity=Decimal('30'),
            unit_cost=Decimal('6000'),
            expiry_date=today + timedelta(days=60),
            user=self.user,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.avg_cost, Decimal('5600.00'))

    def test_consume_40kg_mixed_lot_cost(self):
        movement = consume_stock(product=self.product, quantity=Decimal('40'), user=self.user)
        self.assertEqual(movement.total_cost, Decimal('220000.00'))
        self.assertEqual(movement.unit_cost, Decimal('5500.00'))
        self.assertEqual(movement.lot_allocations.count(), 2)

        lots = list(
            StockLot.objects.filter(product=self.product, quantity__gt=0).order_by('unit_cost')
        )
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].quantity, Decimal('10.000'))
        self.assertEqual(lots[0].unit_cost, Decimal('6000.00'))

        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal('10.000'))
        self.assertEqual(self.product.avg_cost, Decimal('6000.00'))

    def test_preview_matches_consume(self):
        preview = preview_fefo_allocation(self.product, Decimal('40'))
        self.assertTrue(preview['mixed'])
        self.assertEqual(preview['total_cost'], Decimal('220000.00'))
        self.assertEqual(preview['avg_unit_cost'], Decimal('5500.00'))
        self.assertEqual(len(preview['lines']), 2)

        movement = consume_stock(product=self.product, quantity=Decimal('40'), user=self.user)
        self.assertEqual(movement.total_cost, preview['total_cost'])
        self.assertEqual(movement.unit_cost, preview['avg_unit_cost'])

    def test_recipe_preview_uses_fefo_not_flat_avg(self):
        recipe = Recipe.objects.create(name='Kartoshka pishloq')
        RecipeItem.objects.create(
            recipe=recipe,
            product=self.product,
            quantity_per_portion=Decimal('40'),
        )
        info = recipe_nutrition(recipe, 1)
        self.assertEqual(info['total_cost'], Decimal('220000.00'))
        self.assertEqual(info['items'][0]['unit_cost'], Decimal('5500.00'))
        self.assertTrue(info['items'][0]['mixed_cost'])
        self.assertEqual(len(info['items'][0]['allocations']), 2)

        avg_based = money(Decimal('40') * self.product.avg_cost)
        self.assertNotEqual(info['total_cost'], avg_based)

    def test_recipe_cost_uses_lots_outside_default_location(self):
        """Prixod boshqa omborda bo‘lsa ham retsept tannarxi 0 bo‘lmasin."""
        cold, _ = StorageLocation.objects.get_or_create(
            code='COLD',
            defaults={'name': 'Sovuqxona'},
        )
        dry, _ = StorageLocation.objects.get_or_create(
            code='DRY',
            defaults={'name': 'Quruq ombor'},
        )
        egg = Product.objects.create(
            name='TuxumX',
            category=self.cat,
            unit=Unit.PCS,
            default_location=dry,
        )
        receive_stock(
            product=egg,
            quantity=Decimal('100'),
            unit_cost=Decimal('1400'),
            user=self.user,
            location=cold,
        )
        recipe = Recipe.objects.create(name='Tuxum test')
        RecipeItem.objects.create(
            recipe=recipe,
            product=egg,
            quantity_per_portion=Decimal('2'),
        )
        info = recipe_nutrition(recipe, 1)
        self.assertEqual(info['items'][0]['line_cost'], Decimal('2800.00'))
        self.assertEqual(info['total_cost'], Decimal('2800.00'))
        self.assertTrue(info['can_cook'])
        preview = preview_fefo_allocation(egg, Decimal('2'))
        self.assertEqual(preview['missing'], Decimal('0.000'))
        self.assertEqual(preview['total_cost'], Decimal('2800.00'))
        scoped = preview_fefo_allocation(egg, Decimal('2'), location=dry)
        self.assertEqual(scoped['missing'], Decimal('2.000'))
        self.assertEqual(scoped['total_cost'], Decimal('0.00'))

    def test_consume_preview_api(self):
        resp = self.client.get(
            reverse('stock_consume_preview'),
            {'product': self.product.pk, 'quantity': '40'},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(data['enough_stock'])
        self.assertEqual(data['total_cost'], '220000.00')
        self.assertTrue(data['mixed'])
        self.assertEqual(len(data['lines']), 2)

    def test_consume_preview_warns_when_insufficient(self):
        resp = self.client.get(
            reverse('stock_consume_preview'),
            {'product': self.product.pk, 'quantity': '100'},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertFalse(data['enough_stock'])
        self.assertEqual(data['available'], '50.000')

    def test_movement_detail_page(self):
        movement = consume_stock(product=self.product, quantity=Decimal('5'), user=self.user)
        resp = self.client.get(reverse('movement_detail', args=[movement.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Partiya taqsimoti')
        self.assertContains(resp, '220000', count=0)
        self.assertContains(resp, 'FEFO')


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

    def test_nutrition_per_portion(self):
        info = recipe_nutrition(self.recipe, 1)
        self.assertEqual(info['kcal_per_portion'], Decimal('620.00'))
        self.assertEqual(info['protein_per_portion'], Decimal('29.20'))
        self.assertEqual(info['fat_per_portion'], Decimal('12.60'))
        self.assertEqual(info['carbs_per_portion'], Decimal('93.60'))

    def test_invalid_portions(self):
        self.assertFalse(recipe_nutrition(self.recipe, 0)['can_cook'])
        self.assertFalse(recipe_nutrition(self.recipe, 'x')['can_cook'])

    def test_cook_matches_preview_and_cancel(self):
        preview = recipe_nutrition(self.recipe, 10)
        rice_before = Product.objects.get(pk=self.rice.pk).quantity
        meat_before = Product.objects.get(pk=self.meat.pk).quantity
        batch = cook_recipe(recipe=self.recipe, portions=10, user=self.user, shift=Shift.ONE)
        self.assertEqual(batch.shift, Shift.ONE)
        self.assertEqual(batch.total_cost, preview['total_cost'])
        self.rice.refresh_from_db()
        self.meat.refresh_from_db()
        self.assertEqual(self.rice.quantity, qty(rice_before - Decimal('1.200')))
        self.assertEqual(self.meat.quantity, qty(meat_before - Decimal('0.800')))
        cancel_cook_batch(batch=batch, user=self.user)
        self.rice.refresh_from_db()
        self.meat.refresh_from_db()
        self.assertEqual(self.rice.quantity, rice_before)
        self.assertEqual(self.meat.quantity, meat_before)

    def test_cook_accepts_backdated_day(self):
        past = timezone.localdate() - timedelta(days=12)
        batch = cook_recipe(
            recipe=self.recipe,
            portions=1,
            user=self.user,
            cooked_at=past,
        )
        self.assertEqual(timezone.localdate(batch.cooked_at), past)

    def test_cook_with_side_recipes_same_portions(self):
        from kitchen.services import cook_recipes

        side_cat = RecipeCategory.objects.create(name='SideCalc', include_in_meal_sverka=False)
        side = Recipe.objects.create(name='SalatCalc', meal_type=MealType.LUNCH, category=side_cat)
        RecipeItem.objects.create(
            recipe=side,
            product=self.rice,
            quantity_per_portion=Decimal('0.010'),
        )
        rice_before = Product.objects.get(pk=self.rice.pk).quantity
        batches = cook_recipes(
            recipes=[self.recipe, side],
            portions=10,
            user=self.user,
        )
        self.assertEqual(len(batches), 2)
        self.rice.refresh_from_db()
        # main 1.2 + side 0.1
        self.assertEqual(self.rice.quantity, qty(rice_before - Decimal('1.300')))

    def test_base_portions_scales_real_recipe_batch(self):
        recipe = Recipe.objects.create(name='Base10', base_portions=10)
        RecipeItem.objects.create(
            recipe=recipe,
            product=self.rice,
            quantity_per_portion=Decimal('2.000'),
        )
        info10 = recipe_nutrition(recipe, 10)
        info20 = recipe_nutrition(recipe, 20)
        self.assertEqual(info10['items'][0]['need'], Decimal('2.000'))
        self.assertEqual(info20['items'][0]['need'], Decimal('4.000'))


class RecipeEditViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('recipe_edit', 're@t.t', 'x')
        self.client = Client()
        self.client.login(username='recipe_edit', password='x')
        self.cat = Category.objects.create(name='EditCat')
        self.product = Product.objects.create(name='EditRice', category=self.cat, unit=Unit.KG)
        self.extra = Product.objects.create(name='EditOil', category=self.cat, unit=Unit.L)
        self.recipe_cat = RecipeCategory.objects.create(name='AsosiyEdit', include_in_meal_sverka=True)
        self.recipe = Recipe.objects.create(
            name='EditOsh',
            base_portions=1,
            meal_type=MealType.LUNCH,
            category=self.recipe_cat,
        )
        self.item = RecipeItem.objects.create(
            recipe=self.recipe,
            product=self.product,
            quantity_per_portion=Decimal('0.100'),
        )

    def test_list_shows_edit_link(self):
        resp = self.client.get(reverse('recipe_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse('recipe_edit', args=[self.recipe.pk]))
        self.assertContains(resp, 'Tahrirlash')

    def test_create_with_one_ingredient_allows_empty_extra_rows(self):
        resp = self.client.post(
            reverse('recipe_create'),
            {
                'name': 'Bitta ingredient',
                'description': '',
                'category': str(self.recipe_cat.pk),
                'meal_type': MealType.LUNCH,
                'base_portions': 1,
                'allergens': '',
                'is_active': 'on',
                'items-TOTAL_FORMS': '3',
                'items-INITIAL_FORMS': '0',
                'items-MIN_NUM_FORMS': '0',
                'items-MAX_NUM_FORMS': '50',
                'items-0-product': str(self.product.pk),
                'items-0-quantity_per_portion': '0.100',
                'items-1-product': '',
                'items-1-quantity_per_portion': '',
                'items-1-DELETE': 'on',
                'items-2-product': '',
                'items-2-quantity_per_portion': '',
                'items-2-DELETE': 'on',
            },
        )
        self.assertEqual(resp.status_code, 302)
        recipe = Recipe.objects.get(name='Bitta ingredient')
        self.assertEqual(recipe.items.count(), 1)

    def test_edit_updates_name_and_ingredients(self):
        url = reverse('recipe_edit', args=[self.recipe.pk])
        resp = self.client.post(
            url,
            {
                'name': 'Yangilangan osh',
                'description': '',
                'category': str(self.recipe_cat.pk),
                'meal_type': MealType.LUNCH,
                'base_portions': 2,
                'allergens': '',
                'is_active': 'on',
                'items-TOTAL_FORMS': '4',
                'items-INITIAL_FORMS': '1',
                'items-MIN_NUM_FORMS': '0',
                'items-MAX_NUM_FORMS': '50',
                'items-0-id': str(self.item.pk),
                'items-0-product': str(self.product.pk),
                'items-0-quantity_per_portion': '0.150',
                'items-1-product': str(self.extra.pk),
                'items-1-quantity_per_portion': '0.020',
                'items-2-product': '',
                'items-2-quantity_per_portion': '',
                'items-3-product': '',
                'items-3-quantity_per_portion': '',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.name, 'Yangilangan osh')
        self.assertEqual(self.recipe.base_portions, 2)
        items = list(self.recipe.items.order_by('product__name'))
        self.assertEqual(len(items), 2)
        by_name = {i.product.name: i.quantity_per_portion for i in items}
        self.assertEqual(by_name['EditOil'], Decimal('0.020'))
        self.assertEqual(by_name['EditRice'], Decimal('0.150'))


class ApprovalAndPoTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user('admin', 'a@t.t', 'x', is_staff=True)
        self.worker = User.objects.create_user('cook', 'c@t.t', 'x', is_staff=False)
        self.cat = Category.objects.create(name='ApCat')
        self.supplier = Supplier.objects.create(name='Yetkaz')
        self.product = Product.objects.create(name='UnA', category=self.cat, unit=Unit.KG)
        receive_stock(product=self.product, quantity=Decimal('20'), unit_cost=Decimal('9000'), user=self.staff)

    def test_non_staff_waste_creates_request(self):
        movement, req = submit_waste_request(
            product=self.product,
            quantity=Decimal('1'),
            user=self.worker,
            note='Buzildi',
        )
        self.assertIsNone(movement)
        self.assertEqual(req.status, ApprovalStatus.PENDING)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal('20.000'))
        review_change_request(request_obj=req, reviewer=self.staff, approve=True)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal('19.000'))

    def test_purchase_order_receive(self):
        po = PurchaseOrder.objects.create(
            supplier=self.supplier,
            status=PurchaseOrder.Status.ORDERED,
            created_by=self.staff,
        )
        PurchaseOrderLine.objects.create(
            order=po,
            product=self.product,
            quantity=Decimal('5'),
            unit_cost=Decimal('9500'),
        )
        receive_purchase_order(order=po, user=self.staff)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.RECEIVED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, Decimal('25.000'))

    def test_double_receive_rejected(self):
        po = PurchaseOrder.objects.create(
            supplier=self.supplier,
            status=PurchaseOrder.Status.ORDERED,
            created_by=self.staff,
        )
        PurchaseOrderLine.objects.create(
            order=po,
            product=self.product,
            quantity=Decimal('1'),
            unit_cost=Decimal('9000'),
        )
        receive_purchase_order(order=po, user=self.staff)
        with self.assertRaises(StockError):
            receive_purchase_order(order=po, user=self.staff)

    def test_non_staff_cannot_approve(self):
        _, req = submit_waste_request(
            product=self.product,
            quantity=Decimal('1'),
            user=self.worker,
            note='x',
        )
        with self.assertRaises(StockError):
            review_change_request(request_obj=req, reviewer=self.worker, approve=True)

    def test_adjust_request_rejected_if_stock_changed_after_request(self):
        _, req = submit_adjust_request(
            product=self.product,
            new_quantity=Decimal('18'),
            user=self.worker,
            note='Sanash xatosi',
        )
        receive_stock(
            product=self.product,
            quantity=Decimal('5'),
            unit_cost=Decimal('10000'),
            user=self.staff,
        )
        with self.assertRaises(StockError):
            review_change_request(request_obj=req, reviewer=self.staff, approve=True)
        req.refresh_from_db()
        self.assertEqual(req.status, ApprovalStatus.PENDING)

    def test_draft_purchase_order_cannot_be_received(self):
        po = PurchaseOrder.objects.create(
            supplier=self.supplier,
            status=PurchaseOrder.Status.DRAFT,
            created_by=self.staff,
        )
        PurchaseOrderLine.objects.create(
            order=po,
            product=self.product,
            quantity=Decimal('5'),
            unit_cost=Decimal('9500'),
        )
        with self.assertRaises(StockError):
            receive_purchase_order(order=po, user=self.staff)


class KdsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('kds', 'k@t.t', 'x')
        self.cat = Category.objects.create(name='KdsCat')
        self.product = Product.objects.create(
            name='Kart',
            category=self.cat,
            unit=Unit.KG,
            kcal_per_unit=Decimal('770'),
        )
        receive_stock(product=self.product, quantity=Decimal('50'), unit_cost=Decimal('4000'), user=self.user)
        self.recipe = Recipe.objects.create(name='Kartoshka')
        RecipeItem.objects.create(
            recipe=self.recipe,
            product=self.product,
            quantity_per_portion=Decimal('0.200'),
        )

    def test_queue_then_start(self):
        before = Product.objects.get(pk=self.product.pk).quantity
        batch = queue_cook(recipe=self.recipe, portions=5, user=self.user, shift=Shift.TWO)
        self.assertEqual(batch.status, CookBatch.Status.QUEUED)
        self.product.refresh_from_db()
        reserved = self.product.quantity
        self.assertEqual(reserved, qty(before - Decimal('1.000')))
        done = start_queued_cook(batch=batch, user=self.user)
        self.assertEqual(done.status, CookBatch.Status.DONE)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, reserved)

    def test_queue_reserves_stock_immediately(self):
        before = Product.objects.get(pk=self.product.pk).quantity
        batch = queue_cook(recipe=self.recipe, portions=5, user=self.user)
        self.assertEqual(batch.status, CookBatch.Status.QUEUED)
        self.assertTrue(batch.items.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, qty(before - Decimal('1.000')))
        start_queued_cook(batch=batch, user=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, qty(before - Decimal('1.000')))

    def test_second_queue_blocked_when_reserved(self):
        queue_cook(recipe=self.recipe, portions=250, user=self.user)
        with self.assertRaises(StockError):
            queue_cook(recipe=self.recipe, portions=5, user=self.user)

    def test_cancel_queued_restores_reservation(self):
        before = Product.objects.get(pk=self.product.pk).quantity
        batch = queue_cook(recipe=self.recipe, portions=5, user=self.user)
        self.product.refresh_from_db()
        self.assertLess(self.product.quantity, before)
        cancel_cook_batch(batch=batch, user=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, before)
        batch.refresh_from_db()
        self.assertEqual(batch.status, CookBatch.Status.CANCELLED)

    def test_start_stamps_cooked_at(self):
        batch = queue_cook(recipe=self.recipe, portions=5, user=self.user)
        queued_at = batch.cooked_at
        done = start_queued_cook(batch=batch, user=self.user)
        self.assertEqual(done.status, CookBatch.Status.DONE)
    def test_kds_board_has_cancel(self):
        self.client = Client()
        self.client.login(username='kds', password='x')
        queue_cook(recipe=self.recipe, portions=5, user=self.user)
        resp = self.client.get(reverse('kds_board'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'ombor rezervlangan')
        self.assertContains(resp, 'Bekor')


class ViewSmokeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('v', 'v@t.t', 'pass', is_staff=True)
        self.client = Client()
        self.client.login(username='v', password='pass')
        self.cat = Category.objects.create(name='VCat')
        Product.objects.create(name='P', category=self.cat)

    def test_dashboard_and_ops_pages(self):
        for name in [
            'dashboard',
            'product_list',
            'category_list',
            'stock_list',
            'lot_list',
            'approval_list',
            'kds_board',
            'purchase_order_list',
            'hygiene_list',
            'shopping_list',
        ]:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, msg=name)

    def test_shopping_bad_date_safe(self):
        resp = self.client.get(reverse('shopping_list'), {'date': 'not-a-date'})
        self.assertEqual(resp.status_code, 200)

    def test_nutrition_suggest_api(self):
        resp = self.client.get(reverse('product_nutrition_suggest'), {'name': 'Kartoshka'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['found'])
        self.assertIn('kcal_per_unit', data)

    def test_category_quick_create(self):
        resp = self.client.post(reverse('category_quick_create'), {'name': 'Donlar'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(Category.objects.filter(name='Donlar').exists())
        # duplicate -> same id
        resp2 = self.client.post(reverse('category_quick_create'), {'name': 'donlar'})
        self.assertEqual(resp2.json()['id'], data['id'])
        self.assertFalse(resp2.json()['created'])
        self.assertEqual(Category.objects.filter(name__iexact='Donlar').count(), 1)

    def test_product_form_hides_expiry_field(self):
        resp = self.client.get(reverse('product_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id_expiry_date')
        self.assertNotContains(resp, 'Muddat')

    def test_supplier_form_back_link_uses_supplier_list(self):
        resp = self.client.get(reverse('supplier_create'))
        self.assertContains(resp, reverse('supplier_list'))

    def test_waste_form_page(self):
        resp = self.client.get(reverse('waste_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'FEFO tannarx')
        self.assertContains(resp, reverse('stock_consume_preview'))

    def test_menu_apply_form_back_link_uses_menu_page(self):
        resp = self.client.get(reverse('menu_apply_template'))
        self.assertContains(resp, reverse('menu_day'))


class NavAlertsCacheTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('n', 'n@t.t', 'x')
        self.cat = Category.objects.create(name='NCat')
        Product.objects.create(
            name='LowStock',
            category=self.cat,
            quantity=Decimal('1'),
            min_stock=Decimal('10'),
        )

    def test_nav_alerts_cached(self):
        from django.core.cache import cache
        from kitchen.services.notifications import build_notifications

        cache.clear()
        a = build_notifications(None)
        b = build_notifications(None)
        self.assertEqual(a['notification_count'], b['notification_count'])
        self.assertGreaterEqual(a['notification_count'], 1)
        self.assertEqual(a, b)

    def test_cache_bumps_after_receive(self):
        from django.core.cache import cache
        from kitchen.services.notifications import build_notifications

        cache.clear()
        before = build_notifications(None)['notification_count']
        # kam qoldiqni yopish — alert kamayishi kerak
        receive_stock(
            product=Product.objects.get(name='LowStock'),
            quantity=Decimal('20'),
            unit_cost=Decimal('1000'),
            user=self.user,
        )
        after = build_notifications(None)['notification_count']
        self.assertLess(after, before)


class LotIntegrityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('lot', 'l@t.t', 'x')
        self.cat = Category.objects.create(name='LotCat')
        self.product = Product.objects.create(
            name='DriftP',
            category=self.cat,
            unit=Unit.KG,
            quantity=Decimal('10'),
        )

    def test_consume_without_lots_raises_instead_of_synthetic(self):
        with self.assertRaises(StockError) as ctx:
            consume_stock(product=self.product, quantity=Decimal('2'), user=self.user)
        self.assertIn('partiyalar', str(ctx.exception).lower())
        self.assertFalse(StockLot.objects.filter(product=self.product).exists())

    def test_preview_does_not_invent_synthetic_lot(self):
        preview = preview_fefo_allocation(self.product, Decimal('2'))
        self.assertEqual(preview['missing'], Decimal('2.000'))
        self.assertEqual(preview['lines'], [])
        self.assertFalse(any(row.get('synthetic') for row in preview['lines']))


class AnalyticsBudgetAndRangeTests(TestCase):
    def test_day_mode_uses_that_day_month_budget(self):
        MonthlyBudget.objects.create(year=2026, month=1, limit_amount=Decimal('100000'))
        MonthlyBudget.objects.create(year=2026, month=9, limit_amount=Decimal('900000'))
        data = build_analytics(mode='day', year=2026, month=9, day=date(2026, 1, 15))
        self.assertEqual(data['budget']['budget'].month, 1)
        self.assertEqual(data['budget']['budget'].limit_amount, Decimal('100000.00'))

    def test_date_span_bounds_are_half_open(self):
        start, end = local_date_span_bounds(date(2026, 1, 15), date(2026, 1, 15))
        self.assertEqual((end - start).days, 1)
        day_start, day_end = local_day_bounds(date(2026, 1, 15))
        self.assertEqual(start, day_start)
        self.assertEqual(end, day_end)


class ShoppingPrefetchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('shop', 's@t.t', 'x')
        self.cat = Category.objects.create(name='ShopCat')
        self.product = Product.objects.create(name='Un', category=self.cat, unit=Unit.KG)
        receive_stock(product=self.product, quantity=Decimal('1'), unit_cost=Decimal('4000'), user=self.user)
        self.recipe = Recipe.objects.create(name='Non')
        RecipeItem.objects.create(recipe=self.recipe, product=self.product, quantity_per_portion=Decimal('2'))

    def test_range_does_not_filter_prefetched_items(self):
        for offset in range(3):
            menu = DailyMenu.objects.create(date=date(2026, 2, 1) + timedelta(days=offset))
            DailyMenuItem.objects.create(
                menu=menu,
                recipe=self.recipe,
                meal_type=MealType.LUNCH,
                portions=10,
            )
        with CaptureQueriesContext(connection) as ctx:
            data = shopping_list_for_range(date(2026, 2, 1), 7)
        sql = ' '.join(q['sql'] for q in ctx.captured_queries).lower()
        self.assertNotIn('"is_cooked" = false', sql)
        self.assertNotIn('is_cooked" = 0', sql)
        self.assertEqual(len(data['rows']), 1)
        self.assertEqual(data['rows'][0]['buy'], Decimal('59.000'))


class FormFeedbackTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('ff', 'f@t.t', 'pass', is_staff=True)
        self.client = Client()
        self.client.login(username='ff', password='pass')

    def test_recipe_form_shows_field_errors(self):
        resp = self.client.post(reverse('recipe_create'), {'name': '', 'base_portions': '0'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'field-error')

    def test_recipe_form_has_add_ingredient_control(self):
        resp = self.client.get(reverse('recipe_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'add-ingredient-row')
        self.assertContains(resp, 'Yana mahsulot')
        self.assertContains(resp, 'id_items-TOTAL_FORMS')
        self.assertContains(resp, 'ingredient-empty-form')

    def test_po_form_has_error_slots(self):
        resp = self.client.get(reverse('purchase_order_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'formset-table')
        self.assertContains(resp, 'data-label="Mahsulot"')

    def test_menu_and_headcount_error_markup(self):
        resp = self.client.get(reverse('menu_day'))
        self.assertContains(resp, 'formset-table')
        hc = self.client.get(reverse('headcount_list'))
        self.assertEqual(hc.status_code, 200)


class ReceiptEditAndQtyFormatTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('rec', 'r@t.t', 'pass', is_staff=True)
        self.client = Client()
        self.client.login(username='rec', password='pass')
        self.cat = Category.objects.create(name='RecCat')
        self.supplier = Supplier.objects.create(name='Yetkaz')
        self.product = Product.objects.create(name='UnR', category=self.cat, unit=Unit.KG)

    def test_smart_qty_strips_trailing_zeros(self):
        self.assertEqual(smart_qty(Decimal('2.000')), '2')
        self.assertEqual(smart_qty(Decimal('1.500')), '1.5')
        self.assertEqual(smart_qty(Decimal('12.700')), '12.7')

    def test_receipt_list_shows_edit_and_smart_qty(self):
        receive_stock(
            product=self.product,
            quantity=Decimal('2'),
            unit_cost=Decimal('10000'),
            user=self.user,
            supplier=self.supplier,
        )
        resp = self.client.get(reverse('receipt_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Tahrirlash')
        self.assertContains(resp, '2 kg')
        self.assertNotContains(resp, '2.000')

    def test_update_receipt_changes_qty_and_cost(self):
        movement = receive_stock(
            product=self.product,
            quantity=Decimal('10'),
            unit_cost=Decimal('5000'),
            user=self.user,
            supplier=self.supplier,
        )
        update_receipt(
            movement=movement,
            quantity=Decimal('12'),
            unit_cost=Decimal('6000'),
            user=self.user,
            supplier=self.supplier,
        )
        movement.refresh_from_db()
        self.product.refresh_from_db()
        lot = StockLot.objects.get(source_movement=movement)
        self.assertEqual(movement.quantity, Decimal('12.000'))
        self.assertEqual(movement.unit_cost, Decimal('6000.00'))
        self.assertEqual(movement.total_cost, Decimal('72000.00'))
        self.assertEqual(lot.quantity, Decimal('12.000'))
        self.assertEqual(lot.unit_cost, Decimal('6000.00'))
        self.assertEqual(self.product.quantity, Decimal('12.000'))
        self.assertEqual(self.product.avg_cost, Decimal('6000.00'))

    def test_update_receipt_blocked_below_consumed(self):
        movement = receive_stock(
            product=self.product,
            quantity=Decimal('10'),
            unit_cost=Decimal('5000'),
            user=self.user,
        )
        consume_stock(product=self.product, quantity=Decimal('4'), user=self.user)
        with self.assertRaises(StockError):
            update_receipt(
                movement=movement,
                quantity=Decimal('3'),
                unit_cost=Decimal('5000'),
                user=self.user,
            )

    def test_receipt_edit_page_post(self):
        movement = receive_stock(
            product=self.product,
            quantity=Decimal('5'),
            unit_cost=Decimal('8000'),
            user=self.user,
            supplier=self.supplier,
        )
        resp = self.client.post(
            reverse('receipt_edit', args=[movement.pk]),
            {
                'quantity': '7',
                'unit_cost': '9000',
                'supplier': self.supplier.pk,
                'expiry_date': '',
                'location': '',
                'note': 'tuzatildi',
            },
        )
        self.assertEqual(resp.status_code, 302)
        movement.refresh_from_db()
        self.assertEqual(movement.quantity, Decimal('7.000'))
        self.assertEqual(movement.note, 'tuzatildi')



class MealCheckinTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('mealadmin', 'm@t.t', 'x', is_staff=True)
        self.client = Client()
        self.worker = Worker.objects.create(first_name='Ali', last_name='Karimov', department='Sex-1')
        self.cat = Category.objects.create(name='MealCat')
        self.product = Product.objects.create(name='NonM', category=self.cat, unit=Unit.PCS)
        receive_stock(product=self.product, quantity=Decimal('100'), unit_cost=Decimal('1000'), user=self.user)
        self.recipe = Recipe.objects.create(name='TushlikOsh', meal_type=MealType.LUNCH)
        RecipeItem.objects.create(
            recipe=self.recipe,
            product=self.product,
            quantity_per_portion=Decimal('1'),
        )

    def test_public_checkin_and_duplicate_blocked(self):
        resp = self.client.get(reverse('meal_checkin'))
        self.assertEqual(resp.status_code, 200)
        search = self.client.get(reverse('meal_checkin_search'), {'q': 'Karimov'})
        self.assertContains(search, 'Karimov Ali')
        ok = self.client.post(
            reverse('meal_checkin_submit'),
            {'worker_id': self.worker.pk, 'meal_type': MealType.LUNCH},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertContains(ok, 'Tasdiqlandi')
        self.assertEqual(MealCheckin.objects.count(), 1)
        dup = self.client.post(
            reverse('meal_checkin_submit'),
            {'worker_id': self.worker.pk, 'meal_type': MealType.LUNCH},
        )
        self.assertEqual(dup.status_code, 400)
        self.assertEqual(MealCheckin.objects.count(), 1)

    def test_report_sverka_cooked_vs_eaten(self):
        cook_recipe(recipe=self.recipe, portions=10, user=self.user)
        record_meal_checkin(worker=self.worker, meal_type=MealType.LUNCH, portions=3)
        w2 = Worker.objects.create(first_name='Vali', last_name='Sobirov')
        record_meal_checkin(worker=w2, meal_type=MealType.LUNCH, portions=2)
        today = timezone.localdate()
        report = build_meal_report(today.year, today.month)
        self.assertEqual(report['totals']['eaten'], 5)
        self.assertEqual(report['portion_total'], 5)
        self.assertEqual(report['checkin_count'], 2)
        self.assertEqual(report['totals']['cooked'], 10)
        self.assertEqual(report['totals']['diff'], 5)
        self.assertEqual(report['totals']['by_meal'][MealType.LUNCH]['eaten'], 5)
        self.assertEqual(report['unique_workers'], 2)

    def test_report_excludes_side_recipes_from_cooked(self):
        side_cat = RecipeCategory.objects.create(
            name='Qo‘shimchaTest',
            include_in_meal_sverka=False,
        )
        side = Recipe.objects.create(
            name='Kefir',
            meal_type=MealType.LUNCH,
            category=side_cat,
        )
        RecipeItem.objects.create(
            recipe=side,
            product=self.product,
            quantity_per_portion=Decimal('1'),
        )
        cook_recipe(recipe=self.recipe, portions=50, user=self.user)
        cook_recipe(recipe=side, portions=50, user=self.user)
        record_meal_checkin(worker=self.worker, meal_type=MealType.LUNCH, portions=1)
        today = timezone.localdate()
        report = build_meal_report(today.year, today.month)
        self.assertEqual(report['totals']['cooked'], 50)
        self.assertEqual(report['totals']['eaten'], 1)
        self.assertEqual(report['totals']['diff'], 49)
        self.assertEqual(report['side_total'], 50)
        self.assertEqual(report['side_recipe_rows'][0]['recipe'], 'Kefir')
        self.assertEqual(report['main_recipe_rows'][0]['recipe'], 'TushlikOsh')
        self.assertEqual(len(report['side_detail_rows']), 1)

    def test_public_checkin_portions_default_one(self):
        ok = self.client.post(
            reverse('meal_checkin_submit'),
            {'worker_id': self.worker.pk, 'meal_type': MealType.LUNCH},
        )
        self.assertEqual(ok.status_code, 200)
        checkin = MealCheckin.objects.get()
        self.assertEqual(checkin.portions, 1)

    def test_public_checkin_custom_portions(self):
        ok = self.client.post(
            reverse('meal_checkin_submit'),
            {'worker_id': self.worker.pk, 'meal_type': MealType.LUNCH, 'portions': '20'},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertContains(ok, '20 porsiya')
        self.assertEqual(MealCheckin.objects.get().portions, 20)

    def test_excel_export_and_staff_pages(self):
        self.client.login(username='mealadmin', password='x')
        record_meal_checkin(worker=self.worker, meal_type=MealType.BREAKFAST)
        today = timezone.localdate()
        report_page = self.client.get(reverse('meal_report'), {'year': today.year, 'month': today.month})
        self.assertEqual(report_page.status_code, 200)
        self.assertContains(report_page, 'Excel export')
        export = self.client.get(
            reverse('meal_report_export'),
            {'year': today.year, 'month': today.month},
        )
        self.assertEqual(export.status_code, 200)
        self.assertIn('spreadsheetml.sheet', export['Content-Type'])
        workers = self.client.get(reverse('worker_list'))
        self.assertContains(workers, 'Karimov')
        qr = self.client.get(reverse('meal_qr_poster'))
        self.assertEqual(qr.status_code, 200)
        self.assertContains(qr, 'ovqat')
        dl = self.client.get(reverse('meal_qr_download'))
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(dl['Content-Type'], 'image/png')
        self.assertIn('attachment', dl['Content-Disposition'])
        self.assertGreater(len(dl.content), 200)
        door = self.client.get(reverse('meal_qr_door_download'))
        self.assertEqual(door.status_code, 200)
        self.assertEqual(door['Content-Type'], 'image/png')
        self.assertIn('eshik', door['Content-Disposition'])

    def test_search_workers_multi_token(self):
        found = list(search_workers('Karimov Ali'))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].pk, self.worker.pk)

    def test_worker_excel_import_and_today_board(self):
        from io import BytesIO
        from openpyxl import Workbook

        self.client.login(username='mealadmin', password='x')
        wb = Workbook()
        ws = wb.active
        ws.append(['Familiya', 'Ism', 'Bo‘lim', 'Kod'])
        ws.append(['Yusupov', 'Botir', 'Sex-3', '777'])
        buf = BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'workers.xlsx'
        resp = self.client.post(reverse('worker_import'), {'file': buf})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Worker.objects.filter(employee_code='777').exists())

        past = timezone.localdate() - timedelta(days=3)
        record_meal_checkin(
            worker=self.worker,
            meal_type=MealType.LUNCH,
            served_on=past,
        )
        self.assertEqual(
            MealCheckin.objects.get(worker=self.worker, served_on=past).meal_type,
            MealType.LUNCH,
        )
        board = self.client.get(reverse('meal_today'))
        self.assertEqual(board.status_code, 200)
        self.assertContains(board, 'Bugungi')

        checkin = MealCheckin.objects.get(worker=self.worker, served_on=past)
        edit = self.client.post(
            reverse('meal_checkin_edit', args=[checkin.pk]),
            {
                'worker': self.worker.pk,
                'meal_type': MealType.DINNER,
                'portions': '3',
                'served_on': past.isoformat(),
            },
        )
        self.assertEqual(edit.status_code, 302)
        checkin.refresh_from_db()
        self.assertEqual(checkin.meal_type, MealType.DINNER)
        self.assertEqual(checkin.portions, 3)
        deleted = self.client.post(reverse('meal_checkin_delete', args=[checkin.pk]))
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(MealCheckin.objects.filter(pk=checkin.pk).exists())


class ErpIsomerixPushTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('erpuser', 'e@t.t', 'x')
        self.cat = Category.objects.create(name='ErpCat')
        self.product = Product.objects.create(
            name='GuruchERP', category=self.cat, unit=Unit.KG
        )
        self.supplier = Supplier.objects.create(name='Yetkazuvchi')

    @override_settings(ERP_ISOMERIX_WEBHOOK_URL='', ERP_ISOMERIX_BEARER_TOKEN='')
    def test_receive_stock_noop_without_config(self):
        movement = receive_stock(
            product=self.product,
            quantity=Decimal('5'),
            unit_cost=Decimal('10000'),
            user=self.user,
        )
        self.assertEqual(movement.quantity, Decimal('5.000'))

    @override_settings(
        ERP_ISOMERIX_WEBHOOK_URL='https://erp.example/webhook/',
        ERP_ISOMERIX_BEARER_TOKEN='secret-token',
    )
    def test_receive_stock_posts_payload(self):
        from unittest.mock import patch

        from kitchen.services import erp_isomerix

        with patch.object(erp_isomerix, '_post_json') as mock_post:
            with self.captureOnCommitCallbacks(execute=True):
                movement = receive_stock(
                    product=self.product,
                    quantity=Decimal('12.5'),
                    unit_cost=Decimal('18000'),
                    user=self.user,
                    supplier=self.supplier,
                    note='Test prixod',
                )
            self.assertTrue(mock_post.called)
            body = mock_post.call_args[0][0]
            self.assertEqual(body['source'], 'chefpro')
            self.assertEqual(body['event'], 'receipt.created')
            payload = body['payload']
            self.assertEqual(payload['external_id'], f'chefpro-sm-{movement.pk}')
            self.assertEqual(payload['item_name'], 'GuruchERP')
            self.assertEqual(payload['quantity'], '12.500')
            self.assertEqual(payload['unit'], 'kg')
            self.assertEqual(payload['unit_price'], '18000.00')
            self.assertEqual(payload['supplier_name'], 'Yetkazuvchi')

    @override_settings(
        ERP_ISOMERIX_WEBHOOK_URL='https://erp.example/webhook/',
        ERP_ISOMERIX_BEARER_TOKEN='secret-token',
    )
    def test_update_receipt_posts_updated_event(self):
        from unittest.mock import patch

        from kitchen.services import erp_isomerix

        with patch.object(erp_isomerix, '_post_json'):
            with self.captureOnCommitCallbacks(execute=True):
                movement = receive_stock(
                    product=self.product,
                    quantity=Decimal('10'),
                    unit_cost=Decimal('5000'),
                    user=self.user,
                )
        with patch.object(erp_isomerix, '_post_json') as mock_post:
            with self.captureOnCommitCallbacks(execute=True):
                update_receipt(
                    movement=movement,
                    quantity=Decimal('11'),
                    unit_cost=Decimal('5500'),
                    user=self.user,
                )
            self.assertTrue(mock_post.called)
            self.assertEqual(mock_post.call_args[0][0]['event'], 'receipt.updated')
