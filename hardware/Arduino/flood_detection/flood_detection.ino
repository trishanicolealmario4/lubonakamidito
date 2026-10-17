/*
 * AHON FloodWatch sensor, indicator, and SIM900 passthrough firmware.
 * Streams JSON telemetry over USB serial and relays bridge SMS commands
 * to a SIM900/SIM800 modem connected through SoftwareSerial.
 */

#include <SoftwareSerial.h>
#include <string.h>

const int WATER_LEVEL_SENSOR_PIN = A0;
// SIM900 TX -> D7 (Arduino RX); SIM900 RX -> D8 (Arduino TX).
const int GSM_RX_PIN = 7;
const int GSM_TX_PIN = 8;
const int LED_GREEN_PIN = 13;
const int LED_WARNING_PIN = 12;
const int LED_DANGER_PIN = 11;

const int RAW_DRY = 0;
const int RAW_FULL = 600;
const float SENSOR_SPAN_CM = 4.0f;
const float WARNING_THRESHOLD_CM = 2.0f;
const float DANGER_THRESHOLD_CM = 3.0f;
const int SAMPLE_COUNT = 10;
const unsigned long READING_INTERVAL_MS = 5000;
const unsigned long MODEM_RESPONSE_TIMEOUT_MS = 10000;
const unsigned long GSM_RETRY_INTERVAL_MS = 10000;
const size_t USB_COMMAND_BUFFER_SIZE = 512;
const size_t SMS_MESSAGE_MAX_LENGTH = 480;

SoftwareSerial gsmSerial(GSM_RX_PIN, GSM_TX_PIN);
unsigned long lastReadingTime = 0;
bool gsmReady = false;
unsigned long lastGsmInitAttempt = 0;
char usbCommandBuffer[USB_COMMAND_BUFFER_SIZE];
size_t usbCommandLength = 0;
bool usbCommandOverflow = false;

void setup() {
  Serial.begin(9600);
  pinMode(WATER_LEVEL_SENSOR_PIN, INPUT);
  pinMode(LED_GREEN_PIN, OUTPUT);
  pinMode(LED_WARNING_PIN, OUTPUT);
  pinMode(LED_DANGER_PIN, OUTPUT);

  updateIndicators("Normal");
  delay(500);
  gsmReady = initializeGsmModem();
  lastGsmInitAttempt = millis();
  publishGsmStatus();
}

void loop() {
  processUsbCommands();

  unsigned long now = millis();
  if (!gsmReady && now - lastGsmInitAttempt >= GSM_RETRY_INTERVAL_MS) {
    lastGsmInitAttempt = now;
    gsmReady = initializeGsmModem();
    publishGsmStatus();
  }

  if (now - lastReadingTime < READING_INTERVAL_MS) {
    return;
  }
  lastReadingTime = now;

  int raw = readRawAveraged();
  float waterLevelCm = rawToCentimetres(raw);
  const char *status = determineStatus(waterLevelCm);
  const char *sensorStatus = raw <= RAW_FULL ? "online" : "error";

  updateIndicators(status);
  publishReading(waterLevelCm, status, sensorStatus, gsmReady ? "connected" : "disconnected");
}

int readRawAveraged() {
  long total = 0;
  for (int index = 0; index < SAMPLE_COUNT; index++) {
    total += analogRead(WATER_LEVEL_SENSOR_PIN);
    delay(5);
  }

  return (int)(total / SAMPLE_COUNT);
}

float rawToCentimetres(int raw) {
  int rawSpan = RAW_FULL - RAW_DRY;
  if (rawSpan <= 0) {
    return 0.0f;
  }

  float level = (float)(raw - RAW_DRY) * SENSOR_SPAN_CM / (float)rawSpan;
  if (level < 0.0f) {
    return 0.0f;
  }
  if (level > SENSOR_SPAN_CM) {
    return SENSOR_SPAN_CM;
  }
  return level;
}

const char *determineStatus(float waterLevelCm) {
  if (waterLevelCm >= DANGER_THRESHOLD_CM) {
    return "Danger";
  }
  if (waterLevelCm >= WARNING_THRESHOLD_CM) {
    return "Warning";
  }
  return "Normal";
}

void updateIndicators(const char *status) {
  bool danger = strcmp(status, "Danger") == 0;
  bool warning = strcmp(status, "Warning") == 0;
  digitalWrite(LED_GREEN_PIN, danger || warning ? LOW : HIGH);
  digitalWrite(LED_WARNING_PIN, warning ? HIGH : LOW);
  digitalWrite(LED_DANGER_PIN, danger ? HIGH : LOW);
}

void publishGsmStatus() {
  Serial.print(F("{\"gsm_status\":\""));
  Serial.print(gsmReady ? F("connected") : F("disconnected"));
  Serial.println(F("\"}"));
}

