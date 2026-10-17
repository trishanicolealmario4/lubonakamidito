#!/usr/bin/env python3
"""
AHON FloodWatch - Arduino serial bridge.

Reads JSON reading lines from the Arduino over USB serial and POSTs them to the
Django API. It sends SMS alerts to active opted-in residents through a SIM900
connected to the Arduino and records each attempt.

    Arduino --USB serial--> serial_bridge.py --HTTP--> Django
             |                         |
             +--SoftwareSerial--> SIM900

The Arduino has no clock, so this script stamps each reading with the host time.

Usage:
    # Set FLOODWATCH_USER and FLOODWATCH_PASSWORD in the environment first.
    python scripts/serial_bridge.py --port COM8
    python scripts/serial_bridge.py --simulate          # no hardware needed

Requires: pyserial, requests
"""

import argparse
import glob
import json
import logging
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger("serial_bridge")

# Serial devices an Arduino typically shows up as on macOS and Linux.
PORT_GLOBS = [
    "/dev/cu.usbmodem*",
    "/dev/cu.usbserial*",
    "/dev/cu.wchusbserial*",
    "/dev/ttyACM*",
    "/dev/ttyUSB*",
]

VALID_STATUSES = {"Normal", "Alert", "Warning", "Danger", "Critical"}
VALID_SENSOR_STATUSES = {"online", "offline", "error"}
VALID_GSM_STATUSES = {"connected", "disconnected", "error"}
DANGER_STATUSES = {"Danger", "Critical"}
ALERT_SEVERITY = {"Warning": 1, "Danger": 2, "Critical": 2}
MAX_SMS_RECIPIENTS = 3
EMERGENCY_MESSAGE = (
    "EMERGENCY FLOOD ALERT: High water level detected! Evacuate immediately."
)
PHONE_NUMBER_RE = re.compile(r"^\+[1-9]\d{7,14}$")


class BridgeConfig:
    """Calibration and reporting options shared by the line parsers."""

    def __init__(self, args):
        self.raw_dry = args.raw_dry
        self.raw_full = args.raw_full
        self.span_cm = args.span_cm
        self._gsm_mode = args.gsm_status
        self._gsm_seen = False
        self._modem_status = None

    @property
    def gsm_status(self):
        if self._gsm_mode != "auto":
            return self._gsm_mode
        return self._modem_status or ("connected" if self._gsm_seen else "disconnected")

    def set_modem_status(self, status):
        if status not in VALID_GSM_STATUSES:
            raise ValueError(f"Unsupported modem status: {status}")
        self._modem_status = status

    def note_line(self, line):
        """Track verified modem state reported by the single-port firmware."""
        match = GSM_STATUS_RE.search(line)
        if match and self._gsm_mode == "auto":
            self._modem_status = match.group(1).lower()
            self._gsm_seen = self._modem_status == "connected"

    def resolve_gsm_status(self, payload_status):
        """Prefer an explicit CLI override, otherwise use device status or detection."""
        if self._gsm_mode != "auto":
            return self._gsm_mode
        if self._modem_status:
            return self._modem_status
        if payload_status in VALID_GSM_STATUSES:
            return payload_status
        return self.gsm_status


