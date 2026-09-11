# Generated manually for supplier debt / payments

from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('kitchen', '0012_meal_tungi_ovqat'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockmovement',
            name='is_credit',
            field=models.BooleanField(
                default=False,
                help_text='Yetkazuvchidan qarzga olingan prixod. To‘lovlar «Qarzlar» bo‘limida yuritiladi.',
                verbose_name='Qarzga',
            ),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(fields=['is_credit', 'supplier'], name='kit_move_credit_supp'),
        ),
        migrations.CreateModel(
            name='SupplierPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'amount',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=14,
                        validators=[MinValueValidator(Decimal('0.01'))],
                        verbose_name='Summa',
                    ),
                ),
                ('paid_on', models.DateField(default=django.utils.timezone.localdate, verbose_name='To‘lov sanasi')),
                ('note', models.CharField(blank=True, max_length=255, verbose_name='Izoh')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'created_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='supplier_payments',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'supplier',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='payments',
                        to='kitchen.supplier',
                        verbose_name='Yetkazuvchi',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Yetkazuvchi to‘lovi',
                'verbose_name_plural': 'Yetkazuvchi to‘lovlari',
                'ordering': ['-paid_on', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='supplierpayment',
            index=models.Index(fields=['supplier', '-paid_on'], name='kit_supp_pay_supp_day'),
        ),
    ]
