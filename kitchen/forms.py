from django import forms
from django.db.models import Q
from django.forms import inlineformset_factory
from django.utils import timezone

from kitchen.models import (
    Category,
    RecipeCategory,
    DailyHeadcount,
    DailyMenu,
    DailyMenuItem,
    HygieneCheck,
    MealCheckin,
    MenuTemplate,
    MenuTemplateItem,
    MonthlyBudget,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    Recipe,
    RecipeItem,
    Supplier,
    Worker,
)


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get('class', '')
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = f'{css} form-check'.strip()
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = f'{css} form-select'.strip()
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs['class'] = f'{css} form-textarea'.strip()
            else:
                field.widget.attrs['class'] = f'{css} form-input'.strip()


class CategoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name']

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise forms.ValidationError('Nom bo‘sh bo‘lmasin.')
        qs = Category.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        existing = qs.first()
        if existing:
            raise forms.ValidationError(f'Bunday kategoriya allaqachon bor: {existing.name}')
        return name


class RecipeCategoryForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = RecipeCategory
        fields = ['name', 'include_in_meal_sverka']

    def clean_name(self):
        name = (self.cleaned_data.get('name') or '').strip()
        if not name:
            raise forms.ValidationError('Nom bo‘sh bo‘lmasin.')
        qs = RecipeCategory.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        existing = qs.first()
        if existing:
            raise forms.ValidationError(f'Bunday kategoriya allaqachon bor: {existing.name}')
        return name


class SupplierForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'phone', 'note', 'is_active']


class ProductForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            'name',
            'category',
            'unit',
            'default_location',
            'min_stock',
            'kcal_per_unit',
            'protein',
            'fat',
            'carbs',
            'allergens',
            'is_active',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from kitchen.models import StorageLocation
        self.fields['default_location'].queryset = StorageLocation.objects.filter(is_active=True)
        self.fields['default_location'].required = False


class ReceiptForm(StyledFormMixin, forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.filter(is_active=True), label='Mahsulot')
    quantity = forms.DecimalField(min_value=0.001, decimal_places=3, max_digits=12, label='Miqdor')
    unit_cost = forms.DecimalField(min_value=0, decimal_places=2, max_digits=14, label='Birlik narxi')
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.filter(is_active=True),
        required=False,
        label='Yetkazib beruvchi',
    )
    is_credit = forms.BooleanField(
        required=False,
        initial=False,
        label='Qarzga',
        help_text='Belgilansa — yetkazuvchiga qarz yoziladi (keyin «Qarzlar»dan to‘lov).',
    )
    expiry_date = forms.DateField(required=False, label='Muddat', widget=forms.DateInput(attrs={'type': 'date'}))
    location = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label='Ombor joyi',
    )
    note = forms.CharField(required=False, label='Izoh')

    def __init__(self, *args, lock_product=False, instance_supplier=None, **kwargs):
        super().__init__(*args, **kwargs)
        from kitchen.models import StorageLocation
        self.fields['location'].queryset = StorageLocation.objects.filter(is_active=True)
        self._lock_product = lock_product
        self._instance_supplier = instance_supplier
        # Tahrirda faol emas yetkazuvchi ham ko‘rinsin
        if instance_supplier is not None:
            self.fields['supplier'].queryset = Supplier.objects.filter(
                Q(is_active=True) | Q(pk=instance_supplier.pk)
            )
        if lock_product:
            self.fields['product'].disabled = True
            self.fields['product'].required = False

    def clean_product(self):
        if getattr(self, '_lock_product', False):
            product_id = self.initial.get('product')
            if product_id:
                return Product.objects.get(pk=product_id)
            return None
        return self.cleaned_data.get('product')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('is_credit') and not cleaned.get('supplier'):
            self.add_error('supplier', 'Qarzga prixod uchun yetkazuvchi tanlang.')
        return cleaned


