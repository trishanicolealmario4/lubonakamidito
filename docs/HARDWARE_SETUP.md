# Hardware Setup Guide

## Table of Contents

1. [Hardware Components](#hardware-components)
2. [Wiring Diagram](#wiring-diagram)
3. [Assembly Instructions](#assembly-instructions)
4. [Firmware Installation](#firmware-installation)
5. [Configuration](#configuration)
6. [Testing](#testing)
7. [Troubleshooting](#troubleshooting)
8. [Maintenance](#maintenance)

## About AHON FloodWatch Hardware

This guide covers the hardware setup for AHON FloodWatch, the flood detection and monitoring system for Barangay Tonsuya, Malabon.

## Hardware Components

### Required Components

1. **Arduino Uno** - Microcontroller
2. **Water Level Sensor** - Analog water level sensor
3. **SIM900/SIM800 GSM Module** - SMS modem connected to Arduino pins D7/D8 through SoftwareSerial
4. **SIM Card** - With active SMS service
5. **Solar Panel** - 5V 10W (for power)
6. **Rechargeable Battery** - 12V 10Ah Li-ion or similar
7. **Solar Charge Controller** - PWM or MPPT
8. **Breadboard and Jumper Wires** - For connections
9. **LED (Optional)** - Status indicator
10. **Buzzer (Optional)** - Audio alert

### Optional Components

1. **Waterproof Enclosure** - For outdoor installation
2. **Mounting Hardware** - For sensor placement
3. **Cable Glands** - For waterproof cable entry
4. **Voltage Regulator** - If using different power source
5. **SD Card Module** - For local data logging

### Component Specifications

#### Arduino Uno
- Microcontroller: ATmega328P
- Operating Voltage: 5V
- Digital I/O Pins: 14
- Analog Input Pins: 6
- Flash Memory: 32 KB
- SRAM: 2 KB

#### Water Level Sensor
- Type: Analog resistive sensor
- Operating Voltage: 3.3V - 5V
- Detection Range: 0-100 cm (adjustable)
- Output: Analog voltage 0-5V
- Operating Temperature: -20°C to 85°C

#### SIM900/SIM800 GSM Module
- Operating Voltage: 3.4V - 4.4V
- Frequency: Quad-band (850/900/1800/1900 MHz)
- SMS: Support
- Operating Temperature: -40°C to 85°C

#### Solar Panel
- Power: 10W
- Voltage: 18V (open circuit)
- Current: 0.56A (short circuit)
- Type: Monocrystalline

#### Battery
- Type: Li-ion or LiFePO4
- Voltage: 12V
- Capacity: 10Ah
- Protection: Built-in BMS

## Wiring Diagram

### Arduino to Water Level Sensor

```
Water Level Sensor    Arduino Uno
-------------------   ------------
VCC                  Rated sensor supply (3.3V-5V)
GND                  GND
Signal (Analog)      A0
```

### Arduino to SIM900/SIM800 GSM Modem

```
SIM900/SIM800 UART   Arduino Uno
------------------   -----------
TX                   D7 (Arduino SoftwareSerial RX)
RX                   D8 (Arduino SoftwareSerial TX, level-shift as required)
GND                  GND (common ground)
```

Use a regulated supply that meets the modem board's voltage and peak-current
requirements, correct UART logic levels, and a common ground. Never power a
bare SIM900/SIM800 module from the Arduino 5V pin or a USB-TTL adapter's 5V
output.

### Power System

```
Solar Panel → Solar Charge Controller → Battery → regulated supplies → Arduino and GSM modem
```

### Optional Components

```
Status LEDs → D11 (Danger), D12 (Warning), D13 (Normal), each with a current-limiting resistor
```

## Assembly Instructions

### Step 1: Prepare the Workspace

1. Choose a clean, well-lit workspace
2. Gather all components and tools
3. Have the wiring diagram ready for reference
4. Ensure you have proper safety equipment (anti-static wrist strap)

### Step 2: Install Arduino IDE

1. Download Arduino IDE from [arduino.cc](https://www.arduino.cc/en/software)
2. Install the IDE on your computer
3. Install the necessary drivers:
   - CH340 driver (if using clone Arduino)
   - USB Serial drivers

### Step 3: Connect Water Level Sensor

1. Connect the water level sensor VCC to its rated supply (3.3V-5V)
2. Connect GND to Arduino GND
3. Connect the signal pin to Arduino A0
4. Secure the sensor in its mounting location
5. Route the cable away from water sources

### Step 4: Connect the GSM Modem to the Arduino

1. Connect SIM900 TX to Arduino D7 and SIM900 RX to Arduino D8 with appropriate logic levels.
2. Connect modem ground to Arduino ground.
3. Power the modem from a stable supply that meets its board's voltage and peak-current requirements.
4. Insert a SIM card with SMS service and connect the modem antenna.

### Step 5: Connect Power System

1. Connect solar panel to solar charge controller
2. Connect battery to solar charge controller
3. Connect battery output to voltage regulator
4. Connect the regulated supply to the Arduino as specified by the board.
5. Power the GSM modem separately using its rated supply.
6. Add a power switch for easy on/off control

### Step 6: Install Optional Components

1. Connect status LEDs to D11, D12, and D13 through appropriate current-limiting resistors.
2. Add any additional sensors or modules as needed.

### Step 7: Enclosure Assembly

1. Drill holes in enclosure for:
   - Power cable entry
   - Sensor cable entry
   - Antenna cable (for the GSM modem)
   - USB programming cable access
2. Install cable glands for waterproofing
3. Mount Arduino and components inside enclosure
4. Ensure proper ventilation
5. Seal enclosure properly

### Step 8: Sensor Installation

1. Choose appropriate location for water level sensor:
   - Near water source to be monitored
   - Protected from debris
   - Accessible for maintenance
2. Mount sensor securely
3. Calibrate sensor based on installation height
4. Test sensor readings

## Firmware Installation

### Step 1: Download Firmware

1. Navigate to `hardware/Arduino/flood_detection/flood_detection.ino`
2. Open the file in Arduino IDE

The bridge sends SMS to at most the first three active residents with SMS alerts enabled.
The Arduino firmware streams JSON telemetry over USB serial and relays SMS
commands from the Python bridge to a SIM900/SIM800 on D7/D8. It does not store
resident phone numbers; the bridge fetches active, SMS-enabled residents from
the Django API and sends each number to the Arduino only when dispatching an
SMS.

### Step 2: Configure Sensor Calibration

Edit the configuration section at the top of the file:

```cpp
const int RAW_DRY = 0;
const int RAW_FULL = 600;
const float SENSOR_SPAN_CM = 4.0f;
const float WARNING_THRESHOLD_CM = 2.0f;
const float DANGER_THRESHOLD_CM = 3.0f;
```

### Step 3: Install Required Libraries

The firmware uses the Arduino core and its bundled `SoftwareSerial` library. No
separate GSM, Firebase, or SMS library is required.

### Step 4: Select Board and Port

1. Tools → Board → Arduino Uno
2. Tools → Port → Select your Arduino's COM port

### Step 5: Upload Firmware

1. Click the Upload button (→)
2. Wait for upload to complete
3. Check for "Done uploading" message

### Step 6: Verify Installation

1. Open Serial Monitor (Ctrl+Shift+M)
2. Set baud rate to 9600
3. Power on the Arduino
4. Check for JSON sensor readings and `"gsm_status":"connected"`.
5. Close Serial Monitor before starting the Python bridge; only one program can use COM8 at a time.

## Configuration

### Firebase Setup

1. Create a Firebase project at [console.firebase.google.com](https://console.firebase.google.com)
2. Enable Realtime Database
3. Generate service account key
4. Configure the backend Firebase integration if it is enabled for this deployment.

### SIM Card Configuration

1. Ensure the SIM card can send SMS and has sufficient load/credit
2. Test SIM card in a phone first

### Sensor Calibration

1. With the sensor dry, record its averaged ADC value as `RAW_DRY`.
2. Submerge the active traces to their measured full span and record the ADC value as `RAW_FULL` or `RAW_WET`, depending on the selected sensor-only sketch.
3. Set `SENSOR_SPAN_CM` to the measured physical span in centimetres. Keep the same dry value, wet value, and span in `scripts/serial_bridge.py` (`--raw-dry`, `--raw-full`, and `--span-cm`).
4. Reflash the Arduino and verify several dry, midpoint, and full-span readings against a ruler before connecting operational alerts.
5. Readings outside the calibrated ADC or centimetre range are marked faulty or rejected; the dashboard excludes them from the current value and trend chart, and flags an older fallback reading as stale.

Do not map the 10-bit ADC range to an assumed 0-100 cm. Conductivity varies with the water, and this sensor only measures its calibrated active span. The default configuration is 0-600 ADC for 0-4 cm and must be replaced with site measurements if it differs.

### Threshold Configuration

Set appropriate thresholds based on your location:
- **Normal Threshold**: Safe water level
- **Alert Threshold**: Level to start monitoring closely
- **Warning Threshold**: Level to prepare for evacuation
- **Danger Threshold**: Critical level requiring immediate action

## Testing

### Hardware Testing

#### Water Level Sensor Test

1. Place sensor at different water levels
2. Monitor Serial Monitor for readings
3. Verify readings are accurate
4. Check for consistent readings

#### GSM Modem Test

1. Check the modem's power supply, antenna, SIM, and D7/D8 wiring.
2. Upload the updated firmware and confirm telemetry includes `"gsm_status":"connected"`.
3. Start the bridge with `--port COM8` (omit `--gsm-port`) and test with an opted-in resident.

#### Power System Test

1. Disconnect main power
2. Verify system runs on battery
3. Monitor battery voltage
4. Test solar charging

### Integration Testing

#### Django API Test

1. Start Django and the React dashboard.
2. Set `FLOODWATCH_USER` and `FLOODWATCH_PASSWORD`, then start `python scripts/serial_bridge.py --port COM8`.
3. Confirm readings appear on the dashboard.

#### SMS Alert Test

1. Add residents in the dashboard with status Active and SMS enabled.
2. Confirm an active SMS-capable modem and SIM are connected.
3. Trigger a Warning reading in a controlled test.
4. Confirm one message per eligible resident and inspect the SMS Logs page for `sent` or `failed` outcomes.
5. Warning sends once when entered; Danger sends once when the level rises further. A new alert can be sent after recovery and re-entry.

#### Admin-Triggered Official Advisory

1. Keep the Django backend, serial bridge, Arduino, and GSM modem running. The bridge polls the manual warning queue while the sensor is idle.
2. On the Water Level page, an admin selects **Send Warning to Citizens**, enters the issuing source (for example, PAGASA), reviews or edits the SMS text, and confirms.
3. The warning is sent to active residents with SMS enabled regardless of the sensor's current status.
4. The page reports queued, sending, completed, partially completed, or failed status; individual attempts appear in SMS Logs.
5. Use plain ASCII SMS text. A `sent` result indicates modem acceptance, not final confirmation that a recipient's handset received the message.

#### Full System Test

1. Run system for 24 hours
2. Monitor all components
3. Check data consistency
4. Verify battery performance
5. Test all alert levels

## Troubleshooting

### Common Issues

#### Arduino Not Detected

**Symptoms**: Port not showing in Arduino IDE

**Solutions**:
- Check USB cable connection
- Install proper drivers
- Try different USB port
- Test with different computer
- Check Arduino power LED

#### Sensor Not Reading

**Symptoms**: Water level readings are 0 or inconsistent

**Solutions**:
- Check sensor connections
- Verify sensor is powered
- Test sensor with multimeter
- Check analog pin configuration
- Calibrate sensor

#### GSM Modem Not Responding

**Symptoms**: Telemetry arrives but SMS fails, or firmware reports `"gsm_status":"disconnected"`.

**Solutions**:
- Check SIM900 TX/RX connections to Arduino D7/D8, and connect grounds
- Verify the modem's rated supply and peak current
- Verify SIM card is inserted properly
- Check crossed TX/RX wiring and UART logic levels
- Test SIM card in phone
- Verify antenna is connected

#### SMS Not Sending

**Symptoms**: SMS alerts not received

**Solutions**:
- Confirm resident status is Active and SMS is enabled
- Verify the resident has a valid +country-code mobile number
- Confirm the updated firmware is uploaded and the bridge is running with the Arduino COM port
- Check SMS balance
- Review the matching SMS log's delivery status and error message

#### Battery Draining Fast

**Symptoms**: Battery doesn't last through the night

**Solutions**:
- Check solar panel output
- Verify charge controller is working
- Reduce reading/upload intervals
- Check for power leaks
- Consider larger battery

### Debug Mode

Enable debug mode in firmware by uncommenting:
```cpp
#define DEBUG_MODE
```

This will print detailed information to Serial Monitor.

## Maintenance

### Regular Maintenance

#### Daily
- Check system status via dashboard
- Monitor battery level
- Verify data is being uploaded

#### Weekly
- Inspect physical connections
- Check sensor cleanliness
- Verify SMS functionality
- Review error logs

#### Monthly
- Clean solar panel
- Check battery health
- Update firmware if needed
- Calibrate sensor
- Backup configuration

#### Quarterly
- Test full system
- Replace SIM card if needed
- Check enclosure seals
- Review and update thresholds
- Performance analysis

### Sensor Maintenance

1. **Clean sensor regularly** to remove debris
2. **Check for corrosion** on connections
3. **Calibrate periodically** for accuracy
4. **Replace if readings become inconsistent**

### Battery Maintenance

1. **Monitor battery voltage** regularly
2. **Check for swelling or damage**
3. **Test battery capacity** periodically
4. **Replace every 2-3 years** depending on usage

### Solar Panel Maintenance

1. **Clean panel surface** regularly
2. **Check for damage or cracks**
3. **Verify connections are secure**
4. **Test output voltage** periodically

### Winter/Off-Season Storage

If system won't be used for extended period:

1. **Disconnect solar panel**
2. **Charge battery to 100%**
3. **Store battery in cool, dry place**
4. **Remove SIM card** if not needed
5. **Protect from moisture**

## Safety Precautions

### Electrical Safety

- Always disconnect power before working on wiring
- Use proper voltage regulation
- Avoid water contact with electronics
- Use waterproof connections

### Installation Safety

- Install in safe, accessible location
- Secure all components properly
- Use appropriate mounting hardware
- Consider weather protection

### Battery Safety

- Use proper battery type and rating
- Include battery protection circuit
- Store batteries properly
- Dispose of old batteries properly

### General Safety

- Follow local regulations
- Obtain necessary permits
- Work with qualified personnel if unsure
- Keep emergency contact information

## Appendix

### Pin Reference

| Arduino Pin | Connected To | Function |
|-------------|---------------|----------|
| A0 | Water Level Sensor | Analog Input |
| D7 | Sensor power | Digital output |
| D11 | Danger LED | Digital output |
| D12 | Warning LED | Digital output |
| D13 | Normal LED | Digital output |
| A0 | Water level sensor | Analog input |
| GND | Sensor and LEDs | Common ground |

### Contact Information

- **Technical Support**: [Contact details]
- **Hardware Vendor**: [Contact details]
- **Network Provider**: [Contact details]

### Version History

- **v1.0.0** (2026-07-13): Initial hardware setup guide