class APIClient:
    """Thin client that keeps a JWT alive and communicates with the API."""

    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.access = None
        self.refresh = None

    def _authorization_headers(self):
        return {"Authorization": f"Bearer {self.access}"}

    def login(self):
        resp = requests.post(
            f"{self.base_url}/api/auth/login/",
            json={"username": self.username, "password": self.password},
            timeout=10,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Login failed ({resp.status_code}). Check --username/--password. {resp.text[:200]}"
            )
        payload = resp.json()
        self.access = payload["access"]
        self.refresh = payload.get("refresh")
        logger.info("Authenticated with %s as %s", self.base_url, self.username)

    def _refresh_access(self):
        """Re-authenticate.

        The backend exposes no /api/auth/token/refresh/ route, so the refresh
        token is unusable here and a fresh login is the only option.
        """
        self.login()

    def post_reading(self, reading):
        """POST one reading, retrying once after re-auth. Returns True on success."""
        url = f"{self.base_url}/api/water-level/readings/create/"

        for attempt in (1, 2):
            if not self.access:
                self.login()

            try:
                resp = requests.post(
                    url,
                    json=reading,
                    headers=self._authorization_headers(),
                    timeout=10,
                )
            except requests.RequestException as exc:
                logger.warning("POST failed: %s", exc)
                return False

            if resp.status_code in (200, 201):
                return True

            if resp.status_code == 401 and attempt == 1:
                logger.info("Token rejected, re-authenticating")
                self._refresh_access()
                continue

            logger.error("API rejected reading (%s): %s", resp.status_code, resp.text[:300])
            return False

        return False

    def get_sms_recipients(self):
        """Return every active resident who has opted in to SMS."""
        url = f"{self.base_url}/api/residents/"
        recipients = []
        page = 1

        while True:
            for attempt in (1, 2):
                if not self.access:
                    self.login()

                try:
                    resp = requests.get(
                        url,
                        params={
                            "status": "active",
                            "sms_enabled": "true",
                            "page": page,
                        },
                        headers=self._authorization_headers(),
                        timeout=10,
                    )
                except requests.RequestException as exc:
                    raise RuntimeError(f"Could not fetch active SMS residents: {exc}") from exc

                if resp.status_code == 401 and attempt == 1:
                    self._refresh_access()
                    continue
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Resident API rejected request ({resp.status_code}): {resp.text[:300]}"
                    )
                break
            else:
                raise RuntimeError("Resident API authentication failed after re-authentication.")

            try:
                payload = resp.json()
            except ValueError as exc:
                raise RuntimeError("Resident API returned invalid JSON.") from exc
            if isinstance(payload, dict):
                page_results = payload.get("results")
                has_next = bool(payload.get("next"))
                if not isinstance(page_results, list):
                    raise RuntimeError("Resident API returned an invalid paginated response.")
                if has_next and not page_results:
                    raise RuntimeError("Resident API pagination returned an empty page with more results.")
            elif isinstance(payload, list):
                page_results = payload
                has_next = False
            else:
                raise RuntimeError("Resident API returned an invalid response.")

            for resident in page_results:
                if not isinstance(resident, dict):
                    logger.warning("Ignoring invalid resident record from the API")
                    continue
                resident_id = resident.get("resident_id")
                mobile_number = resident.get("mobile_number")
                if not resident_id or not isinstance(mobile_number, str):
                    logger.warning("Ignoring resident response without an ID and mobile number")
                    continue
                if not PHONE_NUMBER_RE.fullmatch(mobile_number):
                    logger.warning("Resident %s has an invalid mobile number; logging the failed send", resident_id)
                recipients.append({
                    "resident_id": resident_id,
                    "mobile_number": mobile_number,
                })

            if not has_next:
                return recipients
            page += 1

    def post_sms_log(self, resident, message, delivery_status, error_message=None, broadcast_id=None):
        """Create one SMS log via the existing authenticated SMS API."""
        url = f"{self.base_url}/api/sms/create/"
        payload = {
            "resident_id": resident["resident_id"],
            "recipient": resident["mobile_number"],
            "message": message,
            "delivery_status": delivery_status,
            "error_message": error_message,
        }
        if broadcast_id:
            payload["broadcast_id"] = broadcast_id

        for attempt in (1, 2):
            try:
                if not self.access:
                    self.login()
            except (RuntimeError, requests.RequestException) as exc:
                logger.error("Could not authenticate to create SMS log: %s", exc)
                return False

            try:
                resp = requests.post(
                    url,
                    json=payload,
                    headers=self._authorization_headers(),
                    timeout=10,
                )
            except requests.RequestException as exc:
                logger.error("Could not log SMS attempt for a resident: %s", exc)
                return False

            if resp.status_code in (200, 201):
                return True
            if resp.status_code == 401 and attempt == 1:
                try:
                    self._refresh_access()
                except (RuntimeError, requests.RequestException) as exc:
                    logger.error("Could not re-authenticate to create SMS log: %s", exc)
                    return False
                continue

            logger.error("SMS log API rejected request (%s): %s", resp.status_code, resp.text[:300])
            return False

        return False

    def claim_manual_broadcast(self):
        """Claim one queued admin broadcast for this running bridge."""
        url = f"{self.base_url}/api/sms/manual-broadcasts/claim/"
        for attempt in (1, 2):
            if not self.access:
                self.login()
            try:
                response = requests.post(
                    url,
                    json={},
                    headers=self._authorization_headers(),
                    timeout=10,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"Could not check for manual SMS broadcasts: {exc}") from exc
            if response.status_code == 401 and attempt == 1:
                self._refresh_access()
                continue
            if response.status_code == 204:
                return None
            if response.status_code != 200:
                raise RuntimeError(
                    f"Manual broadcast API rejected claim ({response.status_code}): "
                    f"{response.text[:300]}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError("Manual broadcast API returned invalid JSON.") from exc
            if not all(payload.get(field) for field in ("broadcast_id", "source", "message")):
                raise RuntimeError("Manual broadcast API returned an incomplete broadcast.")
            return payload
        raise RuntimeError("Manual broadcast authentication failed after re-authentication.")

    def complete_manual_broadcast(self, broadcast_id, sent_count, failed_count, error_message=""):
        """Persist final modem outcomes for an admin-triggered broadcast."""
        url = f"{self.base_url}/api/sms/manual-broadcasts/{broadcast_id}/complete/"
        payload = {
            "sent_count": sent_count,
            "failed_count": failed_count,
            "error_message": error_message,
        }
        for attempt in (1, 2):
            if not self.access:
                self.login()
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=self._authorization_headers(),
                    timeout=10,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"Could not save manual SMS broadcast outcome: {exc}") from exc
            if response.status_code == 401 and attempt == 1:
                self._refresh_access()
                continue
            if response.status_code != 200:
                raise RuntimeError(
                    f"Manual broadcast API rejected completion ({response.status_code}): "
                    f"{response.text[:300]}"
                )
            return
        raise RuntimeError("Manual broadcast authentication failed after re-authentication.")


