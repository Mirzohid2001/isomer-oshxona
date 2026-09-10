"""
ChefPro prixod → ERP Isomerix KitchenExpense webhook.

Sozlama bo‘sh bo‘lsa — hech narsa yuborilmaydi (xavfsiz no-op).
Faqat oddiy prixod (IN, cook_batch yo‘q).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _webhook_url() -> str:
    return (getattr(settings, 'ERP_ISOMERIX_WEBHOOK_URL', None) or '').strip()


def _bearer() -> str:
    return (getattr(settings, 'ERP_ISOMERIX_BEARER_TOKEN', None) or '').strip()


def _timeout() -> float:
    return float(getattr(settings, 'ERP_ISOMERIX_TIMEOUT_SEC', 8) or 8)


def is_configured() -> bool:
    return bool(_webhook_url() and _bearer())


def external_id_for_movement(movement_id: int) -> str:
    return f'chefpro-sm-{int(movement_id)}'


def _receipt_payload(movement, event: str) -> dict[str, Any]:
    product = movement.product
    supplier_name = ''
    if movement.supplier_id and getattr(movement, 'supplier', None):
        supplier_name = movement.supplier.name or ''
    local_day = timezone.localtime(movement.created_at).date()
    note = (movement.note or '').strip()
    return {
        'schema_version': 1,
        'source': 'chefpro',
        'event': f'receipt.{event}',
        'payload': {
            'external_id': external_id_for_movement(movement.pk),
            'date': local_day.isoformat(),
            'item_name': product.name,
            'quantity': str(movement.quantity),
            'unit': product.unit,
            'unit_price': str(movement.unit_cost),
            'supplier_name': supplier_name or None,
            'notes': note or f'ChefPro prixod #{movement.pk}',
            'is_paid': True,
            'is_debt': False,
            'movement_id': movement.pk,
            'product_id': product.pk,
        },
    }


def _delete_payload(movement_id: int) -> dict[str, Any]:
    return {
        'schema_version': 1,
        'source': 'chefpro',
        'event': 'receipt.deleted',
        'payload': {'external_id': external_id_for_movement(movement_id)},
    }


def _post_json(body: dict[str, Any]) -> None:
    url = _webhook_url()
    if not url:
        return
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {_bearer()}',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            if getattr(resp, 'status', 200) >= 400:
                raw = resp.read().decode('utf-8', errors='replace')[:500]
                logger.warning('ERP Isomerix kitchen webhook HTTP %s: %s', resp.status, raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')[:500]
        logger.warning('ERP Isomerix kitchen webhook HTTP %s: %s', exc.code, raw)
        raise
    except urllib.error.URLError:
        raise


def push_receipt(movement, event: str = 'created') -> None:
    """Sync one prixod to ERP. event: created|updated."""
    if not is_configured():
        return
    from kitchen.models import MovementType

    if movement.movement_type != MovementType.IN or movement.cook_batch_id:
        return
    try:
        _post_json(_receipt_payload(movement, event))
    except (urllib.error.URLError, TimeoutError, OSError):
        logger.exception('ERP Isomerix kitchen webhook failed (movement %s)', movement.pk)


def push_receipt_deleted(movement_id: int) -> None:
    if not is_configured():
        return
    try:
        _post_json(_delete_payload(movement_id))
    except (urllib.error.URLError, TimeoutError, OSError):
        logger.exception(
            'ERP Isomerix kitchen webhook delete failed (movement %s)', movement_id
        )


def schedule_push_receipt(movement_id: int, event: str = 'created') -> None:
    """After DB commit — do not block the stock transaction on network."""

    def run():
        from kitchen.models import StockMovement

        try:
            movement = (
                StockMovement.objects.select_related('product', 'supplier')
                .get(pk=movement_id)
            )
        except StockMovement.DoesNotExist:
            logger.warning('ERP push skipped: movement %s not found', movement_id)
            return
        logger.info('ERP kitchen push start movement=%s event=%s', movement_id, event)
        push_receipt(movement, event=event)

    if connection.in_atomic_block:
        transaction.on_commit(run)
    else:
        run()


def schedule_push_receipt_deleted(movement_id: int) -> None:
    def run():
        push_receipt_deleted(movement_id)

    if connection.in_atomic_block:
        transaction.on_commit(run)
    else:
        run()
