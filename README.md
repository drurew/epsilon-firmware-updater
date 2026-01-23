# Epsilon V2 Firmware Updater

Open-source firmware updater for SuperB Epsilon V2 BMS modules using the CANopen protocol. This tool enables firmware updates and recovery without requiring expensive proprietary hardware.

## Features

- ✅ **Firmware Update:** Upload new firmware to BMS modules
- ✅ **Firmware Recovery:** Recover bricked modules stuck in bootloader mode
- ✅ **Multi-Node Support:** Update any node ID on the CAN bus
- ✅ **Progress Monitoring:** Real-time upload progress and speed metrics
- ✅ **Automatic Retry:** Built-in retry logic for reliable updates

## Hardware Requirements

### Tested Configuration
- **CAN Interface:** IXXAT USB-to-CAN V2 (~$200)
- **CAN Bitrate:** 250 kbps
- **Platform:** Linux (Fedora, Ubuntu, Debian tested)

### Other Compatible Interfaces
This tool works with any **SocketCAN-compatible** interface, including:
- PEAK PCAN-USB
- Kvaser USBcan
- CANable/canable
- Raspberry Pi with MCP2515 SPI module
- Any USB/PCI CAN adapter with Linux SocketCAN support

## Software Requirements

### Python
- Python 3.8 or newer

### Dependencies
```bash
pip install python-can>=4.6.1
```

**⚠️ Important:** Must use `python-can` version 4.6.1 or newer. Version 4.2.2 has broken CAN filters and will cause updates to fail.

## Installation

1. Clone this repository:
```bash
git clone https://github.com/drurew/epsilon-firmware-updater.git
cd epsilon-firmware-updater
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up your CAN interface (example for can0 at 250 kbps):
```bash
sudo ip link set can0 type can bitrate 250000
sudo ip link set can0 up
```

## Usage

### Basic Syntax
```bash
python3 update_firmware.py <node_id> <firmware_file.hex> <can_interface>
```

### Arguments
- **node_id**: CANopen node ID of the BMS module (1-127)
- **firmware_file.hex**: Path to Intel HEX firmware file
- **can_interface**: SocketCAN interface name (e.g., can0, vcan0)

### Examples

**Update Node 1 with new firmware:**
```bash
python3 update_firmware.py 1 Epsilon_V2_Application_v1.2.5.hex can0
```

**Recover Node 2 stuck in bootloader:**
```bash
# Same command - tool automatically detects bootloader mode
python3 update_firmware.py 2 Epsilon_V2_Application_v1.2.5.hex can0
```

**Update using different interface:**
```bash
python3 update_firmware.py 1 firmware.hex can1
```

## Use Cases

### 1. Firmware Update
Update a functioning BMS module to a newer firmware version:
- Device starts in application mode (heartbeat 0x05)
- Tool enters bootloader mode automatically
- Uploads firmware
- Returns to application mode

### 2. Firmware Recovery
Recover a module stuck in bootloader mode after interrupted update:
- Device already in bootloader mode (heartbeat 0x7F)
- Tool detects bootloader state
- Uploads firmware without mode switching
- Exits to application mode

## Expected Behavior

### Successful Update Output
```
======================================================================
FIRMWARE UPDATE - Node 1
======================================================================

Loading firmware file...
✓ Loaded 961973 bytes (Intel HEX ASCII format)

Connecting to CAN bus...
✓ Connected to can0

======================================================================
STEP 1: ENTER BOOTLOADER
======================================================================
✓ Bootloader mode activated

======================================================================
STEP 2: UPLOAD FIRMWARE
======================================================================
[SDO] 10% (96201/961973 bytes, 2842 B/s)
[SDO] 20% (192395/961973 bytes, 2915 B/s)
...
[SDO] 100% (961973/961973 bytes, 2972 B/s)
✓ Firmware uploaded successfully

======================================================================
STEP 3: VERIFICATION (Internal)
======================================================================
✓ Verification period complete

======================================================================
STEP 4: EXIT TO APPLICATION
======================================================================
✓ Update complete

