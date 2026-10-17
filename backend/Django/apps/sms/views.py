"""
Views for SMS Logs module.
"""
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.utils import timezone
from django.db import transaction
from datetime import timedelta
from apps.authentication.permissions import IsAdminRole
from apps.residents.models import Resident
from .models import SMSBroadcast, SMSLog
from .serializers import (
    SMSLogSerializer,
    SMSLogCreateSerializer
)


class SMSLogListView(generics.ListAPIView):
    """
    API endpoint to list all SMS logs.
    """
    queryset = SMSLog.objects.select_related('resident', 'alert').all()
    serializer_class = SMSLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['delivery_status', 'alert', 'resident']
    search_fields = ['recipient', 'message']
    ordering_fields = ['sent_at', 'delivery_status']
    ordering = ['-sent_at']


class SMSLogDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    API endpoint to retrieve, update or delete an SMS log.
    """
    queryset = SMSLog.objects.select_related('resident', 'alert').all()
    serializer_class = SMSLogSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'sms_id'
    
    def perform_update(self, serializer):
        # Log activity
        from apps.activity_logs.utils import log_activity
        log_activity(
            user=self.request.user,
            action='update',
            entity='sms_log',
            entity_id=str(self.get_object().sms_id),
            details=serializer.validated_data
        )
        serializer.save()
    
    def perform_destroy(self, instance):
        # Log activity
        from apps.activity_logs.utils import log_activity
        log_activity(
            user=self.request.user,
            action='delete',
            entity='sms_log',
            entity_id=str(instance.sms_id),
            details={'recipient': instance.recipient}
        )
        instance.delete()


class SMSLogCreateView(generics.CreateAPIView):
    """
    API endpoint to create an SMS log.
    """
    queryset = SMSLog.objects.all()
    serializer_class = SMSLogCreateSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def perform_create(self, serializer):
        # Log activity
        from apps.activity_logs.utils import log_activity
        log_activity(
            user=self.request.user,
            action='create',
            entity='sms_log',
            details=serializer.validated_data
        )
        serializer.save()


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated, IsAdminRole])
def claim_manual_broadcast_view(request):
    with transaction.atomic():
        broadcast = (
            SMSBroadcast.objects.select_for_update()
            .filter(status='pending')
            .order_by('created_at')
            .first()
        )
        if broadcast is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        broadcast.status = 'sending'
        broadcast.started_at = timezone.now()
        broadcast.save(update_fields=['status', 'started_at'])

    return Response({
        'broadcast_id': str(broadcast.broadcast_id),
        'source': broadcast.source,
        'message': broadcast.message,
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated, IsAdminRole])
def complete_manual_broadcast_view(request, broadcast_id):
    sent_count = request.data.get('sent_count')
    failed_count = request.data.get('failed_count')
    if (
        type(sent_count) is not int
        or type(failed_count) is not int
        or sent_count < 0
        or failed_count < 0
    ):
        return Response(
            {'detail': 'sent_count and failed_count must be non-negative integers.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        broadcast = SMSBroadcast.objects.get(broadcast_id=broadcast_id)
    except SMSBroadcast.DoesNotExist:
        return Response({'detail': 'Broadcast not found.'}, status=status.HTTP_404_NOT_FOUND)
    if broadcast.status != 'sending':
        return Response(
            {'detail': 'Only a claimed broadcast can be completed.'},
            status=status.HTTP_409_CONFLICT,
        )

    broadcast.sent_count = sent_count
    broadcast.failed_count = failed_count
    broadcast.error_message = str(request.data.get('error_message', ''))[:1000]
    if sent_count and failed_count:
        broadcast.status = 'partial'
    elif sent_count:
        broadcast.status = 'completed'
    else:
        broadcast.status = 'failed'
    broadcast.completed_at = timezone.now()
    broadcast.save(update_fields=[
        'sent_count', 'failed_count', 'error_message', 'status', 'completed_at'
    ])
    return Response({
        'broadcast_id': str(broadcast.broadcast_id),
        'status': broadcast.status,
        'sent_count': broadcast.sent_count,
        'failed_count': broadcast.failed_count,
        'error_message': broadcast.error_message,
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated, IsAdminRole])
def manual_broadcast_detail_view(request, broadcast_id):
    try:
        broadcast = SMSBroadcast.objects.get(broadcast_id=broadcast_id)
    except SMSBroadcast.DoesNotExist:
        return Response({'detail': 'Broadcast not found.'}, status=status.HTTP_404_NOT_FOUND)
    return Response({
        'broadcast_id': str(broadcast.broadcast_id),
        'source': broadcast.source,
        'status': broadcast.status,
        'recipient_count': broadcast.recipient_count,
        'sent_count': broadcast.sent_count,
        'failed_count': broadcast.failed_count,
        'error_message': broadcast.error_message,
        'created_at': broadcast.created_at,
        'completed_at': broadcast.completed_at,
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated, IsAdminRole])
def create_manual_broadcast_view(request):
    if not isinstance(request.data, dict):
        return Response(
            {'detail': 'A JSON object with source and message is required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    source = request.data.get('source', '')
    message = request.data.get('message', '')
    if not isinstance(source, str) or not source.strip() or len(source.strip()) > 120:
        return Response(
            {'source': ['Enter an announcement source of 1 to 120 characters.']},
            status=status.HTTP_400_BAD_REQUEST,
        )
    source = source.strip()
    if not source.isascii() or any(not character.isprintable() for character in source):
        return Response(
            {'source': ['Use plain ASCII text without control characters for SMS.']},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not isinstance(message, str) or not message.strip() or len(message.strip()) > 480:
        return Response(
            {'message': ['Enter a warning message of 1 to 480 characters.']},
            status=status.HTTP_400_BAD_REQUEST,
        )
    message = message.strip()
    if not message.isascii() or any(not character.isprintable() for character in message):
        return Response(
            {'message': ['Use plain ASCII text without control characters for SMS.']},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if len(f'OFFICIAL FLOOD WARNING FROM {source}: {message}') > 480:
        return Response(
            {'message': ['The complete SMS including its source must be 480 characters or fewer.']},
            status=status.HTTP_400_BAD_REQUEST,
        )

    recipient_count = Resident.objects.filter(status='active', sms_enabled=True).count()
    if recipient_count == 0:
        return Response(
            {'detail': 'There are no active residents with SMS alerts enabled.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from apps.activity_logs.utils import log_activity
    with transaction.atomic():
        broadcast = SMSBroadcast.objects.create(
            requested_by=request.user,
            source=source,
            message=message,
            recipient_count=recipient_count,
        )
        log_activity(
            user=request.user,
            action='create',
            entity='sms_broadcast',
            entity_id=str(broadcast.broadcast_id),
            details={
                'source': broadcast.source,
                'recipient_count': recipient_count,
            },
        )
    return Response({
        'broadcast_id': str(broadcast.broadcast_id),
        'status': broadcast.status,
        'recipient_count': recipient_count,
    }, status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def sms_statistics_view(request):
    """
    API endpoint to get SMS statistics.
    Query parameters:
    - hours: Number of hours to look back (default: 24)
    """
    hours = request.query_params.get('hours', 24)
    cutoff_time = timezone.now() - timedelta(hours=int(hours))
    
    logs = SMSLog.objects.filter(sent_at__gte=cutoff_time)
    
    total_sent = logs.count()
    
    # Count by delivery status
    status_counts = {}
    for status_choice in SMSLog.DELIVERY_STATUS_CHOICES:
        status_name = status_choice[0]
        count = logs.filter(delivery_status=status_name).count()
        status_counts[status_name] = count
    
    data = {
        'total_sent': total_sent,
        'status_counts': status_counts,
        'time_range_hours': int(hours)
    }
    
    return Response(data)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def failed_sms_view(request):
    """
    API endpoint to get failed SMS logs.
    """
    failed_logs = SMSLog.objects.filter(
        delivery_status='failed'
    ).order_by('-sent_at')
    
    serializer = SMSLogSerializer(failed_logs, many=True)
    return Response(serializer.data)
