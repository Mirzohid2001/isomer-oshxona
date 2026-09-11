# Generated manually for RecipeCategory

import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Lower


def seed_and_assign(apps, schema_editor):
    Recipe = apps.get_model('kitchen', 'Recipe')
    RecipeCategory = apps.get_model('kitchen', 'RecipeCategory')

    main, _ = RecipeCategory.objects.get_or_create(
        name='Asosiy ovqat',
        defaults={'include_in_meal_sverka': True},
    )
    if not main.include_in_meal_sverka:
        main.include_in_meal_sverka = True
        main.save(update_fields=['include_in_meal_sverka'])

    side, _ = RecipeCategory.objects.get_or_create(
        name='Qo‘shimcha',
        defaults={'include_in_meal_sverka': False},
    )
    if side.include_in_meal_sverka:
        side.include_in_meal_sverka = False
        side.save(update_fields=['include_in_meal_sverka'])

    for recipe in Recipe.objects.all():
        if getattr(recipe, 'include_in_meal_sverka', True):
            recipe.category = main
        else:
            recipe.category = side
        recipe.save(update_fields=['category'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('kitchen', '0010_recipe_include_in_meal_sverka'),
    ]

    operations = [
        migrations.CreateModel(
            name='RecipeCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, verbose_name='Nomi')),
                (
                    'include_in_meal_sverka',
                    models.BooleanField(
                        default=True,
                        help_text=(
                            'Asosiy ovqat uchun yoqing. Salat, kefir, kompot kabi qo‘shimchalar '
                            'uchun o‘chiring — ombordan yechiladi, lekin QR porsiya sverkasiga qo‘shilmaydi.'
                        ),
                        verbose_name='Ovqat sverkasiga kiradi',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Retsept kategoriyasi',
                'verbose_name_plural': 'Retsept kategoriyalari',
                'ordering': ['name'],
            },
        ),
        migrations.AddConstraint(
            model_name='recipecategory',
            constraint=models.UniqueConstraint(Lower('name'), name='kit_recipe_category_name_lower_uniq'),
        ),
        migrations.AddField(
            model_name='recipe',
            name='category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='recipes',
                to='kitchen.recipecategory',
                verbose_name='Kategoriya',
            ),
        ),
        migrations.RunPython(seed_and_assign, noop_reverse),
        migrations.RemoveField(
            model_name='recipe',
            name='include_in_meal_sverka',
        ),
    ]