void publishReading(
  float waterLevelCm,
  const char *status,
  const char *sensorStatus,
  const char *gsmStatus
) {
  Serial.print(F("{\"water_level_cm\":"));
  Serial.print(waterLevelCm, 2);
  Serial.print(F(",\"status\":\""));
  Serial.print(status);
  Serial.print(F("\",\"sensor_status\":\""));
  Serial.print(sensorStatus);
  Serial.print(F("\",\"gsm_status\":\""));
  Serial.print(gsmStatus);
  Serial.println(F("\"}"));
}

void clearGsmBuffer() {
  while (gsmSerial.available() > 0) {
    gsmSerial.read();
  }
}

bool waitForGsmResponse(const char *expected, unsigned long timeout) {
  char response[64];
  size_t responseLength = 0;
  response[0] = '\0';
  unsigned long startedAt = millis();

  while (millis() - startedAt < timeout) {
    while (gsmSerial.available() > 0) {
      char character = (char)gsmSerial.read();
      if (responseLength >= sizeof(response) - 1) {
        memmove(response, response + 1, sizeof(response) - 2);
        responseLength = sizeof(response) - 2;
      }
      response[responseLength++] = character;
      response[responseLength] = '\0';

      if (strstr(response, "ERROR") != NULL) {
        return false;
      }
      if (strstr(response, expected) != NULL) {
        return true;
      }
    }
  }
  return false;
}

bool initializeGsmModem() {
  const unsigned long baudRates[] = {9600, 19200};
  for (size_t rateIndex = 0; rateIndex < sizeof(baudRates) / sizeof(baudRates[0]); rateIndex++) {
    gsmSerial.begin(baudRates[rateIndex]);
    gsmSerial.listen();
    delay(300);
    for (int attempt = 0; attempt < 3; attempt++) {
      clearGsmBuffer();
      gsmSerial.println(F("AT"));
      if (waitForGsmResponse("OK", 2000)) {
        clearGsmBuffer();
        gsmSerial.println(F("AT+CMGF=1"));
        if (waitForGsmResponse("OK", 5000)) {
          return true;
        }
      }
      delay(300);
    }
  }
  return false;
}

bool isValidPhoneNumber(const char *phone) {
  size_t length = strlen(phone);
  if (phone[0] != '+' || length < 9 || length > 16) {
    return false;
  }
  for (size_t index = 1; index < length; index++) {
    if (phone[index] < '0' || phone[index] > '9') {
      return false;
    }
  }
  return true;
}

bool sendHardwareSms(const char *recipient, const char *message) {
  if (!gsmReady) {
    gsmReady = initializeGsmModem();
    publishGsmStatus();
    if (!gsmReady) {
      return false;
    }
  }

  gsmSerial.listen();
  clearGsmBuffer();
  gsmSerial.println(F("AT+CMGF=1"));
  if (!waitForGsmResponse("OK", 5000)) {
    return false;
  }

  gsmSerial.print(F("AT+CMGS=\""));
  gsmSerial.print(recipient);
  gsmSerial.println(F("\""));
  if (!waitForGsmResponse(">", MODEM_RESPONSE_TIMEOUT_MS)) {
    return false;
  }

  gsmSerial.print(message);
  gsmSerial.write((uint8_t)26);
  if (!waitForGsmResponse("+CMGS:", 60000) || !waitForGsmResponse("OK", 10000)) {
    return false;
  }
  return true;
}

void reportSmsError(const char *reason) {
  Serial.print(F("@SMS_RESULT:ERROR:"));
  Serial.println(reason);
}

void processSmsCommand(char *command) {
  if (strncmp(command, "SMS|", 4) != 0) {
    reportSmsError("unknown-command");
    return;
  }

  char *recipient = command + 4;
  char *separator = strchr(recipient, '|');
  if (separator == NULL) {
    reportSmsError("invalid-command");
    return;
  }
  *separator = '\0';
  const char *message = separator + 1;
  if (!isValidPhoneNumber(recipient)) {
    reportSmsError("invalid-recipient");
    return;
  }
  if (strlen(message) == 0 || strlen(message) > SMS_MESSAGE_MAX_LENGTH) {
    reportSmsError("invalid-message-length");
    return;
  }

  if (!sendHardwareSms(recipient, message)) {
    reportSmsError(gsmReady ? "modem-did-not-confirm-send" : "modem-not-ready");
    return;
  }
  Serial.println(F("@SMS_RESULT:OK"));
}

void processUsbCommands() {
  while (Serial.available() > 0) {
    char character = (char)Serial.read();
    if (character == '\r') {
      continue;
    }
    if (character != '\n') {
      if (usbCommandLength < sizeof(usbCommandBuffer) - 1) {
        usbCommandBuffer[usbCommandLength++] = character;
      } else {
        usbCommandOverflow = true;
      }
      continue;
    }

    if (usbCommandOverflow) {
      reportSmsError("command-too-long");
    } else if (usbCommandLength > 0) {
      usbCommandBuffer[usbCommandLength] = '\0';
      processSmsCommand(usbCommandBuffer);
    }
    usbCommandLength = 0;
    usbCommandOverflow = false;
  }
}