======================================================================
✅ FIRMWARE UPDATE COMPLETE!
======================================================================
```

### Performance Metrics
- **Upload Speed:** ~2,960 bytes/second
- **Total Time:** ~6 minutes for 962KB firmware
- **Segments:** 137,425 × 7-byte segments
- **Success Rate:** 100% with proper power supply

## Customizing CAN Interface

### Using a Different Interface
The tool uses the interface name passed as the third argument. To use a different interface:

1. Verify interface exists:
```bash
ip link show can1
```

2. Configure bitrate (if needed):
```bash
sudo ip link set can1 type can bitrate 250000
sudo ip link set can1 up
```

3. Run updater with new interface:
```bash
python3 update_firmware.py 1 firmware.hex can1
```

### Modifying Interface in Code
If you need to change the default behavior, edit `update_firmware.py`:

```python
# Line ~360: CAN bus initialization
bus = can.Bus(interface='socketcan', 
              channel=can_interface,  # This uses the CLI argument
              bitrate=250000)
```

To hardcode a specific interface:
```python
bus = can.Bus(interface='socketcan', 
              channel='can0',  # Hardcoded interface
              bitrate=250000)
```

For non-SocketCAN interfaces, change the interface type:
```python
# Example: PCAN USB on Windows
bus = can.Bus(interface='pcan', 
              channel='PCAN_USBBUS1',
              bitrate=250000)
```

See [python-can documentation](https://python-can.readthedocs.io/) for all supported interfaces.

## Protocol Details

### CANopen SDO Objects
- **0x1F51:01** - Program Control (0=Bootloader, 1=Application)
- **0x1F50:01** - Firmware Upload (segmented download)

### Update Sequence
1. **Enter Bootloader:** Write 0 to Program Control SDO
2. **Upload Firmware:** Segmented download to Upload SDO (137,425 segments)
3. **Verification:** Device verifies internally (~30 seconds)
4. **Exit Bootloader:** Write 1 to Program Control SDO

### File Format
Firmware must be in **Intel HEX ASCII format** (not binary). The tool uploads the .hex file exactly as-is without parsing or conversion.

## Troubleshooting

### Update Fails Immediately
- **Check CAN interface is up:** `ip link show can0`
- **Verify bitrate is 250 kbps:** `ip -d link show can0`
- **Check python-can version:** `pip show python-can` (must be ≥4.6.1)

### Update Stops at Low Percentage
- **Ensure stable power supply** - voltage drops cause failures
- **Check for CAN bus conflicts** - disable other services using the bus
- **Verify CAN cable quality** - poor connections cause timeouts

### Device Not Responding After Update
- **Wait 60 seconds** - device may still be verifying firmware
- **Power cycle the device** - some failures require hardware reset
- **Re-run recovery** - upload firmware again if stuck in bootloader

### No Progress Updates
- **Check node ID is correct** - wrong ID means no response
- **Verify device is powered** - check for heartbeat messages:
  ```bash
  candump can0 | grep 701  # Node 1 heartbeat
  ```

## Safety Warnings

⚠️ **Power Stability:** Ensure continuous stable power for the entire ~6 minute update. Loss of power can brick the BMS.

⚠️ **Complete Sequence:** If an update fails, restart from the beginning. Do not attempt to resume partial updates.

⚠️ **Correct Firmware:** Only use firmware files intended for Epsilon V2 modules. Wrong firmware can brick the device.

⚠️ **Backup Configuration:** Device configuration and serial numbers are preserved, but document settings before updating.

## Technical Specifications

- **Protocol:** CANopen SDO Segmented Download
- **CAN Bitrate:** 250 kbps
- **Node ID Range:** 1-127
- **Firmware Size:** ~962 KB (961,973 bytes)
- **Segment Size:** 7 bytes per segment
- **Total Segments:** 137,425
- **Timeout:** 30 seconds per SDO operation

## License

GPLv3 - see [LICENSE](LICENSE) file for details.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This ensures that any modifications or derivative works remain open source and freely available to the community.

## Contributing

Contributions are welcome! Please open an issue or pull request.

## Disclaimer

This tool is provided as-is without warranty. Use at your own risk. Always ensure you have a backup plan and understand the risks of firmware updates.

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Verify your CAN interface is working: `candump can0`
3. Open an issue on GitHub with full error output

---

**Successfully tested on SuperB Epsilon V2 BMS modules with firmware v1.2.5**
