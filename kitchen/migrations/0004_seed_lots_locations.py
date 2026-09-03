from decimal import Decimal

from django.db import migrations


def seed_locations_and_lots(apps, schema_editor):
    StorageLocation = apps.get_model('kitchen', 'StorageLocation')
    Product = apps.get_model('kitchen', 'Product')
    StockLot = apps.get_model('kitchen', 'StockLot')

    defaults = [
        ('Quruq ombor', 'DRY'),
        ('Sovuqxona', 'COLD'),
        ('Oshxona', 'KITCHEN'),
    ]
    loc_map = {}
    for name, code in defaults:
        loc, _ = StorageLocation.objects.get_or_create(name=name, defaults={'code': code})
        loc_map[code] = loc

    dry = loc_map['DRY']
    for p in Product.objects.all():
        if p.default_location_id is None:
            p.default_location = dry
            p.save(update_fields=['default_location'])
        has_lots = StockLot.objects.filter(product_id=p.pk).exists()
        if not has_lots and p.quantity and p.quantity > 0:
            StockLot.objects.create(
                product=p,
                location=p.default_location,
                quantity=p.quantity,
                unit_cost=p.avg_cost or Decimal('0'),
                expiry_date=p.expiry_date,
                note='Migratsiya partiyasi',
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('kitchen', '0003_lots_fefo_ops'),
    ]

    operations = [
        migrations.RunPython(seed_locations_and_lots, noop_reverse),
    ]
