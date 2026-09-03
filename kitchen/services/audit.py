from kitchen.models import AuditLog


def log_action(user, action, entity, entity_id=None, detail=''):
    AuditLog.objects.create(
        user=user if user and user.is_authenticated else None,
        action=action,
        entity=entity,
        entity_id=entity_id,
        detail=detail,
    )
