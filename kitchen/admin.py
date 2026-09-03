from django.contrib import admin

from kitchen.models import (
    AuditLog,
    Category,
    CookBatch,
    CookBatchItem,
    DailyHeadcount,
    DailyMenu,
    DailyMenuItem,
    HygieneCheck,
    MenuTemplate,
    MenuTemplateItem,
    MonthlyBudget,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    Recipe,
    RecipeItem,
    StockChangeRequest,
    StockLot,
    StockMovement,
    StorageLocation,
    Supplier,
)


class RecipeItemInline(admin.TabularInline):
    model = RecipeItem
    extra = 1


class CookBatchItemInline(admin.TabularInline):
    model = CookBatchItem
    extra = 0
    readonly_fields = ['product', 'quantity', 'unit_cost', 'line_cost']


class DailyMenuItemInline(admin.TabularInline):
    model = DailyMenuItem
    extra = 1


class MenuTemplateItemInline(admin.TabularInline):
    model = MenuTemplateItem
    extra = 1


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 1


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ['name']


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'is_active']
    search_fields = ['name']


@admin.register(StorageLocation)
class StorageLocationAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'unit', 'quantity', 'avg_cost', 'min_stock', 'is_active']
    list_filter = ['category', 'unit', 'is_active']
    search_fields = ['name']


@admin.register(StockLot)
class StockLotAdmin(admin.ModelAdmin):
    list_display = ['product', 'quantity', 'unit_cost', 'expiry_date', 'location', 'received_at']
    list_filter = ['location']


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'movement_type', 'product', 'quantity', 'unit_cost', 'total_cost']
    list_filter = ['movement_type']


@admin.register(StockChangeRequest)
class StockChangeRequestAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'request_type', 'product', 'status', 'requested_by']


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'supplier', 'status', 'created_at']
    inlines = [PurchaseOrderLineInline]


@admin.register(HygieneCheck)
class HygieneCheckAdmin(admin.ModelAdmin):
    list_display = ['checked_at', 'check_type', 'location', 'is_ok', 'checked_by']


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ['name', 'meal_type', 'base_portions', 'is_active']
    inlines = [RecipeItemInline]


@admin.register(CookBatch)
class CookBatchAdmin(admin.ModelAdmin):
    list_display = ['cooked_at', 'recipe', 'portions', 'shift', 'total_cost', 'status']
    inlines = [CookBatchItemInline]


@admin.register(DailyMenu)
class DailyMenuAdmin(admin.ModelAdmin):
    list_display = ['date', 'note']
    inlines = [DailyMenuItemInline]


@admin.register(MenuTemplate)
class MenuTemplateAdmin(admin.ModelAdmin):
    inlines = [MenuTemplateItemInline]


@admin.register(DailyHeadcount)
class DailyHeadcountAdmin(admin.ModelAdmin):
    list_display = ['date', 'shift', 'people_count']


@admin.register(MonthlyBudget)
class MonthlyBudgetAdmin(admin.ModelAdmin):
    list_display = ['year', 'month', 'limit_amount']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user', 'action', 'entity', 'entity_id']
    readonly_fields = ['user', 'action', 'entity', 'entity_id', 'detail', 'created_at']
