from django.db import migrations, models


MEAL_CHOICES_FULL = [
    ('breakfast', 'Nonushta'),
    ('lunch', 'Tushlik'),
    ('dinner', 'Kechki ovqat'),
    ('night', 'Tungi ovqat'),
    ('other', 'Boshqa'),
]
MEAL_CHOICES_CHECKIN = [
    ('breakfast', 'Nonushta'),
    ('lunch', 'Tushlik'),
    ('dinner', 'Kechki ovqat'),
    ('night', 'Tungi ovqat'),
]


def afternoon_to_night(apps, schema_editor):
    for model_name in ('MealCheckin', 'Recipe', 'DailyMenuItem', 'MenuTemplateItem'):
        Model = apps.get_model('kitchen', model_name)
        Model.objects.filter(meal_type='afternoon').update(meal_type='night')


def night_to_afternoon(apps, schema_editor):
    for model_name in ('MealCheckin', 'Recipe', 'DailyMenuItem', 'MenuTemplateItem'):
        Model = apps.get_model('kitchen', model_name)
        Model.objects.filter(meal_type='night').update(meal_type='afternoon')


class Migration(migrations.Migration):

    dependencies = [
        ('kitchen', '0011_recipe_category'),
    ]

    operations = [
        migrations.RunPython(afternoon_to_night, night_to_afternoon),
        migrations.AlterField(
            model_name='dailymenuitem',
            name='meal_type',
            field=models.CharField(
                choices=MEAL_CHOICES_FULL,
                default='lunch',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='mealcheckin',
            name='meal_type',
            field=models.CharField(choices=MEAL_CHOICES_CHECKIN, max_length=20),
        ),
        migrations.AlterField(
            model_name='menutemplateitem',
            name='meal_type',
            field=models.CharField(
                choices=MEAL_CHOICES_FULL,
                default='lunch',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='recipe',
            name='meal_type',
            field=models.CharField(
                choices=MEAL_CHOICES_FULL,
                default='lunch',
                max_length=20,
                verbose_name='Ovqat turi',
            ),
        ),
    ]