def autodetect_port():
    """Return the single obvious serial port, or None."""
    if sys.platform == "win32":
        from serial.tools import list_ports

        ports = list(list_ports.comports())
        usb_serial_markers = ("arduino", "usb serial", "usb-serial", "ch340", "cp210", "ftdi")
        candidates = [
            port for port in ports
            if any(
                marker in f"{port.description} {port.manufacturer or ''} {port.hwid}".lower()
                for marker in usb_serial_markers
            )
        ]
        if len(candidates) == 1:
            return candidates[0].device
        if candidates:
            logger.warning(
                "Multiple USB serial ports found: %s. Pass --port explicitly.",
                ", ".join(port.device for port in candidates),
            )
            return None
        if ports:
            logger.warning(
                "Serial ports found but no recognizable USB serial device: %s. Pass --port explicitly.",
                ", ".join(f"{port.device} ({port.description})" for port in ports),
            )
            return None

    found = []
    for pattern in PORT_GLOBS:
        found.extend(glob.glob(pattern))

    # Ignore the Bluetooth pseudo-ports macOS always exposes.
    found = [p for p in found if "Bluetooth" not in p]

    if not found:
        return None
    if len(found) > 1:
        logger.warning("Multiple serial ports found: %s", ", ".join(found))
    return sorted(found)[0]


# sketch_sep12a.ino prints "Current Sensor Value: 486" rather than JSON.
RAW_LINE_RE = re.compile(r"Current Sensor Value:\s*(\d+)")
GSM_STATUS_RE = re.compile(r'"gsm_status"\s*:\s*"(connected|disconnected|error)"')


def raw_to_cm(raw, raw_dry, raw_full, span_cm):
    """Linear map from the analog reading to centimetres of water."""
    span = raw_full - raw_dry
    if span <= 0:
        return 0.0
    level = (raw - raw_dry) * span_cm / span
    return round(max(0.0, min(span_cm, level)), 2)