class SupplierPaymentForm(StyledFormMixin, forms.Form):
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.filter(is_active=True),
        label='Yetkazuvchi',
    )
    amount = forms.DecimalField(min_value=0.01, decimal_places=2, max_digits=14, label='To‘lov summasi')
    paid_on = forms.DateField(
        label='Sana',
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    note = forms.CharField(required=False, label='Izoh')

    def __init__(self, *args, supplier=None, remaining=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._locked_supplier = supplier
        self._remaining = remaining
        if supplier is not None:
            self.fields['supplier'].queryset = Supplier.objects.filter(
                Q(pk=supplier.pk) | Q(is_active=True)
            ).filter(pk=supplier.pk)
            self.fields['supplier'].initial = supplier.pk
        if remaining is not None:
            self.fields['amount'].help_text = (
                f'Qoldiq qarz: {remaining} so‘m. Ko‘proq to‘lash mumkin (oldindan to‘lov).'
            )

    def clean_supplier(self):
        if self._locked_supplier is not None:
            return self._locked_supplier
        return self.cleaned_data.get('supplier')


class AdjustStockForm(StyledFormMixin, forms.Form):
    new_quantity = forms.DecimalField(min_value=0, decimal_places=3, max_digits=12, label='Yangi qoldiq')
    note = forms.CharField(required=False, label='Sabab')


class WasteForm(StyledFormMixin, forms.Form):
    REASONS = [
        ('Buzildi', 'Buzildi'),
        ('To‘kildi', 'To‘kildi'),
        ('Qaytarildi', 'Qaytarildi'),
        ('Muddati o‘tdi', 'Muddati o‘tdi'),
        ('Boshqa', 'Boshqa'),
    ]
    product = forms.ModelChoiceField(queryset=Product.objects.filter(is_active=True), label='Mahsulot')
    quantity = forms.DecimalField(min_value=0.001, decimal_places=3, max_digits=12, label='Miqdor')
    reason = forms.ChoiceField(choices=REASONS, label='Sabab')
    note = forms.CharField(required=False, label='Qo‘shimcha izoh')


class RecipeForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Recipe
        fields = [
            'name',
            'description',
            'category',
            'meal_type',
            'base_portions',
            'allergens',
            'is_active',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = RecipeCategory.objects.all()
        self.fields['category'].required = True
        if not self.instance.pk and not self.initial.get('category'):
            main = RecipeCategory.objects.filter(include_in_meal_sverka=True).order_by('id').first()
            if main:
                self.fields['category'].initial = main.pk


class RecipeItemForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = RecipeItem
        fields = ['product', 'quantity_per_portion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Bo‘sh / o‘chirilgan qatorlar ham "required" xato bermasin.
        self.fields['product'].required = False
        self.fields['quantity_per_portion'].required = False
        self.fields['quantity_per_portion'].label = 'Miqdor / baza retsept'
        self.fields['quantity_per_portion'].help_text = (
            'Bu miqdor retseptdagi baza porsiya uchun yoziladi.'
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('DELETE'):
            return cleaned
        product = cleaned.get('product')
        qty = cleaned.get('quantity_per_portion')
        if product or qty is not None:
            if not product:
                self.add_error('product', 'Mahsulot tanlang.')
            if qty is None:
                self.add_error('quantity_per_portion', 'Miqdorni kiriting.')
        return cleaned


class BaseRecipeItemFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        count = 0
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if form.cleaned_data.get('DELETE'):
                continue
            if form.cleaned_data.get('product') and form.cleaned_data.get('quantity_per_portion') is not None:
                count += 1
        if count < 1:
            raise forms.ValidationError('Kamida bitta ingredient kerak.')


RecipeItemFormSet = inlineformset_factory(
    Recipe,
    RecipeItem,
    form=RecipeItemForm,
    formset=BaseRecipeItemFormSet,
    extra=1,
    can_delete=True,
    min_num=0,
    validate_min=False,
    max_num=50,
)


class CookForm(StyledFormMixin, forms.Form):
    recipe = forms.ModelChoiceField(
        queryset=Recipe.objects.none(),
        label='Ovqat',
    )
    sides = forms.ModelMultipleChoiceField(
        queryset=Recipe.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Qo‘shimchalar',
        help_text='Salat, kefir, kompot — bir xil porsiya bilan birga rasxod. Ovqat sverkasiga kirmaydi.',
    )
    portions = forms.IntegerField(min_value=1, initial=50, label='Porsiya')
    cooked_on = forms.DateField(
        label='Sana',
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='Eski kunlardagi pishirishni ham shu yerdan kiritish mumkin.',
    )
    note = forms.CharField(required=False, label='Izoh')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from kitchen.models import recipe_in_meal_sverka_q, recipe_side_q

        active = Recipe.objects.filter(is_active=True).select_related('category').order_by('name')
        self.fields['recipe'].queryset = active.filter(recipe_in_meal_sverka_q())
        self.fields['sides'].queryset = active.filter(recipe_side_q())

    def clean_cooked_on(self):
        value = self.cleaned_data['cooked_on']
        if value > timezone.localdate():
            raise forms.ValidationError('Kelajak sanasini tanlab bo‘lmaydi.')
        return value

    def clean(self):
        cleaned = super().clean()
        recipe = cleaned.get('recipe')
        sides = list(cleaned.get('sides') or [])
        if recipe:
            sides = [s for s in sides if s.pk != recipe.pk]
            cleaned['sides'] = sides
        cleaned['all_recipes'] = ([recipe] if recipe else []) + sides
        return cleaned


class HygieneCheckForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = HygieneCheck
        fields = ['check_type', 'location', 'is_ok', 'value', 'note']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from kitchen.models import StorageLocation
        self.fields['location'].queryset = StorageLocation.objects.filter(is_active=True)
        self.fields['location'].required = False


class PurchaseOrderForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ['supplier', 'note', 'ordered_at']
        widgets = {'ordered_at': forms.DateInput(attrs={'type': 'date'})}


class PurchaseOrderLineForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = PurchaseOrderLine
        fields = ['product', 'quantity', 'unit_cost', 'expiry_date']
        widgets = {'expiry_date': forms.DateInput(attrs={'type': 'date'})}


class DailyMenuItemForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DailyMenuItem
        fields = ['recipe', 'meal_type', 'portions']


DailyMenuItemFormSet = inlineformset_factory(
    DailyMenu,
    DailyMenuItem,
    form=DailyMenuItemForm,
    extra=4,
    can_delete=True,
)


class MenuTemplateForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = MenuTemplate
        fields = ['name']


class MenuTemplateItemForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = MenuTemplateItem
        fields = ['weekday', 'recipe', 'meal_type', 'portions']
        widgets = {
            'weekday': forms.Select(
                choices=[
                    (0, 'Dushanba'),
                    (1, 'Seshanba'),
                    (2, 'Chorshanba'),
                    (3, 'Payshanba'),
                    (4, 'Juma'),
                    (5, 'Shanba'),
                    (6, 'Yakshanba'),
                ]
            )
        }


MenuTemplateItemFormSet = inlineformset_factory(
    MenuTemplate,
    MenuTemplateItem,
    form=MenuTemplateItemForm,
    extra=5,
    can_delete=True,
)


class HeadcountForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DailyHeadcount
        fields = ['date', 'people_count']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}


class WorkerForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Worker
        fields = ['last_name', 'first_name', 'employee_code', 'department', 'is_active']


class WorkerImportForm(StyledFormMixin, forms.Form):
    file = forms.FileField(
        label='Excel fayl (.xlsx)',
        help_text='Ustunlar: Familiya | Ism | Bo‘lim | Kod',
    )


class StaffMealCheckinForm(StyledFormMixin, forms.Form):
    worker = forms.ModelChoiceField(
        queryset=Worker.objects.filter(is_active=True),
        label='Ishchi',
    )
    meal_type = forms.ChoiceField(choices=MealCheckin.MEAL_CHOICES, label='Mahal')
    portions = forms.IntegerField(min_value=1, max_value=500, initial=1, label='Porsiya')
    served_on = forms.DateField(
        label='Sana',
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def clean_served_on(self):
        value = self.cleaned_data['served_on']
        if value > timezone.localdate():
            raise forms.ValidationError('Kelajak sanasini tanlab bo‘lmaydi.')
        return value


class MealCheckinEditForm(StyledFormMixin, forms.Form):
    worker = forms.ModelChoiceField(queryset=Worker.objects.all(), label='Ishchi')
    meal_type = forms.ChoiceField(choices=MealCheckin.MEAL_CHOICES, label='Mahal')
    portions = forms.IntegerField(min_value=1, max_value=500, initial=1, label='Porsiya')
    served_on = forms.DateField(
        label='Sana',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def clean_served_on(self):
        value = self.cleaned_data['served_on']
        if value > timezone.localdate():
            raise forms.ValidationError('Kelajak sanasini tanlab bo‘lmaydi.')
        return value


class BudgetForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = MonthlyBudget
        fields = ['year', 'month', 'limit_amount']


class ApplyTemplateForm(StyledFormMixin, forms.Form):
    template = forms.ModelChoiceField(queryset=MenuTemplate.objects.all(), label='Shablon')
    week_start = forms.DateField(
        label='Hafta boshi (dushanba)',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def clean_week_start(self):
        value = self.cleaned_data['week_start']
        if value.weekday() != 0:
            raise forms.ValidationError('Hafta boshi dushanba bo‘lishi kerak.')
        return value
