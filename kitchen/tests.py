from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from kitchen.models import (
    ApprovalStatus,
    Category,
    CookBatch,
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
from kitchen.services.approvals import receive_purchase_order, review_change_request, submit_waste_request
from kitchen.services.cook import cancel_cook_batch, cook_recipe, queue_cook, start_queued_cook
from kitchen.services.nutrition_lookup import lookup_local, suggest_nutrition
from kitchen.services.precision import money, qty, weighted_avg
from kitchen.services.recipe_cost import recipe_nutrition
from kitchen.services.stock import StockError, receive_stock


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
        batch = queue_cook(recipe=self.recipe, portions=5, user=self.user, shift=Shift.TWO)
        self.assertEqual(batch.status, CookBatch.Status.QUEUED)
        before = Product.objects.get(pk=self.product.pk).quantity
        done = start_queued_cook(batch=batch, user=self.user)
        self.assertEqual(done.status, CookBatch.Status.DONE)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, qty(before - Decimal('1.000')))

    def test_cancel_queued_does_not_touch_stock(self):
        before = Product.objects.get(pk=self.product.pk).quantity
        batch = queue_cook(recipe=self.recipe, portions=5, user=self.user)
        cancel_cook_batch(batch=batch, user=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, before)
        batch.refresh_from_db()
        self.assertEqual(batch.status, CookBatch.Status.CANCELLED)


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