def parse_line(line, cfg):
    """Turn one serial line into a reading dict, or None if it isn't one."""
    line = line.strip()
    if not line:
        return None

    if line.startswith("[DEBUG] Payload: "):
        line = line.partition(": ")[2]

    cfg.note_line(line)

    # The plain-text sketch reports a raw ADC value; convert it here.
    match = RAW_LINE_RE.search(line)
    if match:
        raw_value = int(match.group(1))
        if not cfg.raw_dry <= raw_value <= cfg.raw_full:
            logger.warning(
                "Ignoring raw sensor value outside calibration (%s-%s): %s",
                cfg.raw_dry,
                cfg.raw_full,
                raw_value,
            )
            return None
        level = raw_to_cm(raw_value, cfg.raw_dry, cfg.raw_full, cfg.span_cm)
        return build_reading(level, derive_status(level), "online", cfg)

    if not line.startswith("{"):
        # Status banners and boot noise are expected; ignore them quietly.
        return None

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        logger.debug("Ignoring unparsable line: %s", line[:120])
        return None

    if "water_level_cm" not in data:
        return None

    try:
        level = round(float(data["water_level_cm"]), 2)
    except (TypeError, ValueError):
        logger.warning("Ignoring non-numeric water_level_cm: %r", data.get("water_level_cm"))
        return None

    if math.isnan(level) or math.isinf(level) or not 0 <= level <= cfg.span_cm:
        logger.warning("Ignoring water level outside calibrated 0-%s cm range: %s", cfg.span_cm, level)
        return None

    status = data.get("status")
    if status == "Critical":
        status = "Danger"
    elif status not in VALID_STATUSES:
        # The API requires status, so derive it rather than letting the POST 400.
        status = derive_status(level)

    sensor_status = data.get("sensor_status")
    if sensor_status not in VALID_SENSOR_STATUSES:
        sensor_status = "online"

    return build_reading(level, status, sensor_status, cfg, data.get("gsm_status"))


def build_reading(level, status, sensor_status, cfg, gsm_status=None):
    return {
        "water_level_cm": level,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sensor_status": sensor_status,
        "gsm_status": cfg.resolve_gsm_status(gsm_status),
    }


def _wait_for_modem_response(modem, expected, timeout=10):
    """Read modem output until a prompt/response is confirmed or it times out."""
    deadline = time.monotonic() + timeout
    response = bytearray()
    expected = tuple(marker if isinstance(marker, bytes) else marker.encode() for marker in expected)

    while time.monotonic() < deadline:
        response.extend(modem.read(1))
        if b"ERROR" in response:
            raise RuntimeError(response.decode("ascii", errors="replace").strip())
        if any(marker in response for marker in expected):
            return response.decode("ascii", errors="replace")

    raise TimeoutError(
        f"Modem did not respond with {expected!r}: "
        f"{response.decode('ascii', errors='replace').strip()}"
    )


def initialize_gsm_modem(modem):
    """Verify that the modem responds and enable text-mode SMS."""
    modem.reset_input_buffer()
    modem.write(b"AT\r")
    _wait_for_modem_response(modem, (b"OK",), timeout=5)
    modem.write(b"AT+CMGF=1\r")
    _wait_for_modem_response(modem, (b"OK",), timeout=5)


def send_sms_command(modem, recipient, message):
    """Send one SMS and require the modem's +CMGS and OK confirmation."""
    if not PHONE_NUMBER_RE.fullmatch(recipient):
        raise ValueError("Recipient mobile number is not a valid international number.")

    modem.write(b"AT+CMGF=1\r")
    _wait_for_modem_response(modem, (b"OK",), timeout=5)
    modem.write(f'AT+CMGS="{recipient}"\r'.encode("ascii"))
    _wait_for_modem_response(modem, (b">",), timeout=10)
    modem.write(message.encode("ascii") + b"\x1a")
    _wait_for_modem_response(modem, (b"+CMGS:",), timeout=60)
    _wait_for_modem_response(modem, (b"OK",), timeout=10)
    modem.flush()


