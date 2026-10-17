"""
SMS Logs models for tracking SMS messages.
"""
from django.db import models
from django.conf import settings
from apps.alerts.models import FloodAlert
from apps.residents.models import Resident
import uuid


class SMSBroadcast(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sending', 'Sending'),
        ('completed', 'Completed'),
        ('partial', 'Partially completed'),
        ('failed', 'Failed'),
    ]

    broadcast_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sms_broadcasts',
    )
    source = models.CharField(max_length=120)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    recipient_count = models.PositiveIntegerField(default=0)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'sms_broadcasts'
        ordering = ['-created_at']


class SMSLog(models.Model):
    """
    Model to store SMS message logs.
    """
    DELIVERY_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('failed', 'Failed'),
    ]
    
    sms_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    broadcast = models.ForeignKey(
        SMSBroadcast,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sms_logs',
    )
    alert = models.ForeignKey(FloodAlert, on_delete=models.SET_NULL, null=True, blank=True, related_name='sms_logs')
    resident = models.ForeignKey(Resident, on_delete=models.CASCADE, related_name='sms_logs')
    recipient = models.CharField(max_length=20)
    message = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    delivery_status = models.CharField(max_length=20, choices=DELIVERY_STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'sms_logs'
        verbose_name = 'SMS Log'
        verbose_name_plural = 'SMS Logs'
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['sent_at']),
            models.Index(fields=['delivery_status']),
            models.Index(fields=['resident']),
            models.Index(fields=['alert']),
        ]
    
    def __str__(self):
        return f"SMS to {self.recipient} - {self.delivery_status} at {self.sent_at}"
