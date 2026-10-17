"""
Admin registration for SMS Logs module.
"""
from django.contrib import admin

from .models import SMSBroadcast, SMSLog


@admin.register(SMSBroadcast)
class SMSBroadcastAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'source', 'status', 'recipient_count',
        'sent_count', 'failed_count', 'requested_by'
    )
    list_filter = ('status',)
    search_fields = ('source', 'message')
    ordering = ('-created_at',)
    readonly_fields = (
        'broadcast_id', 'requested_by', 'source', 'message', 'status',
        'recipient_count', 'sent_count', 'failed_count', 'error_message',
        'created_at', 'started_at', 'completed_at'
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SMSLog)
class SMSLogAdmin(admin.ModelAdmin):
    list_display = ('sent_at', 'recipient', 'delivery_status', 'resident', 'alert')
    list_filter = ('delivery_status',)
    search_fields = ('recipient', 'message')
    date_hierarchy = 'sent_at'
    ordering = ('-sent_at',)
    readonly_fields = ('sms_id', 'sent_at')
