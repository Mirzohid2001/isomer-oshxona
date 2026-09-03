# Oshxona

Neft zavodi oshpazi uchun Django oshxona boshqaruvi.

## Ishga tushirish

```bash
cd oshxona
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_kitchen
python manage.py runserver
```

Brauzer: http://127.0.0.1:8000/

Login: `oshpaz` / `oshpaz123`

## Imkoniyatlar

- Mahsulotlar, prixod, ombor, yetkazib beruvchilar
- Retseptlar: porsiya tannarxi, kkal, BJU, allergenlar, tannarx tarixi (Chart.js), print/PDF
- Pishirish: live HTMX preview + avtomatik rasxod + smenadan tavsiya porsiya
- Kunlik menyu, shablonlar, xarid ro‘yxati
- Smena/odam soni, chiqindi, byudjet, hisobotlar (oy filter, yetkazuvchilar), CSV
- Audit jurnal, mobil menyu, sahifalash (mahsulot, retsept, prixod, tarix, audit)
- PDF eksport (pishirish, retsept, xarid, hisobot)
- Bildirishnomalar: kam qoldiq va muddat (qo‘ng‘iroqcha)
