"""Mahsulot nomi bo‘yicha oziqlanish qiymatlari (1 birlik: kg / l / dona)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings

# Qiymatlar: 1 kg yoki 1 l (yoki 1 dona) uchun taxminiy o‘rtacha.
# Manba: umumiy oziq-ovqat jadvallari (USDA / oshxona amaliyoti).
_CATALOG: list[dict] = [
    # Don / un
    {'keys': ['guruch', 'рис', 'rice'], 'unit': 'kg', 'kcal': 3500, 'protein': 70, 'fat': 5, 'carbs': 780, 'allergens': ''},
    {'keys': ['un', 'bugdoy un', 'мука', 'flour'], 'unit': 'kg', 'kcal': 3640, 'protein': 100, 'fat': 10, 'carbs': 760, 'allergens': 'gluten'},
    {'keys': ['makaron', 'спагетти', 'pasta', 'лапша'], 'unit': 'kg', 'kcal': 3700, 'protein': 130, 'fat': 15, 'carbs': 750, 'allergens': 'gluten'},
    {'keys': ['grechka', 'гречка', 'buckwheat'], 'unit': 'kg', 'kcal': 3430, 'protein': 130, 'fat': 30, 'carbs': 720, 'allergens': ''},
    {'keys': ['no‘xat', 'noxat', 'горох', 'peas'], 'unit': 'kg', 'kcal': 3410, 'protein': 230, 'fat': 15, 'carbs': 600, 'allergens': ''},
    {'keys': ['loviya', 'фасоль', 'beans'], 'unit': 'kg', 'kcal': 3330, 'protein': 210, 'fat': 15, 'carbs': 600, 'allergens': ''},
    {'keys': ['mosh', 'маш', 'mung'], 'unit': 'kg', 'kcal': 3470, 'protein': 240, 'fat': 12, 'carbs': 630, 'allergens': ''},
    # Go‘sht / baliq
    {'keys': ["go‘sht", 'gosht', 'mol go‘shti', 'mol goshti', 'говядина', 'beef'], 'unit': 'kg', 'kcal': 2500, 'protein': 260, 'fat': 150, 'carbs': 0, 'allergens': ''},
    {'keys': ['qo‘y', 'qoy', 'баранина', 'lamb', 'mutton'], 'unit': 'kg', 'kcal': 2800, 'protein': 250, 'fat': 200, 'carbs': 0, 'allergens': ''},
    {'keys': ['tovuq', 'товуқ', 'курица', 'chicken'], 'unit': 'kg', 'kcal': 1650, 'protein': 310, 'fat': 36, 'carbs': 0, 'allergens': ''},
    {'keys': ['tovuq son', 'tovuq filesi', 'куриная грудка'], 'unit': 'kg', 'kcal': 1100, 'protein': 230, 'fat': 12, 'carbs': 0, 'allergens': ''},
    {'keys': ['baliq', 'рыба', 'fish'], 'unit': 'kg', 'kcal': 1200, 'protein': 200, 'fat': 40, 'carbs': 0, 'allergens': 'baliq'},
    {'keys': ['kolbasa', 'колбаса', 'sausage'], 'unit': 'kg', 'kcal': 3000, 'protein': 120, 'fat': 280, 'carbs': 20, 'allergens': ''},
    # Sut / tuxum
    {'keys': ['sut', 'молоко', 'milk'], 'unit': 'l', 'kcal': 640, 'protein': 33, 'fat': 36, 'carbs': 48, 'allergens': 'sut'},
    {'keys': ['qatiq', 'ayron', 'кефир', 'kefir', 'yogurt'], 'unit': 'l', 'kcal': 550, 'protein': 34, 'fat': 20, 'carbs': 45, 'allergens': 'sut'},
    {'keys': ['qaymoq', 'сметана', 'sour cream'], 'unit': 'kg', 'kcal': 2100, 'protein': 25, 'fat': 210, 'carbs': 35, 'allergens': 'sut'},
    {'keys': ['sariyog‘', 'sariyog', 'сливочное масло', 'butter'], 'unit': 'kg', 'kcal': 7170, 'protein': 5, 'fat': 810, 'carbs': 5, 'allergens': 'sut'},
    {'keys': ['pishloq', 'сыр', 'cheese'], 'unit': 'kg', 'kcal': 3500, 'protein': 250, 'fat': 270, 'carbs': 15, 'allergens': 'sut'},
    {'keys': ['tvorog', 'творог', 'cottage'], 'unit': 'kg', 'kcal': 1200, 'protein': 160, 'fat': 50, 'carbs': 30, 'allergens': 'sut'},
    {'keys': ['tuxum', 'яйцо', 'egg', 'яйца'], 'unit': 'dona', 'kcal': 70, 'protein': 6.3, 'fat': 5, 'carbs': 0.4, 'allergens': 'tuxum'},
    # Yog‘
    {'keys': ['o‘simlik yog‘i', 'osimlik yogi', 'подсолнечное', 'растительное масло', 'oil', 'yog‘', 'yog'], 'unit': 'l', 'kcal': 8840, 'protein': 0, 'fat': 1000, 'carbs': 0, 'allergens': ''},
    {'keys': ['paxta yog‘i', 'paxta yogi'], 'unit': 'l', 'kcal': 8840, 'protein': 0, 'fat': 1000, 'carbs': 0, 'allergens': ''},
    # Sabzavot
    {'keys': ['kartoshka', 'картофель', 'potato'], 'unit': 'kg', 'kcal': 770, 'protein': 20, 'fat': 1, 'carbs': 170, 'allergens': ''},
    {'keys': ['sabzi', 'морковь', 'carrot'], 'unit': 'kg', 'kcal': 410, 'protein': 9, 'fat': 2, 'carbs': 100, 'allergens': ''},
    {'keys': ['piyoz', 'лук', 'onion'], 'unit': 'kg', 'kcal': 400, 'protein': 11, 'fat': 1, 'carbs': 93, 'allergens': ''},
    {'keys': ['sarimsoq', 'чеснок', 'garlic'], 'unit': 'kg', 'kcal': 1490, 'protein': 64, 'fat': 5, 'carbs': 330, 'allergens': ''},
    {'keys': ['pomidor', 'томат', 'tomato'], 'unit': 'kg', 'kcal': 180, 'protein': 9, 'fat': 2, 'carbs': 39, 'allergens': ''},
    {'keys': ['bodring', 'огурец', 'cucumber'], 'unit': 'kg', 'kcal': 150, 'protein': 7, 'fat': 1, 'carbs': 36, 'allergens': ''},
    {'keys': ['karam', 'капуста', 'cabbage'], 'unit': 'kg', 'kcal': 250, 'protein': 13, 'fat': 1, 'carbs': 58, 'allergens': ''},
    {'keys': ['lavlagi', 'свёкла', 'свекла', 'beet'], 'unit': 'kg', 'kcal': 430, 'protein': 16, 'fat': 2, 'carbs': 100, 'allergens': ''},
    {'keys': ['qovoq', 'тыква', 'pumpkin'], 'unit': 'kg', 'kcal': 260, 'protein': 10, 'fat': 1, 'carbs': 65, 'allergens': ''},
    {'keys': ['baqlajon', 'баклажан', 'eggplant'], 'unit': 'kg', 'kcal': 250, 'protein': 10, 'fat': 2, 'carbs': 60, 'allergens': ''},
    {'keys': ['bulg‘ori', 'bulgori', 'перец', 'bell pepper'], 'unit': 'kg', 'kcal': 270, 'protein': 10, 'fat': 2, 'carbs': 60, 'allergens': ''},
    # Meva
    {'keys': ['olma', 'яблоко', 'apple'], 'unit': 'kg', 'kcal': 520, 'protein': 3, 'fat': 2, 'carbs': 140, 'allergens': ''},
    {'keys': ['banan', 'банан', 'banana'], 'unit': 'kg', 'kcal': 890, 'protein': 11, 'fat': 3, 'carbs': 230, 'allergens': ''},
    {'keys': ['limon', 'лимон', 'lemon'], 'unit': 'kg', 'kcal': 290, 'protein': 11, 'fat': 3, 'carbs': 90, 'allergens': ''},
    # Shirin / boshqa
    {'keys': ['shakar', 'сахар', 'sugar'], 'unit': 'kg', 'kcal': 3870, 'protein': 0, 'fat': 0, 'carbs': 1000, 'allergens': ''},
    {'keys': ['tuz', 'соль', 'salt'], 'unit': 'kg', 'kcal': 0, 'protein': 0, 'fat': 0, 'carbs': 0, 'allergens': ''},
    {'keys': ['asal', 'мёд', 'мед', 'honey'], 'unit': 'kg', 'kcal': 3040, 'protein': 3, 'fat': 0, 'carbs': 820, 'allergens': ''},
    {'keys': ['mayonez', 'майонез', 'mayonnaise'], 'unit': 'kg', 'kcal': 6800, 'protein': 10, 'fat': 750, 'carbs': 10, 'allergens': 'tuxum'},
    {'keys': ['tomat pasta', 'томатная паста', 'tomato paste'], 'unit': 'kg', 'kcal': 820, 'protein': 40, 'fat': 5, 'carbs': 190, 'allergens': ''},
    {'keys': ['non', 'хлеб', 'bread'], 'unit': 'kg', 'kcal': 2650, 'protein': 90, 'fat': 30, 'carbs': 490, 'allergens': 'gluten'},
    {'keys': ['choy', 'чай', 'tea'], 'unit': 'kg', 'kcal': 0, 'protein': 0, 'fat': 0, 'carbs': 0, 'allergens': ''},
    {'keys': ['qahva', 'кофе', 'coffee'], 'unit': 'kg', 'kcal': 0, 'protein': 0, 'fat': 0, 'carbs': 0, 'allergens': ''},
    {'keys': ['suv', 'вода', 'water'], 'unit': 'l', 'kcal': 0, 'protein': 0, 'fat': 0, 'carbs': 0, 'allergens': ''},
    # Ziravor
    {'keys': ['zira', 'зира', 'cumin'], 'unit': 'kg', 'kcal': 3750, 'protein': 180, 'fat': 220, 'carbs': 440, 'allergens': ''},
    {'keys': ['qora murch', 'черный перец', 'pepper'], 'unit': 'kg', 'kcal': 2510, 'protein': 100, 'fat': 30, 'carbs': 640, 'allergens': ''},
    {'keys': ['koriander', 'кинза', 'кориандр'], 'unit': 'kg', 'kcal': 230, 'protein': 21, 'fat': 5, 'carbs': 40, 'allergens': ''},
]


def _norm(text: str) -> str:
    text = (text or '').lower().strip()
    repl = {
        '‘': "'",
        '’': "'",
        '`': "'",
        'ʻ': "'",
        'ʼ': "'",
        'ö': 'o',
        'ó': 'o',
        'ў': "o'",
        'қ': 'q',
        'ғ': "g'",
        'ҳ': 'h',
    }
    for a, b in repl.items():
        text = text.replace(a, b)
    text = re.sub(r'[^\w\s\'\-]+', ' ', text, flags=re.UNICODE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _score(name: str, key: str) -> int:
    if name == key:
        return 1000 + len(key)
    if key in name:
        return 500 + len(key)
    if name in key and len(name) >= 3:
        return 300 + len(name)
    # token overlap
    n_tokens = set(name.split())
    k_tokens = set(key.split())
    overlap = n_tokens & k_tokens
    if overlap:
        return 100 + sum(len(t) for t in overlap)
    return 0


def lookup_local(name: str, unit: str | None = None) -> dict | None:
    cleaned = _norm(name)
    if len(cleaned) < 2:
        return None

    best = None
    best_score = 0
    best_key = ''
    for row in _CATALOG:
        for key in row['keys']:
            score = _score(cleaned, _norm(key))
            if score > best_score:
                best_score = score
                best = row
                best_key = key

    if not best or best_score < 100:
        return None

    result = {
        'found': True,
        'source': 'local',
        'match': best_key,
        'unit': best['unit'],
        'kcal_per_unit': float(best['kcal']),
        'protein': float(best['protein']),
        'fat': float(best['fat']),
        'carbs': float(best['carbs']),
        'allergens': best.get('allergens') or '',
        'note': f'1 {best["unit"]} uchun taxminiy qiymat ({best_key})',
    }
    # Agar foydalanuvchi boshqa birlik tanlagan bo‘lsa — ogohlantiramiz, lekin qiymatni shu birlikda qoldiramiz
    if unit and unit != best['unit']:
        result['note'] += f'. Tavsiya: birlik «{best["unit"]}»'
        result['suggested_unit'] = best['unit']
    return result


def _lookup_ai(name: str, unit: str | None = None) -> dict | None:
    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not api_key.strip():
        return None

    unit = unit or 'kg'
    prompt = (
        'Oshxona ombori uchun oziqlanish qiymatini JSON qaytar. '
        f'Mahsulot: "{name}". Birlik: {unit}. '
        'Faqat JSON: {"kcal_per_unit": number, "protein": number, "fat": number, '
        '"carbs": number, "allergens": string, "unit": "kg"|"l"|"dona"}. '
        'Qiymatlar 1 birlik (1 kg yoki 1 l yoki 1 dona) uchun gramm/kkal. '
        'Aniq bilmasang taxminiy o‘rtacha yoz.'
    )
    body = json.dumps(
        {
            'model': getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini'),
            'temperature': 0.1,
            'response_format': {'type': 'json_object'},
            'messages': [
                {'role': 'system', 'content': 'You are a nutrition assistant for a canteen. Reply JSON only.'},
                {'role': 'user', 'content': prompt},
            ],
        }
    ).encode()
    req = urllib.request.Request(
        'https://api.openai.com/v1/chat/completions',
        data=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode())
        content = payload['choices'][0]['message']['content']
        data = json.loads(content)
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, IndexError, TypeError):
        return None

    def num(v, default=0):
        try:
            return float(Decimal(str(v)))
        except Exception:
            return default

    return {
        'found': True,
        'source': 'ai',
        'match': name,
        'unit': data.get('unit') or unit,
        'kcal_per_unit': num(data.get('kcal_per_unit')),
        'protein': num(data.get('protein')),
        'fat': num(data.get('fat')),
        'carbs': num(data.get('carbs')),
        'allergens': str(data.get('allergens') or ''),
        'note': 'AI taxminiy qiymat — tekshirib saqlang',
        'suggested_unit': data.get('unit') or unit,
    }


def suggest_nutrition(name: str, unit: str | None = None) -> dict:
    local = lookup_local(name, unit=unit)
    if local:
        return local
    ai = _lookup_ai(name, unit=unit)
    if ai:
        return ai
    return {
        'found': False,
        'source': None,
        'note': 'Topilmadi. Nomni aniqroq yozing (masalan: Guruch, Tovuq, Sut) yoki qo‘lda kiriting.',
    }
