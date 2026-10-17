"""
URL configuration for SMS Logs module.
"""
from django.urls import path
from .views import (
    SMSLogListView,
    SMSLogDetailView,
    SMSLogCreateView,
    claim_manual_broadcast_view,
    complete_manual_broadcast_view,
    manual_broadcast_detail_view,
    sms_statistics_view,
    failed_sms_view
)

urlpatterns = [
    path('', SMSLogListView.as_view(), name='sms_log_list'),
    path('<uuid:sms_id>/', SMSLogDetailView.as_view(), name='sms_log_detail'),
    path('create/', SMSLogCreateView.as_view(), name='sms_log_create'),
    path('manual-broadcasts/claim/', claim_manual_broadcast_view, name='manual_broadcast_claim'),
    path('manual-broadcasts/<uuid:broadcast_id>/', manual_broadcast_detail_view, name='manual_broadcast_detail'),
    path('manual-broadcasts/<uuid:broadcast_id>/complete/', complete_manual_broadcast_view, name='manual_broadcast_complete'),
    path('statistics/', sms_statistics_view, name='sms_statistics'),
    path('failed/', failed_sms_view, name='failed_sms'),
]