def dispatch_sms_to_residents(
    api_client,
    residents,
    gsm_port,
    gsm_baud,
    message,
    cfg=None,
    broadcast_id=None,
    sms_sender=None,
):
    """Send and log a message for up to three active SMS-enabled residents."""
    results = {"sent_count": 0, "failed_count": 0, "error_message": ""}
    if len(residents) > MAX_SMS_RECIPIENTS:
        logger.warning(
            "SMS recipient limit is %d; sending only to the first %d eligible residents.",
            MAX_SMS_RECIPIENTS,
            MAX_SMS_RECIPIENTS,
        )
        residents = residents[:MAX_SMS_RECIPIENTS]
    if not residents:
        results.update(failed_count=1, error_message="No active residents have SMS enabled.")
        return results

    def log_failures(error_message):
        for resident in residents:
            api_client.post_sms_log(
                resident, message, "failed", error_message[:1000], broadcast_id
            )
        results["failed_count"] = len(residents)
        results["error_message"] = error_message[:1000]

    if not gsm_port and sms_sender is None:
        error_message = "GSM modem port is not configured; pass --gsm-port or FLOODWATCH_GSM_PORT."
        logger.error("SMS broadcast cannot be sent: %s", error_message)
        if cfg:
            cfg.set_modem_status("error")
        log_failures(error_message)
        return results

    if not gsm_port:
        for resident in residents:
            try:
                sms_sender(resident["mobile_number"], message)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.error(
                    "SMS send failed for resident %s: %s",
                    resident["resident_id"],
                    exc,
                )
                if cfg:
                    cfg.set_modem_status("error")
                api_client.post_sms_log(
                    resident, message, "failed", str(exc)[:1000], broadcast_id
                )
                results["failed_count"] += 1
                continue

            if cfg:
                cfg.set_modem_status("connected")
            if api_client.post_sms_log(
                resident, message, "sent", broadcast_id=broadcast_id
            ):
                logger.info(
                    "SMS accepted by modem and logged for resident %s",
                    resident["resident_id"],
                )
            else:
                results["error_message"] = "One or more SMS logs could not be saved."
            results["sent_count"] += 1
        return results

    try:
        import serial
    except ImportError as exc:
        error_message = f"pyserial is required for GSM SMS dispatch: {exc}"
        logger.error(error_message)
        if cfg:
            cfg.set_modem_status("error")
        log_failures(error_message)
        return results

    try:
        with serial.Serial(gsm_port, gsm_baud, timeout=1, write_timeout=5) as modem:
            try:
                initialize_gsm_modem(modem)
            except (OSError, RuntimeError) as exc:
                error_message = f"GSM modem initialization failed: {exc}"
                logger.error(error_message)
                if cfg:
                    cfg.set_modem_status("error")
                log_failures(error_message)
                return results
            if cfg:
                cfg.set_modem_status("connected")
            for resident in residents:
                try:
                    send_sms_command(modem, resident["mobile_number"], message)
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.error("SMS send failed for resident %s: %s", resident["resident_id"], exc)
                    api_client.post_sms_log(
                        resident,
                        message,
                        "failed",
                        str(exc)[:1000],
                        broadcast_id,
                    )
                    results["failed_count"] += 1
                    continue

                if api_client.post_sms_log(
                    resident, message, "sent", broadcast_id=broadcast_id
                ):
                    logger.info("SMS accepted by modem and logged for resident %s", resident["resident_id"])
                else:
                    results["error_message"] = "One or more SMS logs could not be saved."
                results["sent_count"] += 1
        return results
    except (OSError, RuntimeError) as exc:
        error_message = f"GSM modem initialization failed: {exc}"
        logger.error(error_message)
        if cfg:
            cfg.set_modem_status("error")
        if results["sent_count"] or results["failed_count"]:
            results["error_message"] = error_message[:1000]
            return results
        log_failures(error_message)
        return results


def dispatch_flood_alert_sms(
    api_client,
    gsm_port,
    gsm_baud,
    water_level_cm,
    alert_status,
    cfg=None,
    sms_sender=None,
):
    """Send and log a Warning or Danger alert for every SMS-enabled resident."""
    if alert_status == "Warning":
        message = (
            f"FLOOD WARNING: Water level has reached {water_level_cm:.2f} cm. "
            "Please stay alert and prepare to evacuate if instructed."
        )
    elif alert_status in DANGER_STATUSES:
        message = f"{EMERGENCY_MESSAGE} Water level: {water_level_cm:.2f} cm."
    else:
        raise ValueError(f"Unsupported automatic flood alert status: {alert_status}")

    try:
        residents = api_client.get_sms_recipients()
    except (RuntimeError, requests.RequestException) as exc:
        logger.error(
            "Could not load SMS recipients; %s alert was not sent: %s",
            alert_status,
            exc,
        )
        return

    if not residents:
        logger.warning(
            "%s alert was not sent: no active residents have SMS enabled",
            alert_status,
        )
        return

    return dispatch_sms_to_residents(
        api_client,
        residents,
        gsm_port,
        gsm_baud,
        message,
        cfg,
        sms_sender=sms_sender,
    )


