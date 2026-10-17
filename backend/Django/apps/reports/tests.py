"""
Unit tests for Reports module.
"""

from datetime import datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from .models import Report
from apps.authentication.models import User
from apps.water_level.models import WaterLevelReading


class ReportModelTest(TestCase):
    """Test cases for Report model."""
    
    def setUp(self):
        self.report = Report.objects.create(
            report_type='daily',
            start_date=timezone.now() - timezone.timedelta(days=1),
            end_date=timezone.now(),
            file_format='pdf',
            record_count=100,
            generated_by=User.objects.create_user(
                username='admin',
                email='admin@example.com',
                password='adminpass123',
                role='admin'
            )
        )
    
    def test_report_creation(self):
        """Test report creation."""
        self.assertEqual(self.report.report_type, 'daily')
        self.assertEqual(self.report.file_format, 'pdf')
        self.assertEqual(self.report.record_count, 100)
    
    def test_report_str(self):
        """Test report string representation."""
        str_repr = str(self.report)
        self.assertIn('daily', str_repr)
    
    def test_report_type_options(self):
        """Test report type options."""
        valid_types = ['daily', 'weekly', 'monthly', 'annual']
        for report_type in valid_types:
            report = Report.objects.create(
                report_type=report_type,
                start_date=timezone.now() - timezone.timedelta(days=1),
                end_date=timezone.now(),
                file_format='pdf',
                record_count=50,
                generated_by=self.report.generated_by
            )
            self.assertEqual(report.report_type, report_type)


class ReportsAPITest(TestCase):
    """Test cases for Reports API endpoints."""
    
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123',
            role='staff'
        )
        self.admin_user = User.objects.create_user(
            username='admin',
            email='admin@example.com',
            password='adminpass123',
            role='admin'
        )
        
        # Create test reports
        for i in range(3):
            Report.objects.create(
                report_type=['daily', 'weekly', 'monthly'][i],
                start_date=timezone.now() - timezone.timedelta(days=i+1),
                end_date=timezone.now(),
                file_format='pdf',
                record_count=100 * (i+1),
                generated_by=self.admin_user
            )
    
    def test_list_reports_unauthorized(self):
        """Test list reports without authentication."""
        response = self.client.get('/api/reports/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
    
    def test_list_reports_authorized(self):
        """Test list reports with authentication."""
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/reports/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)
    
    def test_create_report_admin_only(self):
        """Test create report requires admin."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post('/api/reports/create/', {
            'report_type': 'daily',
            'file_format': 'pdf'
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_create_report_admin_success(self):
        """Test create report by admin."""
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post('/api/reports/create/', {
            'report_type': 'daily',
            'file_format': 'pdf'
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
    
    def test_generate_report(self):
        """Test generate report endpoint."""
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/reports/generate/?type=daily&format=pdf')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_statistics(self):
        """Test report statistics."""
        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/reports/statistics/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('total_reports', response.data)

    def test_report_data_applies_month_week_and_threshold_aggregations(self):
        self.client.force_authenticate(user=self.user)
        selected_date = timezone.localdate().replace(day=3)
        selected_timestamp = timezone.make_aware(
            datetime.combine(selected_date, time(12)),
            timezone.get_current_timezone(),
        )
        for level in (1.5, 2.5, 3.5):
            WaterLevelReading.objects.create(
                water_level_cm=level,
                status='Normal',
                timestamp=selected_timestamp,
            )
        WaterLevelReading.objects.create(
            water_level_cm=2.0,
            status='Normal',
            timestamp=timezone.make_aware(
                datetime.combine(selected_date + timedelta(days=6), time(12)),
                timezone.get_current_timezone(),
            ),
        )

        response = self.client.get('/api/reports/data/', {
            'year': selected_date.year,
            'month': selected_date.month,
            'week': 1,
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['record_count'], 3)
        self.assertEqual(response.data['average_level'], 2.5)
        self.assertEqual(response.data['minimum_level'], 1.5)
        self.assertEqual(response.data['maximum_level'], 3.5)
        self.assertEqual(response.data['status_counts'], {
            'Normal': 1,
            'Alert': 0,
            'Warning': 1,
            'Danger': 1,
        })
        self.assertEqual(response.data['flood_alert_count'], 2)
        self.assertEqual(response.data['daily_summaries'][0]['average_level'], 2.5)

    def test_report_data_and_filtered_export_use_the_same_date_range(self):
        self.client.force_authenticate(user=self.user)
        selected_date = timezone.localdate().replace(day=3)
        timestamp = timezone.make_aware(
            datetime.combine(selected_date, time.min),
            timezone.get_current_timezone(),
        )
        WaterLevelReading.objects.bulk_create([
            WaterLevelReading(
                water_level_cm=2.5,
                status='Normal',
                timestamp=timestamp + timedelta(minutes=index),
            )
            for index in range(30)
        ])
        filters = {
            'start_date': selected_date.isoformat(),
            'end_date': selected_date.isoformat(),
        }

        data_response = self.client.get('/api/reports/data/', filters)
        export_response = self.client.get(
            '/api/reports/export/',
            {**filters, 'file_format': 'csv'},
        )

        self.assertEqual(data_response.status_code, status.HTTP_200_OK)
        self.assertEqual(data_response.data['record_count'], 30)
        self.assertEqual(export_response.status_code, status.HTTP_200_OK)
        self.assertIn(b'Timestamp,Water Level (cm),Status,Threshold Band', export_response.content)
        self.assertNotIn(b'Average (cm)', export_response.content)
        self.assertEqual(len(export_response.content.decode().splitlines()), 37)
        self.assertIn(b',Warning,Warning / Moderate,', export_response.content)
        self.assertIn(b'2.50', export_response.content)

    def test_export_with_no_readings_contains_no_telemetry_rows(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get('/api/reports/export/', {'file_format': 'csv'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(b'Latest 0 recorded telemetry readings', response.content)
        self.assertIn(b'Timestamp,Water Level (cm),Status,Threshold Band', response.content)
        self.assertEqual(len(response.content.decode().splitlines()), 7)

    def test_report_data_rejects_incomplete_date_ranges(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get('/api/reports/data/', {'start_date': '2026-01-01'})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
