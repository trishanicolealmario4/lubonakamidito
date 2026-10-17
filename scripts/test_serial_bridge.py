import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.serial_bridge import (
    APIClient,
    BridgeConfig,
    dispatch_danger_sms,
    dispatch_flood_alert_sms,
    dispatch_pending_manual_broadcast,
    dispatch_sms_to_residents,
    is_new_danger_transition,
    is_new_flood_alert_transition,
    parse_line,
    raw_to_cm,
    send_sms_command,
    serial_lines,
)


class FakeModem:
    def __init__(self, fail_send=False):
        self.response = bytearray()
        self.writes = []
        self.fail_send = fail_send

    def reset_input_buffer(self):
        self.response.clear()

    def write(self, payload):
        self.writes.append(payload)
        if payload == b"AT\r" or payload == b"AT+CMGF=1\r":
            self.response.extend(b"\r\nOK\r\n")
        elif payload.startswith(b'AT+CMGS="'):
            self.response.extend(b"\r\n>")
        elif payload.endswith(b"\x1a"):
            if self.fail_send:
                self.response.extend(b"\r\n+CMS ERROR: 500\r\n")
            else:
                self.response.extend(b"\r\n+CMGS: 42\r\nOK\r\n")

    def read(self, size):
        if not self.response:
            return b""
        value = bytes(self.response[:size])
        del self.response[:size]
        return value

    def flush(self):
        pass


class SerialBridgeCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.config = BridgeConfig(SimpleNamespace(
            raw_dry=0,
            raw_full=600,
            span_cm=4.0,
            gsm_status="disconnected",
        ))

    def test_raw_conversion_clamps_to_calibrated_span(self):
        self.assertEqual(raw_to_cm(900, 0, 600, 4.0), 4.0)

    def test_json_reading_above_calibrated_span_is_ignored(self):
        line = json.dumps({"water_level_cm": 4.26, "status": "Danger"})

        self.assertIsNone(parse_line(line, self.config))

    def test_reading_at_calibrated_limit_is_preserved(self):
        line = json.dumps({"water_level_cm": 4.0, "status": "Danger"})

        reading = parse_line(line, self.config)

        self.assertEqual(reading["water_level_cm"], 4.0)
        self.assertEqual(reading["status"], "Danger")

    def test_critical_telemetry_is_normalized_to_danger(self):
        line = json.dumps({"water_level_cm": 3.5, "status": "Critical"})

        reading = parse_line(line, self.config)

        self.assertEqual(reading["status"], "Danger")

    def test_modem_status_telemetry_updates_bridge_status(self):
        self.assertIsNone(parse_line('{"gsm_status":"disconnected"}', self.config))

        self.assertEqual(self.config.gsm_status, "disconnected")

    def test_danger_sms_is_sent_only_on_valid_sensor_transition(self):
        self.assertTrue(is_new_danger_transition("Danger", "online", "Warning", "online"))
        self.assertFalse(is_new_danger_transition("Danger", "online", "Danger", "online"))
        self.assertFalse(is_new_danger_transition("Danger", "error", "Warning", "online"))
        self.assertTrue(is_new_danger_transition("Danger", "online", "Danger", "error"))

    def test_warning_and_danger_sms_only_send_on_upward_valid_transitions(self):
        self.assertTrue(is_new_flood_alert_transition("Warning", "online", "Normal", "online"))
        self.assertFalse(is_new_flood_alert_transition("Warning", "online", "Warning", "online"))
        self.assertTrue(is_new_flood_alert_transition("Danger", "online", "Warning", "online"))
        self.assertFalse(is_new_flood_alert_transition("Warning", "online", "Danger", "online"))
        self.assertFalse(is_new_flood_alert_transition("Warning", "error", "Normal", "online"))

    def test_raw_reading_above_calibrated_sensor_is_ignored(self):
        self.assertIsNone(parse_line("Current Sensor Value: 601", self.config))

    def test_serial_timeout_yields_control_for_manual_broadcast_polling(self):
        class IdleSerial:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def reset_input_buffer(self):
                pass

            def readline(self):
                return b""

            def close(self):
                pass

        serial_stub = SimpleNamespace(
            SerialException=OSError,
            Serial=IdleSerial,
        )
        with patch.dict("sys.modules", {"serial": serial_stub}), patch(
            "scripts.serial_bridge.time.sleep"
        ):
            source = serial_lines("COM8", 9600, 1)
            self.assertEqual(next(source), "")
        source.close()

    def test_single_port_source_sends_command_and_waits_for_modem_confirmation(self):
        class FakeConnection:
            def __init__(self):
                self.writes = []
                self.responses = [
                    b'{"water_level_cm":2.1}\n',
                    b'{"water_level_cm":2.2}\n',
                    b"@SMS_RESULT:OK\n",
                ]

            def reset_input_buffer(self):
                pass

            def write(self, payload):
                self.writes.append(payload)

            def flush(self):
                pass

            def readline(self):
                return self.responses.pop(0)

            def close(self):
                pass

        connection = FakeConnection()
        serial_stub = SimpleNamespace(
            SerialException=OSError,
            Serial=lambda *args, **kwargs: connection,
        )
        with patch.dict("sys.modules", {"serial": serial_stub}), patch(
            "scripts.serial_bridge.time.sleep"
        ):
            source = serial_lines("COM8", 9600, 1)
            next(source)
            source.send_sms(
                "+639123456789",
                "FLOOD WARNING: Water is rising.",
            )

        self.assertEqual(
            connection.writes,
            [b"SMS|+639123456789|FLOOD WARNING: Water is rising.\n"],
        )
        self.assertEqual(next(source), '{"water_level_cm":2.2}')
        source.close()