def dispatch_danger_sms(api_client, gsm_port, gsm_baud, water_level_cm, cfg=None):
    """Send and log one Danger alert for each active SMS-enabled resident."""
    return dispatch_flood_alert_sms(
        api_client, gsm_port, gsm_baud, water_level_cm, "Danger", cfg
    )


def dispatch_pending_manual_broadcast(
    api_client, gsm_port, gsm_baud, cfg=None, sms_sender=None
):
    """Claim and dispatch one admin-requested SMS broadcast, if one is queued."""
    try:
        broadcast = api_client.claim_manual_broadcast()
    except (RuntimeError, requests.RequestException) as exc:
        logger.error("Could not poll manual SMS broadcast queue: %s", exc)
        return
    if broadcast is None:
        return

    broadcast_id = broadcast["broadcast_id"]
    source = broadcast["source"].strip()
    message = f"OFFICIAL FLOOD WARNING FROM {source}: {broadcast['message'].strip()}"
    try:
        residents = api_client.get_sms_recipients()
        result = dispatch_sms_to_residents(
            api_client,
            residents,
            gsm_port,
            gsm_baud,
            message,
            cfg,
            broadcast_id,
            sms_sender,
        )
    except (RuntimeError, requests.RequestException) as exc:
        logger.error("Could not dispatch manual broadcast %s: %s", broadcast_id, exc)
        result = {
            "sent_count": 0,
            "failed_count": 1,
            "error_message": f"Could not load SMS recipients: {exc}"[:1000],
        }

    try:
        api_client.complete_manual_broadcast(
            broadcast_id,
            result["sent_count"],
            result["failed_count"],
            result["error_message"],
        )
    except (RuntimeError, requests.RequestException) as exc:
        logger.error("Could not save outcome for manual broadcast %s: %s", broadcast_id, exc)
        return
    logger.info(
        "Manual broadcast %s finished: modem accepted %d, failed %d",
        broadcast_id,
        result["sent_count"],
        result["failed_count"],
    )


def should_post_reading(reading_status, last_post, last_status, min_interval, now):
    """Post at the normal interval, or immediately when telemetry status changes."""
    return reading_status != last_status or now - last_post >= min_interval


def is_new_flood_alert_transition(
    reading_status, sensor_status, last_status, last_sensor_status
):
    """Return whether a valid reading has entered a higher SMS alert level."""
    reading_severity = ALERT_SEVERITY.get(reading_status, 0)
    last_severity = (
        ALERT_SEVERITY.get(last_status, 0)
        if last_sensor_status == "online"
        else 0
    )
    return (
        reading_severity > 0
        and reading_severity > last_severity
        and sensor_status == "online"
    )


def is_new_danger_transition(reading_status, sensor_status, last_status, last_sensor_status):
    """Return whether this valid sensor reading has just entered Danger."""
    return (
        reading_status in DANGER_STATUSES
        and is_new_flood_alert_transition(
            reading_status, sensor_status, last_status, last_sensor_status
        )
    )


# Matches the Arduino ADC thresholds converted to centimetres for the 4 cm strip.
def derive_status(level, warning=2.0, danger=3.0):
    if level >= danger:
        return "Danger"
    if level >= warning:
        return "Warning"
    return "Normal"


def simulated_lines(interval, cfg):
    """Yield Arduino-shaped JSON lines without any hardware attached."""
    level = 1.0
    while True:
        level = max(0.0, min(cfg.span_cm, level + random.uniform(-0.4, 0.45)))
        yield json.dumps(
            {
                "water_level_cm": round(level, 2),
                "status": derive_status(level),
                "sensor_status": "online",
                "raw": int(level * 7),
            }
        )
        time.sleep(interval)


