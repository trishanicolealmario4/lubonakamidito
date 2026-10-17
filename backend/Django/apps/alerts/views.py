"""
Views for Flood Alerts module.
"""
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from apps.sms.views import create_manual_broadcast_view
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.utils import timezone
from datetime import timedelta
from .models import FloodAlert
from .serializers import (
    FloodAlertSerializer,
    FloodAlertCreateSerializer
)


class FloodAlertListView(generics.ListAPIView):
    """
    API endpoint to list all flood alerts.
    """
    queryset = FloodAlert.objects.select_related('reading').all()
    serializer_class = FloodAlertSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['alert_level', 'is_active']
    search_fields = ['message', 'alert_level']
    ordering_fields = ['timestamp', 'alert_level']
    ordering = ['-timestamp']


class FloodAlertDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    API endpoint to retrieve, update or delete a flood alert.
    """
    queryset = FloodAlert.objects.select_related('reading').all()
    serializer_class = FloodAlertSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'alert_id'
    
    def perform_update(self, serializer):
        # Log activity
        from apps.activity_logs.utils import log_activity
        log_activity(
            user=self.request.user,
            action='update',
            entity='flood_alert',
            entity_id=str(self.get_object().alert_id),
            details=serializer.validated_data
        )
        serializer.save()
    
    def perform_destroy(self, instance):
        # Log activity
        from apps.activity_logs.utils import log_activity
        log_activity(
            user=self.request.user,
            action='delete',
            entity='flood_alert',
            entity_id=str(instance.alert_id),
            details={'alert_level': instance.alert_level}
        )
        instance.delete()


class FloodAlertCreateView(generics.CreateAPIView):
    """
    API endpoint to create a flood alert.
    """
    queryset = FloodAlert.objects.all()
    serializer_class = FloodAlertCreateSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def perform_create(self, serializer):
        # Log activity
        from apps.activity_logs.utils import log_activity
        log_activity(
            user=self.request.user,
            action='create',
            entity='flood_alert',
            details=serializer.validated_data
        )
        serializer.save()


send_manual_warning_view = create_manual_broadcast_view


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def active_alerts_view(request):
    """
    API endpoint to get all active flood alerts.
    """
    alerts = FloodAlert.objects.filter(is_active=True).order_by('-timestamp')
    serializer = FloodAlertSerializer(alerts, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def recent_alerts_view(request):
    """
    API endpoint to get recent flood alerts.
    Query parameters:
    - hours: Number of hours to look back (default: 24)
    """
    hours = request.query_params.get('hours', 24)
    cutoff_time = timezone.now() - timedelta(hours=int(hours))
    
    alerts = FloodAlert.objects.filter(
        timestamp__gte=cutoff_time
    ).order_by('-timestamp')
    
    serializer = FloodAlertSerializer(alerts, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def alert_statistics_view(request):
    """
    API endpoint to get flood alert statistics.
    """
    total_alerts = FloodAlert.objects.count()
    active_alerts = FloodAlert.objects.filter(is_active=True).count()
    
    # Count by alert level
    level_counts = {}
    for level_choice in FloodAlert.ALERT_LEVEL_CHOICES:
        level_name = level_choice[0]
        count = FloodAlert.objects.filter(alert_level=level_name).count()
        level_counts[level_name] = count
    
    data = {
        'total_alerts': total_alerts,
        'active_alerts': active_alerts,
        'level_counts': level_counts
    }
    
    return Response(data)
