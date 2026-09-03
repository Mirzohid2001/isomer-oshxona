from django.contrib import admin

from kitchen.models import (
    AuditLog,
    Category,
    CookBatch,
    CookBatchItem,
    DailyHeadcount,
    DailyMenu,
    DailyMenuItem,
    MenuTemplate,
    MenuTemplateItem,
    MonthlyBudget,
    Product,
    Recipe,
    RecipeItem,
    StockMovement,
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


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ['name']


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'is_active']
    search_fields = ['name']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'unit', 'quantity', 'avg_cost', 'min_stock', 'is_active']
    list_filter = ['category', 'unit', 'is_active']
    search_fields = ['name']


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'movement_type', 'product', 'quantity', 'unit_cost', 'total_cost']
    list_filter = ['movement_type']


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ['name', 'meal_type', 'base_portions', 'is_active']
    inlines = [RecipeItemInline]


@admin.register(CookBatch)
class CookBatchAdmin(admin.ModelAdmin):
    list_display = ['cooked_at', 'recipe', 'portions', 'total_cost', 'status']
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
