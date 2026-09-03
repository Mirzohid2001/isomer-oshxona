from django import forms
from django.forms import inlineformset_factory

from kitchen.models import (
    Category,
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
    Supplier,
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
    expiry_date = forms.DateField(required=False, label='Muddat', widget=forms.DateInput(attrs={'type': 'date'}))
    location = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label='Ombor joyi',
    )
    note = forms.CharField(required=False, label='Izoh')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from kitchen.models import StorageLocation
        self.fields['location'].queryset = StorageLocation.objects.filter(is_active=True)


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
        fields = ['name', 'description', 'meal_type', 'base_portions', 'allergens', 'is_active']


class RecipeItemForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = RecipeItem
        fields = ['product', 'quantity_per_portion']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['quantity_per_portion'].label = 'Miqdor / baza retsept'
        self.fields['quantity_per_portion'].help_text = (
            'Bu miqdor retseptdagi baza porsiya uchun yoziladi.'
        )


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
            if form.cleaned_data.get('product') and form.cleaned_data.get('quantity_per_portion'):
                count += 1
        if count < 1:
            raise forms.ValidationError('Kamida bitta ingredient kerak.')


RecipeItemFormSet = inlineformset_factory(
    Recipe,
    RecipeItem,
    form=RecipeItemForm,
    formset=BaseRecipeItemFormSet,
    extra=3,
    can_delete=True,
)


class CookForm(StyledFormMixin, forms.Form):
    recipe = forms.ModelChoiceField(queryset=Recipe.objects.filter(is_active=True), label='Ovqat')
    portions = forms.IntegerField(min_value=1, initial=50, label='Porsiya')
    shift = forms.ChoiceField(
        choices=[('', '—')] + list(DailyHeadcount._meta.get_field('shift').choices),
        required=False,
        label='Smena',
    )
    note = forms.CharField(required=False, label='Izoh')


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
        fields = ['date', 'shift', 'people_count']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'})}


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