class SMSBridgeTests(unittest.TestCase):
    def test_resident_fetch_follows_all_pages_and_filters_opted_in_active_residents(self):
        client = APIClient("http://localhost:8000", "bridge", "secret")
        client.access = "access-token"
        responses = [
            SimpleNamespace(
                status_code=200,
                text="",
                json=lambda: {
                    "results": [{
                        "resident_id": "resident-1",
                        "mobile_number": "+639123456789",
                    }],
                    "next": "http://localhost:8000/api/residents/?page=2",
                },
            ),
            SimpleNamespace(
                status_code=200,
                text="",
                json=lambda: {
                    "results": [{
                        "resident_id": "resident-2",
                        "mobile_number": "+639987654321",
                    }],
                    "next": None,
                },
            ),
        ]

        with patch("scripts.serial_bridge.requests.get", side_effect=responses) as get:
            residents = client.get_sms_recipients()

        self.assertEqual(len(residents), 2)
        self.assertEqual(residents[0]["resident_id"], "resident-1")
        self.assertEqual(get.call_args_list[0].kwargs["params"]["status"], "active")
        self.assertEqual(get.call_args_list[0].kwargs["params"]["sms_enabled"], "true")
        self.assertEqual(get.call_args_list[1].kwargs["params"]["page"], 2)

    def test_sms_log_uses_existing_django_api_payload(self):
        client = APIClient("http://localhost:8000", "bridge", "secret")
        client.access = "access-token"
        resident = {
            "resident_id": "resident-1",
            "mobile_number": "+639123456789",
        }
        response = SimpleNamespace(status_code=201, text="")

        with patch("scripts.serial_bridge.requests.post", return_value=response) as post:
            logged = client.post_sms_log(
                resident,
                "EMERGENCY FLOOD ALERT",
                "sent",
            )

        self.assertTrue(logged)
        self.assertEqual(post.call_args.args[0], "http://localhost:8000/api/sms/create/")
        self.assertEqual(post.call_args.kwargs["json"]["resident_id"], "resident-1")
        self.assertEqual(post.call_args.kwargs["json"]["delivery_status"], "sent")

    def test_sms_log_can_link_to_manual_broadcast(self):
        client = APIClient("http://localhost:8000", "bridge", "secret")
        client.access = "access-token"
        response = SimpleNamespace(status_code=201, text="")
        resident = {
            "resident_id": "resident-1",
            "mobile_number": "+639123456789",
        }

        with patch("scripts.serial_bridge.requests.post", return_value=response) as post:
            logged = client.post_sms_log(
                resident,
                "OFFICIAL FLOOD WARNING",
                "sent",
                broadcast_id="broadcast-1",
            )

        self.assertTrue(logged)
        self.assertEqual(post.call_args.kwargs["json"]["broadcast_id"], "broadcast-1")

    def test_manual_broadcast_claim_and_completion_use_api(self):
        client = APIClient("http://localhost:8000", "bridge", "secret")
        client.access = "access-token"
        claim_response = SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {
                "broadcast_id": "broadcast-1",
                "source": "PAGASA",
                "message": "Flood advisory issued.",
            },
        )
        complete_response = SimpleNamespace(status_code=200, text="")

        with patch(
            "scripts.serial_bridge.requests.post",
            side_effect=[claim_response, complete_response],
        ) as post:
            broadcast = client.claim_manual_broadcast()
            client.complete_manual_broadcast("broadcast-1", 2, 1, "one failure")

        self.assertEqual(broadcast["source"], "PAGASA")
        self.assertTrue(post.call_args_list[0].args[0].endswith("/api/sms/manual-broadcasts/claim/"))
        self.assertEqual(post.call_args_list[1].kwargs["json"]["failed_count"], 1)

    def test_sms_command_requires_modem_acceptance(self):
        modem = FakeModem()

        send_sms_command(
            modem,
            "+639123456789",
            "EMERGENCY FLOOD ALERT: Evacuate immediately.",
        )

        self.assertIn(b'AT+CMGS="+639123456789"\r', modem.writes)
        self.assertIn(b"EMERGENCY FLOOD ALERT: Evacuate immediately.\x1a", modem.writes)

    def test_sms_command_rejects_modem_error_response(self):
        modem = FakeModem(fail_send=True)

        with self.assertRaises(RuntimeError):
            send_sms_command(modem, "+639123456789", "EMERGENCY FLOOD ALERT")

    def test_danger_alert_sends_to_and_logs_every_recipient(self):
        residents = [
            {"resident_id": "resident-1", "mobile_number": "+639123456789"},
            {"resident_id": "resident-2", "mobile_number": "+639987654321"},
        ]
        api_client = SimpleNamespace(
            get_sms_recipients=lambda: residents,
            post_sms_log=Mock(return_value=True),
        )
        modem = FakeModem()
        serial_stub = SimpleNamespace(
            Serial=lambda *args, **kwargs: _FakeModemContext(modem),
        )

        with patch.dict("sys.modules", {"serial": serial_stub}), patch(
            "scripts.serial_bridge.initialize_gsm_modem"
        ) as initialize, patch(
            "scripts.serial_bridge.send_sms_command"
        ) as send:
            dispatch_danger_sms(api_client, "COM9", 9600, 3.5)

        initialize.assert_called_once_with(modem)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(api_client.post_sms_log.call_count, 2)
        self.assertTrue(all(call.args[2] == "sent" for call in api_client.post_sms_log.call_args_list))

    def test_warning_alert_sends_warning_message_to_every_recipient(self):
        residents = [
            {"resident_id": "resident-1", "mobile_number": "+639123456789"},
            {"resident_id": "resident-2", "mobile_number": "+639987654321"},
        ]
        api_client = SimpleNamespace(
            get_sms_recipients=lambda: residents,
            post_sms_log=Mock(return_value=True),
        )
        modem = FakeModem()
        serial_stub = SimpleNamespace(
            Serial=lambda *args, **kwargs: _FakeModemContext(modem),
        )

        with patch.dict("sys.modules", {"serial": serial_stub}), patch(
            "scripts.serial_bridge.initialize_gsm_modem"
        ), patch("scripts.serial_bridge.send_sms_command") as send:
            dispatch_flood_alert_sms(api_client, "COM9", 9600, 2.5, "Warning")

        self.assertEqual(send.call_count, 2)
        self.assertTrue(all(
            call.args[2].startswith("FLOOD WARNING: Water level has reached 2.50 cm.")
            for call in send.call_args_list
        ))
        self.assertEqual(api_client.post_sms_log.call_count, 2)

    def test_warning_alert_uses_single_port_sms_sender_for_every_recipient(self):
        residents = [
            {"resident_id": "resident-1", "mobile_number": "+639123456789"},
            {"resident_id": "resident-2", "mobile_number": "+639987654321"},
        ]
        api_client = SimpleNamespace(
            get_sms_recipients=lambda: residents,
            post_sms_log=Mock(return_value=True),
        )
        send_sms = Mock()

        result = dispatch_flood_alert_sms(
            api_client,
            None,
            9600,
            2.5,
            "Warning",
            sms_sender=send_sms,
        )

        self.assertEqual(result["sent_count"], 2)
        self.assertEqual(send_sms.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in send_sms.call_args_list],
            ["+639123456789", "+639987654321"],
        )
        self.assertEqual(api_client.post_sms_log.call_count, 2)

    def test_sms_dispatch_limits_recipients_to_first_three(self):
        residents = [
            {"resident_id": f"resident-{index}", "mobile_number": f"+63912345678{index}"}
            for index in range(1, 5)
        ]
        api_client = SimpleNamespace(
            post_sms_log=Mock(return_value=True),
        )
        send_sms = Mock()

        result = dispatch_sms_to_residents(
            api_client,
            residents,
            None,
            9600,
            "FLOOD WARNING",
            sms_sender=send_sms,
        )

        self.assertEqual(result["sent_count"], 3)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(send_sms.call_count, 3)
        self.assertEqual(api_client.post_sms_log.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in send_sms.call_args_list],
            [resident["mobile_number"] for resident in residents[:3]],
        )

    def test_manual_broadcast_sends_source_and_message_without_sensor_status(self):
        residents = [
            {"resident_id": "resident-1", "mobile_number": "+639123456789"},
        ]
        api_client = SimpleNamespace(
            claim_manual_broadcast=lambda: {
                "broadcast_id": "broadcast-1",
                "source": "PAGASA",
                "message": "Stay alert.",
            },
            get_sms_recipients=lambda: residents,
            post_sms_log=Mock(return_value=True),
            complete_manual_broadcast=Mock(),
        )
        modem = FakeModem()
        serial_stub = SimpleNamespace(
            Serial=lambda *args, **kwargs: _FakeModemContext(modem),
        )

        with patch.dict("sys.modules", {"serial": serial_stub}), patch(
            "scripts.serial_bridge.initialize_gsm_modem"
        ), patch("scripts.serial_bridge.send_sms_command") as send:
            dispatch_pending_manual_broadcast(api_client, "COM9", 9600)

        send.assert_called_once()
        self.assertEqual(
            send.call_args.args[2],
            "OFFICIAL FLOOD WARNING FROM PAGASA: Stay alert.",
        )
        self.assertEqual(api_client.post_sms_log.call_args.kwargs["broadcast_id"], "broadcast-1")
        api_client.complete_manual_broadcast.assert_called_once_with(
            "broadcast-1", 1, 0, ""
        )

    def test_manual_broadcast_uses_single_port_sms_sender(self):
        resident = {
            "resident_id": "resident-1",
            "mobile_number": "+639123456789",
        }
        api_client = SimpleNamespace(
            claim_manual_broadcast=lambda: {
                "broadcast_id": "broadcast-1",
                "source": "PAGASA",
                "message": "Stay alert.",
            },
            get_sms_recipients=lambda: [resident],
            post_sms_log=Mock(return_value=True),
            complete_manual_broadcast=Mock(),
        )
        send_sms = Mock()

        dispatch_pending_manual_broadcast(
            api_client, None, 9600, sms_sender=send_sms
        )

        send_sms.assert_called_once_with(
            "+639123456789",
            "OFFICIAL FLOOD WARNING FROM PAGASA: Stay alert.",
        )
        api_client.complete_manual_broadcast.assert_called_once_with(
            "broadcast-1", 1, 0, ""
        )

    def test_manual_broadcast_without_modem_is_completed_as_failed(self):
        residents = [
            {"resident_id": "resident-1", "mobile_number": "+639123456789"},
        ]
        api_client = SimpleNamespace(
            claim_manual_broadcast=lambda: {
                "broadcast_id": "broadcast-1",
                "source": "Barangay Officials",
                "message": "Stay alert.",
            },
            get_sms_recipients=lambda: residents,
            post_sms_log=Mock(return_value=True),
            complete_manual_broadcast=Mock(),
        )

        dispatch_pending_manual_broadcast(api_client, None, 9600)

        self.assertEqual(api_client.post_sms_log.call_args.args[2], "failed")
        self.assertEqual(api_client.post_sms_log.call_args.args[4], "broadcast-1")
        api_client.complete_manual_broadcast.assert_called_once()
        self.assertEqual(api_client.complete_manual_broadcast.call_args.args[1], 0)
        self.assertEqual(api_client.complete_manual_broadcast.call_args.args[2], 1)

    def test_no_modem_logs_unsent_alerts_for_each_recipient(self):
        residents = [
            {"resident_id": "resident-1", "mobile_number": "+639123456789"},
            {"resident_id": "resident-2", "mobile_number": "+639987654321"},
        ]
        api_client = SimpleNamespace(
            get_sms_recipients=lambda: residents,
            post_sms_log=Mock(return_value=True),
        )

        dispatch_danger_sms(api_client, None, 9600, 3.5)

        self.assertEqual(api_client.post_sms_log.call_count, 2)
        self.assertTrue(all(call.args[2] == "failed" for call in api_client.post_sms_log.call_args_list))

    def test_failed_recipient_is_logged_and_next_resident_is_still_attempted(self):
        residents = [
            {"resident_id": "resident-1", "mobile_number": "+639123456789"},
            {"resident_id": "resident-2", "mobile_number": "+639987654321"},
        ]
        api_client = SimpleNamespace(
            get_sms_recipients=lambda: residents,
            post_sms_log=Mock(return_value=True),
        )
        modem = FakeModem()
        serial_stub = SimpleNamespace(
            Serial=lambda *args, **kwargs: _FakeModemContext(modem),
        )

        with patch.dict("sys.modules", {"serial": serial_stub}), patch(
            "scripts.serial_bridge.initialize_gsm_modem"
        ), patch(
            "scripts.serial_bridge.send_sms_command",
            side_effect=[RuntimeError("modem rejected message"), None],
        ):
            dispatch_danger_sms(api_client, "COM9", 9600, 3.5)

        self.assertEqual(
            [call.args[2] for call in api_client.post_sms_log.call_args_list],
            ["failed", "sent"],
        )


class _FakeModemContext:
    def __init__(self, modem):
        self.modem = modem

    def __enter__(self):
        return self.modem

    def __exit__(self, exc_type, exc_value, traceback):
        return False


if __name__ == "__main__":
    unittest.main()