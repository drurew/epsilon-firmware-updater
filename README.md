# Epsilon Firmware Updater v2.1

Production-grade firmware updater for SuperB Epsilon V2 battery management system with automatic BIN→HEX conversion.

## 🎉 What's New in v2.1 (Dec 25, 2025)

- ✅ **Automatic BIN→HEX Conversion** - Upload .bin files directly, automatic Intel HEX conversion
- ✅ **Correct Base Address** - Fixed 0x08018000 application region
- ✅ **Multiple ELA Records** - Proper Extended Linear Address records for >64KB firmware
- ✅ **Start Linear Address** - Extracts Reset Handler from vector table (type 05 record)
- ✅ **Automatic CAN Setup** - Brings up can0 interface with sudo if needed
- ✅ **Mode Detection** - Checks if device already in correct mode before switching
- ✅ **Firmware Boots Successfully** - Complete Intel HEX format with all required records

## Features

- ✅ **Automatic Retry** - Intelligent retry logic with backoff
- ✅ **Reboot Detection** - Automatically detects device reboots, no manual power cycling
- ✅ **Complete Error Recovery** - Recovers from interrupted uploads and errors
- ✅ **Smart Cancellation** - Never interrupts critical firmware upload operations
- ✅ **30-Second Timeouts** - Intelligent timeout windows for each phase
- ✅ **Progress Tracking** - Real-time status updates during update process

## Installation

### Requirements

- Python 3.7+
- python-can library
- SocketCAN interface (Linux) or compatible CAN adapter

```bash
pip install python-can
```

### CAN Interface Setup

The updater automatically brings up the CAN interface if needed. No manual setup required!

**Automatic** (v2.1+):
```bash
# Just run the updater, it handles CAN setup
python3 update_firmware_v2.1.py 1 firmware.bin
```

**Manual** (if needed):
```bash
# Bring up CAN interface manually
sudo ip link set can0 type can bitrate 250000
sudo ip link set up can0
```

## Usage

### Basic Update (.hex or .bin files)

```bash
python3 update_firmware_v2.1.py <node_id> <firmware_file>
```

Examples:
```bash
# Intel HEX file (traditional)
python3 update_firmware_v2.1.py 1 Epsilon_V2_Application_v1.2.5.hex

# Binary file (v2.1+ automatic conversion)
python3 update_firmware_v2.1.py 1 firmware_v1.2.5.bin
```

The updater automatically:
1. Detects file format (.bin or .hex)
2. Converts .bin to Intel HEX if needed (base 0x08018000, all required records)
3. Sets up CAN interface (with sudo prompt if not running as root)
4. Checks current device mode (bootloader/application)
5. Uploads firmware
6. Verifies successful boot

## Update Process

The firmware update follows these phases:

```
[PHASE 1] Reading firmware file
  ✓ Loaded firmware
  ✓ Format detected (Binary/Intel HEX)
  ✓ Converted to Intel HEX (if .bin)

[PHASE 2] Entering bootloader mode
  ✓ Current mode detected
  ✓ Switched to bootloader (if needed)

[PHASE 3] Flashing firmware
  ⚠️  CRITICAL: Upload must complete - do not interrupt!
  ✓ Uploading firmware...
  ✓ Upload complete

[PHASE 4] Verifying firmware
  ✓ Device rebooted
  ✓ Application mode verified

[PHASE 5] Returning to application mode
  ✓ Device running new firmware
```

## Intel HEX Format (v2.1)

The updater automatically converts .bin files to proper Intel HEX format:

**Required Records**:
- **Type 00**: Data records (16 bytes per line)
- **Type 04**: Extended Linear Address (ELA) - emitted at every 64KB boundary
- **Type 05**: Start Linear Address (SLA) - execution entry point from vector table
- **Type 01**: End of File (EOF)

**Base Address**: 0x08018000 (application region after bootloader)

**Example** (for 342KB firmware):
```
:020000040801F1          # ELA #1 (0x0801xxxx)
:10018000<data>...       # Data records at 0x08018000
:020000040802F0          # ELA #2 (0x0802xxxx) - at 64KB boundary
:020000040803EF          # ELA #3 (0x0803xxxx) - at 128KB boundary
:020000040804EE          # ELA #4 (0x0804xxxx) - at 192KB boundary
:020000040805ED          # ELA #5 (0x0805xxxx) - at 256KB boundary
:020000040806EC          # ELA #6 (0x0806xxxx) - at 320KB boundary
:0400000508018201E9     # Start Linear Address (Reset Handler)
:00000001FF              # EOF
```

## Troubleshooting

### Upload Failed

If upload fails, simply run the updater again:

```bash
# Retry with same firmware
python3 update_firmware_v2.1.py 1 firmware.hex

# Or try different firmware
python3 update_firmware_v2.1.py 1 different_firmware.hex
```

### Device Won't Boot

If firmware uploads but device won't boot:
1. Device stays in bootloader mode
2. Firmware may be corrupted or incompatible
3. Try uploading official firmware from manufacturer

**Recovery**:
```bash
# Upload official firmware
python3 update_firmware_v2.1.py 1 official_firmware.hex
```

### CAN Bus Issues

**Symptom**: "Failed to connect to CAN bus"

**Solution**:
```bash
# Check interface exists
ip link show can0

# Manually bring up interface
sudo ip link set can0 type can bitrate 250000
sudo ip link set up can0

# Verify CAN traffic
candump can0
```

### Permission Denied

**Symptom**: "Permission denied" when setting up CAN

**Solution**: Run with sudo or add user to appropriate group:
```bash
# Run with sudo
sudo python3 update_firmware_v2.1.py 1 firmware.bin

# Or add user to dialout group
sudo usermod -a -G dialout $USER
# Log out and back in
```

## Safety Notes

⚠️ **IMPORTANT: Power Quality**
- Ensure device is powered from clean, stable power source
- DO NOT update while charging from noisy/dirty power
- Power fluctuations can cause upload failures

⚠️ **CRITICAL: Do Not Interrupt**
- Never interrupt during Phase 3 (Flashing)
- Let the update complete fully
- Device may be bricked if interrupted during flash

⚠️ **Recovery**
- Keep original firmware files as backup
- If update fails, retry with original firmware
- Device can recover from most failed updates

## Technical Details

### Hardware
- **MCU**: STM32L452VET3 (ARM Cortex-M4)
- **Flash**: 512KB (Bootloader: 0x08000000-0x08018000, App: 0x08018000+)
- **CAN**: 250 kbps, CANopen protocol
- **Bootloader**: 98KB (0x08000000-0x08018000)
- **Application**: Up to 414KB (0x08018000-0x08080000)

### Protocol
- **CANopen SDO** - Segmented download for firmware transfer
- **Object 0x1F50:01** - Firmware upload (segmented download)
- **Object 0x1F51:01** - Program mode control (0=bootloader, 1=application)
- **Object 0x1F57:01** - Firmware status (busy/complete/error)

### Firmware Versions

**From SuperB Website**:
- Epsilon_V2_Application_v1.2.5.hex (Dec 2022)
- Epsilon_V2_Application_v1.3.5.hex (Dec 2025 - Latest)

**Format**: Intel HEX with proper addressing for STM32L452

## License

Educational and interoperability project for SuperB Epsilon V2 BMS.

Firmware files are property of Super B Battery Management.

## Credits

**Version**: 2.1  
**Release Date**: December 25, 2025  
**Major Breakthrough**: Automatic BIN→HEX conversion with correct Intel HEX format

---

**Happy Updating!** 🔋⚡
