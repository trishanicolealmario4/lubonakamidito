"""
Signal handlers for Water Level readings.

Escalating reading statuses creates dashboard notifications. SMS delivery is
handled by the serial bridge using the GSM modem and records each real attempt.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import WaterLevelReading

logger = logging.getLogger(__name__)


def notify_users_on_status_change(instance):
    from apps.authentication.models import User
    from apps.notifications.models import Notification

    previous = (
        WaterLevelReading.objects
        .exclude(pk=instance.pk)
        .order_by('-timestamp')
        .first()
    )
    if previous is not None and previous.status == instance.status:
        return
    if previous is None and instance.status == 'Normal':
        return

    display_status = 'Critical' if instance.status == 'Danger' else instance.status
    previous_status = 'Critical' if previous and previous.status == 'Danger' else previous.status if previous else 'No previous status'
    notification_type = {
        'Normal': 'success',
        'Warning': 'warning',
        'Danger': 'danger',
    }.get(instance.status, 'info')
    title = f'Water level status: {display_status}'
    message = (
        f'Water level changed from {previous_status} to {display_status}. '
        f'Current reading: {instance.water_level_cm} cm.'
    )
    users = User.objects.filter(is_active=True)
    Notification.objects.bulk_create([
        Notification(
            user=user,
            title=title,
            message=message,
            type=notification_type,
        )
        for user in users
    ])


@receiver(post_save, sender=WaterLevelReading, dispatch_uid='water_level_status_notification')
def notify_users_on_reading_created(sender, instance, created, **kwargs):
    if not created:
        return

    try:
        notify_users_on_status_change(instance)
    except Exception:
        logger.exception('Failed to create water-level notification for reading %s', instance.reading_id)
