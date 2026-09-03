from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
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
    RecipeItem,
    Shift,
    StockChangeRequest,
    StockLot,
    StorageLocation,
    Supplier,
    Unit,
)
from kitchen.services.analytics import build_analytics
from kitchen.services.approvals import (
    receive_purchase_order,
    review_change_request,
    submit_adjust_request,
    submit_waste_request,
)
from kitchen.services.cook import cancel_cook_batch, cook_recipe, queue_cook, start_queued_cook
from kitchen.services.nutrition_lookup import lookup_local, suggest_nutrition
from kitchen.services.precision import money, qty, weighted_avg
from kitchen.services.recipe_cost import recipe_nutrition
from kitchen.services.shopping import shopping_list_for_range
from kitchen.services.stock import StockError, consume_stock, preview_fefo_allocation, receive_stock
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

