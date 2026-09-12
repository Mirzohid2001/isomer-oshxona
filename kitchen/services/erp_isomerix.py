"""
ChefPro prixod / qarz to‘lovi → ERP Isomerix webhook.

Sozlama bo‘sh bo‘lsa — hech narsa yuborilmaydi (xavfsiz no-op).
Faqat oddiy prixod (IN, cook_batch yo‘q) va SupplierPayment.
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


def _payment_webhook_url() -> str:
    """Alohida URL bo‘lmasa — expense webhook (event=payment.*) ishlatiladi."""
    dedicated = (getattr(settings, 'ERP_ISOMERIX_PAYMENT_WEBHOOK_URL', None) or '').strip()
    return dedicated or _webhook_url()


def _bearer() -> str:
    return (getattr(settings, 'ERP_ISOMERIX_BEARER_TOKEN', None) or '').strip()


def _timeout() -> float:
    return float(getattr(settings, 'ERP_ISOMERIX_TIMEOUT_SEC', 8) or 8)


def is_configured() -> bool:
    return bool(_webhook_url() and _bearer())


def external_id_for_movement(movement_id: int) -> str:
    return f'chefpro-sm-{int(movement_id)}'


def external_id_for_payment(payment_id: int) -> str:
    return f'chefpro-pay-{int(payment_id)}'


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
            'is_paid': not bool(getattr(movement, 'is_credit', False)),
            'is_debt': bool(getattr(movement, 'is_credit', False)),
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


def _payment_payload(payment, event: str = 'created') -> dict[str, Any]:
    return {
        'schema_version': 1,
        'source': 'chefpro',
        'event': f'payment.{event}',
        'payload': {
            'external_id': external_id_for_payment(payment.pk),
            'supplier_name': payment.supplier.name,
            'amount': str(payment.amount),
            'paid_on': payment.paid_on.isoformat(),
            'notes': (payment.note or '').strip() or f'ChefPro to‘lov #{payment.pk}',
            'payment_id': payment.pk,
            'supplier_id': payment.supplier_id,
        },
    }


def _payment_delete_payload(payment_id: int) -> dict[str, Any]:
    return {
        'schema_version': 1,
        'source': 'chefpro',
        'event': 'payment.deleted',
        'payload': {'external_id': external_id_for_payment(payment_id)},
    }


def _post_json(body: dict[str, Any], *, url: str | None = None) -> None:
    target = (url or _webhook_url()).strip()
    if not target:
        return
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        target,
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


def push_payment(payment, event: str = 'created') -> None:
    if not is_configured():
        return
    try:
        _post_json(_payment_payload(payment, event), url=_payment_webhook_url())
    except (urllib.error.URLError, TimeoutError, OSError):
        logger.exception('ERP Isomerix payment webhook failed (payment %s)', payment.pk)


def push_payment_deleted(payment_id: int) -> None:
    if not is_configured():
        return
    try:
        _post_json(_payment_delete_payload(payment_id), url=_payment_webhook_url())
    except (urllib.error.URLError, TimeoutError, OSError):
        logger.exception(
            'ERP Isomerix payment delete webhook failed (payment %s)', payment_id
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


def schedule_push_payment(payment_id: int, event: str = 'created') -> None:
    def run():
        from kitchen.models import SupplierPayment

        try:
            payment = SupplierPayment.objects.select_related('supplier').get(pk=payment_id)
        except SupplierPayment.DoesNotExist:
            logger.warning('ERP payment push skipped: payment %s not found', payment_id)
            return
        logger.info('ERP payment push start payment=%s event=%s', payment_id, event)
        push_payment(payment, event=event)

    if connection.in_atomic_block:
        transaction.on_commit(run)
    else:
        run()


def schedule_push_payment_deleted(payment_id: int) -> None:
    def run():
        push_payment_deleted(payment_id)

    if connection.in_atomic_block:
        transaction.on_commit(run)
    else:
        run()
