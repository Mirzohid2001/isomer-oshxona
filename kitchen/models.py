from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Unit(models.TextChoices):
    KG = 'kg', 'kg'
    L = 'l', 'l'
    PCS = 'dona', 'dona'
    G = 'g', 'g'


class MealType(models.TextChoices):
    BREAKFAST = 'breakfast', 'Nonushta'
    LUNCH = 'lunch', 'Tushlik'
    DINNER = 'dinner', 'Kechki ovqat'
    OTHER = 'other', 'Boshqa'


class MovementType(models.TextChoices):
    IN = 'in', 'Prixod'
    OUT = 'out', 'Rasxod'
    ADJUST = 'adjust', 'Tuzatish'
    WASTE = 'waste', 'Chiqindi'


class Shift(models.TextChoices):
    ONE = '1', '1-smena'
    TWO = '2', '2-smena'


class Category(models.Model):
    name = models.CharField('Nomi', max_length=120)

    class Meta:
        ordering = ['name']
        verbose_name = 'Kategoriya'
        verbose_name_plural = 'Kategoriyalar'
        constraints = [
            models.UniqueConstraint(Lower('name'), name='kit_category_name_lower_uniq'),
        ]

    def __str__(self):
        return self.name


class Supplier(models.Model):
    name = models.CharField('Nomi', max_length=200)
    phone = models.CharField('Telefon', max_length=40, blank=True)
    note = models.TextField('Izoh', blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Yetkazib beruvchi'
        verbose_name_plural = 'Yetkazib beruvchilar'

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField('Nomi', max_length=200)
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='products',
        verbose_name='Kategoriya',
    )
    unit = models.CharField('Birlik', max_length=10, choices=Unit.choices, default=Unit.KG)
    quantity = models.DecimalField(
        'Qoldiq',
        max_digits=12,
        decimal_places=3,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    avg_cost = models.DecimalField(
        'O‘rtacha tannarx',
        max_digits=14,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    min_stock = models.DecimalField(
        'Min qoldiq',
        max_digits=12,
        decimal_places=3,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    kcal_per_unit = models.DecimalField(
        'Kkal / 1 birlik',
        max_digits=10,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Masalan 1 kg yoki 1 litr uchun',
    )
    protein = models.DecimalField(
        'Oqsil g / 1 birlik',
        max_digits=10,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text='1 kg/l/dona ichidagi oqsil (gramm). 100 g da 26 g bo‘lsa, 1 kg da 260',
    )
    fat = models.DecimalField(
        'Yog‘ g / 1 birlik',
        max_digits=10,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text='1 birlikdagi yog‘ (gramm)',
    )
    carbs = models.DecimalField(
        'Uglevod g / 1 birlik',
        max_digits=10,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
        help_text='1 birlikdagi uglevod (gramm)',
    )
    allergens = models.CharField('Allergenlar', max_length=255, blank=True)
    expiry_date = models.DateField('Muddat', null=True, blank=True)
    default_location = models.ForeignKey(
        'StorageLocation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
        verbose_name='Asosiy joy',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Mahsulot'
        verbose_name_plural = 'Mahsulotlar'
        indexes = [
            models.Index(fields=['is_active', 'quantity'], name='kit_prod_active_qty'),
            models.Index(fields=['expiry_date'], name='kit_prod_expiry'),
        ]

    def __str__(self):
        return self.name

    @property
    def stock_value(self):
        from kitchen.services.precision import money
        return money(self.quantity * self.avg_cost)

    @property
    def is_low(self):
        return self.quantity <= self.min_stock

    @property
    def is_expiring_soon(self):
        if not self.expiry_date:
            return False
        today = timezone.localdate()
        return today <= self.expiry_date <= today + timedelta(days=7)

    @property
    def is_expired(self):
        if not self.expiry_date:
            return False
        return self.expiry_date < timezone.localdate()


class StockMovement(models.Model):
    movement_type = models.CharField(max_length=10, choices=MovementType.choices)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='movements')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movements',
    )
    expiry_date = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    cook_batch = models.ForeignKey(
        'CookBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movements',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements',
    )
    created_at = models.DateTimeField(default=timezone.now)
    location = models.ForeignKey(
        'StorageLocation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movements',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Ombor harakati'
        verbose_name_plural = 'Ombor harakatlari'
        indexes = [
            models.Index(fields=['-created_at'], name='kit_move_created'),
            models.Index(fields=['movement_type', '-created_at'], name='kit_move_type_created'),
            models.Index(fields=['product', '-created_at'], name='kit_move_prod_created'),
        ]

    def __str__(self):
        return f'{self.get_movement_type_display()} — {self.product}'


class StorageLocation(models.Model):
    """Ombor zonasi / oshxona joyi (sovuqxona, quruq ombor, …)."""

    name = models.CharField('Nomi', max_length=120, unique=True)
    code = models.CharField('Kod', max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Ombor joyi'
        verbose_name_plural = 'Ombor joylari'

    def __str__(self):
        return self.name


class StockLot(models.Model):
    """Partiya — FEFO uchun muddat bo‘yicha sarflash."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='lots')
    location = models.ForeignKey(
        StorageLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lots',
    )
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    unit_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    expiry_date = models.DateField(null=True, blank=True)
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lots',
    )
    received_at = models.DateTimeField(default=timezone.now)
    source_movement = models.ForeignKey(
        StockMovement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_lots',
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['expiry_date', 'received_at', 'id']
        verbose_name = 'Partiya'
        verbose_name_plural = 'Partiyalar'
        indexes = [
            models.Index(fields=['product', 'expiry_date', 'received_at'], name='kit_lot_fefo'),
            models.Index(fields=['product', 'quantity'], name='kit_lot_prod_qty'),
        ]

    def __str__(self):
        return f'{self.product} · {self.quantity} · {self.expiry_date or "muddatsiz"}'


class StockLotAllocation(models.Model):
    """Rasxod qaysi partiyadan olingani."""

    lot = models.ForeignKey(StockLot, on_delete=models.PROTECT, related_name='allocations')
    movement = models.ForeignKey(StockMovement, on_delete=models.CASCADE, related_name='lot_allocations')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))

    class Meta:
        ordering = ['id']


class Recipe(models.Model):
    name = models.CharField('Nomi', max_length=200)
    description = models.TextField('Tavsif', blank=True)
    meal_type = models.CharField(
        'Ovqat turi',
        max_length=20,
        choices=MealType.choices,
        default=MealType.LUNCH,
    )
    base_portions = models.PositiveIntegerField('Baza porsiya', default=1)
    allergens = models.CharField('Allergenlar', max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Retsept'
        verbose_name_plural = 'Retseptlar'

    def __str__(self):
        return self.name


class RecipeItem(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='recipe_items')
    quantity_per_portion = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal('0.001'))],
    )

    class Meta:
        unique_together = [('recipe', 'product')]
        ordering = ['product__name']

    def __str__(self):
        return f'{self.recipe} — {self.product}'


class CookBatch(models.Model):
    class Status(models.TextChoices):
        QUEUED = 'queued', 'Navbat'
        COOKING = 'cooking', 'Tayyorlanmoqda'
        DONE = 'done', 'Bajarildi'
        CANCELLED = 'cancelled', 'Bekor'

    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT, related_name='batches')
    portions = models.PositiveIntegerField()
    cooked_at = models.DateTimeField(default=timezone.now)
    shift = models.CharField(
        'Smena',
        max_length=2,
        choices=Shift.choices,
        blank=True,
        default='',
    )
    total_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    cost_per_portion = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    total_kcal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    kcal_per_portion = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    protein_per_portion = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    fat_per_portion = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    carbs_per_portion = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DONE)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cook_batches',
    )

    class Meta:
        ordering = ['-cooked_at']
        verbose_name = 'Pishirish'
        verbose_name_plural = 'Pishirishlar'
        indexes = [
            models.Index(fields=['-cooked_at'], name='kit_cook_cooked'),
            models.Index(fields=['status', '-cooked_at'], name='kit_cook_status'),
        ]

    def __str__(self):
        return f'{self.recipe} × {self.portions}'


class CookBatchItem(models.Model):
    batch = models.ForeignKey(CookBatch, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    line_cost = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))

    class Meta:
        ordering = ['product__name']


class DailyMenu(models.Model):
    date = models.DateField(unique=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-date']
        verbose_name = 'Kunlik menyu'
        verbose_name_plural = 'Kunlik menyular'

    def __str__(self):
        return str(self.date)


class DailyMenuItem(models.Model):
    menu = models.ForeignKey(DailyMenu, on_delete=models.CASCADE, related_name='items')
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT)
    meal_type = models.CharField(max_length=20, choices=MealType.choices, default=MealType.LUNCH)
    portions = models.PositiveIntegerField(default=1)
    is_cooked = models.BooleanField(default=False)
    cook_batch = models.ForeignKey(
        CookBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='menu_items',
    )

    class Meta:
        ordering = ['meal_type', 'recipe__name']


class MenuTemplate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class MenuTemplateItem(models.Model):
    template = models.ForeignKey(MenuTemplate, on_delete=models.CASCADE, related_name='items')
    weekday = models.PositiveSmallIntegerField()
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT)
    meal_type = models.CharField(max_length=20, choices=MealType.choices, default=MealType.LUNCH)
    portions = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['weekday', 'meal_type']


class DailyHeadcount(models.Model):
    date = models.DateField()
    shift = models.CharField(max_length=2, choices=Shift.choices)
    people_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [('date', 'shift')]
        ordering = ['-date', 'shift']

    def __str__(self):
        return f'{self.date} {self.get_shift_display()}: {self.people_count}'


class MonthlyBudget(models.Model):
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField()
    limit_amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        unique_together = [('year', 'month')]
        ordering = ['-year', '-month']

    def __str__(self):
        return f'{self.year}-{self.month:02d}'


class AuditLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=80)
    entity = models.CharField(max_length=80)
    entity_id = models.PositiveIntegerField(null=True, blank=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at'], name='kit_audit_created'),
            models.Index(fields=['action', '-created_at'], name='kit_audit_action'),
        ]


class ApprovalStatus(models.TextChoices):
    PENDING = 'pending', 'Kutilmoqda'
    APPROVED = 'approved', 'Tasdiqlangan'
    REJECTED = 'rejected', 'Rad etilgan'


class StockChangeRequest(models.Model):
    """Tuzatish / chiqindi — staff tasdiqlashi mumkin."""

    class RequestType(models.TextChoices):
        ADJUST = 'adjust', 'Tuzatish'
        WASTE = 'waste', 'Chiqindi'

    request_type = models.CharField(max_length=10, choices=RequestType.choices)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='change_requests')
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    new_quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    requested_from_quantity = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=12,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_change_requests',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_change_reviews',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Qoralama'
        ORDERED = 'ordered', 'Buyurtma'
        RECEIVED = 'received', 'Qabul qilingan'
        CANCELLED = 'cancelled', 'Bekor'

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name='purchase_orders',
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    note = models.CharField(max_length=255, blank=True)
    ordered_at = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'PO-{self.pk} · {self.supplier}'


class PurchaseOrderLine(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal('0.001'))],
    )
    unit_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    expiry_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['id']


class HygieneCheck(models.Model):
    """Oddiy HACCP / sanitariya yozuvi."""

    class CheckType(models.TextChoices):
        TEMP = 'temp', 'Harorat'
        CLEAN = 'clean', 'Tozalik'
        HAND = 'hand', 'Qo‘l gigiyenasi'
        OTHER = 'other', 'Boshqa'

    checked_at = models.DateTimeField(default=timezone.now)
    check_type = models.CharField(max_length=12, choices=CheckType.choices)
    location = models.ForeignKey(
        StorageLocation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hygiene_checks',
    )
    is_ok = models.BooleanField(default=True)
    value = models.CharField(max_length=80, blank=True, help_text='Masalan harorat °C')
    note = models.CharField(max_length=255, blank=True)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ['-checked_at']