class SerialLineSource:
    """Keep the Arduino port open for both telemetry and SMS passthrough."""

    def __init__(self, port, baud, reconnect_delay):
        import serial  # imported here so --simulate works without pyserial

        self._serial = serial
        self.port = port
        self.baud = baud
        self.reconnect_delay = reconnect_delay
        self.connection = None
        self.pending_lines = []
        self._opened = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.pending_lines:
            return self.pending_lines.pop(0)

        if self.connection is None:
            try:
                logger.info("Opening %s at %d baud", self.port, self.baud)
                self.connection = self._serial.Serial(
                    self.port, self.baud, timeout=1, write_timeout=5
                )
                # Opening an Uno resets it; allow the bootloader and modem setup to finish.
                time.sleep(3)
                self.connection.reset_input_buffer()
                if not self._opened:
                    logger.info("Listening for readings (Ctrl+C to stop)")
                    self._opened = True
            except self._serial.SerialException as exc:
                self._close_connection()
                logger.error("Serial error: %s", exc)
                logger.info("Reconnecting in %ss", self.reconnect_delay)
                time.sleep(self.reconnect_delay)
                return ""

        try:
            raw = self.connection.readline()
        except self._serial.SerialException as exc:
            self._close_connection()
            logger.error("Serial error: %s", exc)
            logger.info("Reconnecting in %ss", self.reconnect_delay)
            time.sleep(self.reconnect_delay)
            return ""
        return raw.decode("utf-8", errors="replace") if raw else ""

    def send_sms(self, recipient, message):
        """Ask the Arduino to send one SMS through its attached GSM modem."""
        if self.connection is None:
            raise RuntimeError("Arduino serial connection is not open.")
        if not PHONE_NUMBER_RE.fullmatch(recipient):
            raise ValueError("Recipient mobile number is not a valid international number.")

        message = message.replace("\r", " ").replace("\n", " ")
        try:
            encoded_message = message.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("SMS message must contain ASCII characters only.") from exc
        if any(character < 32 or character > 126 for character in encoded_message):
            raise ValueError("SMS message must use printable ASCII characters only.")
        if len(encoded_message) > 480:
            raise ValueError("SMS message must not exceed 480 ASCII characters.")
        command = b"SMS|" + recipient.encode("ascii") + b"|" + encoded_message + b"\n"
        if len(command) > 511:
            raise ValueError("SMS command exceeds the Arduino serial buffer.")

        try:
            self.connection.write(command)
            self.connection.flush()
            deadline = time.monotonic() + 75
            while time.monotonic() < deadline:
                raw = self.connection.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if line == "@SMS_RESULT:OK":
                    return
                if line.startswith("@SMS_RESULT:ERROR:"):
                    raise RuntimeError(line.partition(":ERROR:")[2])
                self.pending_lines.append(line)
        except self._serial.SerialException as exc:
            self._close_connection()
            raise RuntimeError(f"Arduino serial connection failed during SMS: {exc}") from exc
        raise TimeoutError("Arduino did not return an SMS result within 75 seconds.")

    def _close_connection(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def close(self):
        self._close_connection()


def serial_lines(port, baud, reconnect_delay):
    """Create the shared serial connection for readings and SMS passthrough."""
    return SerialLineSource(port, baud, reconnect_delay)


def main():
    parser = argparse.ArgumentParser(
        description="Bridge Arduino water level readings into the AHON FloodWatch API.",
    )
    parser.add_argument("--port", help="Serial port (auto-detected if omitted)")
    parser.add_argument("--baud", type=int, default=9600, help="Baud rate (default: 9600)")
    parser.add_argument(
        "--api",
        default=os.environ.get("FLOODWATCH_API", "http://localhost:8000"),
        help="Django base URL (default: http://localhost:8000)",
    )
    parser.add_argument("--username", default=os.environ.get("FLOODWATCH_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("FLOODWATCH_PASSWORD"))
    parser.add_argument(
        "--min-interval",
        type=float,
        default=10.0,
        help="Minimum seconds between stored readings (default: 10)",
    )
    parser.add_argument(
        "--reconnect-delay", type=float, default=5.0, help="Seconds to wait before reopening the port"
    )
    parser.add_argument(
        "--raw-dry", type=int, default=0, help="analogRead value with the sensor dry (default: 0)"
    )
    parser.add_argument(
        "--raw-full", type=int, default=600,
        help="analogRead value at full submersion (default: 600)",
    )
    parser.add_argument(
        "--span-cm", type=float, default=4.0,
        help="Centimetres of water represented by --raw-full (default: 4.0)",
    )
    parser.add_argument(
        "--gsm-status", choices=("auto", "connected", "disconnected"), default="auto",
        help="GSM state override; auto uses status reported by the Arduino firmware (default: auto)",
    )
    parser.add_argument(
        "--gsm-port",
        default=os.environ.get("FLOODWATCH_GSM_PORT"),
        help="Optional separate SIM900 serial port; omit when SIM900 is wired to Arduino pins 8/9",
    )
    parser.add_argument(
        "--gsm-baud",
        type=int,
        default=int(os.environ.get("FLOODWATCH_GSM_BAUD", "9600")),
        help="SIM900 serial baud rate (default: 9600)",
    )
    parser.add_argument("--simulate", action="store_true", help="Generate fake readings, no hardware")
    parser.add_argument("--once", action="store_true", help="Post a single reading and exit")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.password:
        parser.error("--password is required (or set FLOODWATCH_PASSWORD)")

    cfg = BridgeConfig(args)
    if cfg.raw_full <= cfg.raw_dry or cfg.span_cm <= 0:
        parser.error("--raw-full must exceed --raw-dry and --span-cm must be positive")
    client = APIClient(args.api, args.username, args.password)
    try:
        client.login()
    except (RuntimeError, requests.RequestException) as exc:
        logger.error("%s", exc)
        logger.error("Is the backend running?  cd backend/Django && .venv/bin/python manage.py runserver 8000")
        return 1

    if args.simulate:
        source = simulated_lines(min(args.min_interval, 5.0), cfg)
    else:
        port = args.port or autodetect_port()
        if not port:
            logger.error("No serial port found. Plug in the Arduino, or pass --port explicitly.")
            logger.error("Ports are usually /dev/cu.usbmodem* on macOS, /dev/ttyACM* on Linux.")
            return 1
        source = serial_lines(port, args.baud, args.reconnect_delay)

    posted = 0
    skipped = 0
    last_post = 0.0
    last_broadcast_poll = 0.0
    last_status = None
    last_sensor_status = None

    try:
        for line in source:
            poll_time = time.monotonic()
            if not args.simulate and poll_time - last_broadcast_poll >= 3.0:
                dispatch_pending_manual_broadcast(
                    client,
                    args.gsm_port,
                    args.gsm_baud,
                    cfg,
                    None if args.gsm_port else getattr(source, "send_sms", None),
                )
                last_broadcast_poll = time.monotonic()

            reading = parse_line(line, cfg)
            if reading is None:
                continue

            # Throttle: the sketch reads faster than the DB needs rows.
            now = time.monotonic()
            if not args.once and not should_post_reading(
                reading["status"], last_post, last_status, args.min_interval, now
            ):
                skipped += 1
                continue

            if client.post_reading(reading):
                alert_status = reading["status"] if is_new_flood_alert_transition(
                    reading["status"],
                    reading["sensor_status"],
                    last_status,
                    last_sensor_status,
                ) else None
                last_post = now
                last_status = reading["status"]
                last_sensor_status = reading["sensor_status"]
                posted += 1
                logger.info(
                    "Stored %.2f cm (%s)  [posted=%d skipped=%d]",
                    reading["water_level_cm"],
                    reading["status"],
                    posted,
                    skipped,
                )
                if alert_status:
                    if args.simulate:
                        logger.info("Skipping SMS dispatch for simulated telemetry")
                    else:
                        dispatch_flood_alert_sms(
                            client,
                            args.gsm_port,
                            args.gsm_baud,
                            reading["water_level_cm"],
                            alert_status,
                            cfg,
                            None if args.gsm_port else getattr(source, "send_sms", None),
                        )
                if args.once:
                    return 0

    except KeyboardInterrupt:
        logger.info("Stopped. %d readings posted, %d throttled.", posted, skipped)
    finally:
        if not args.simulate:
            source.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
