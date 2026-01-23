#!/usr/bin/env python3
"""
Firmware updater for SuperB Epsilon V2 BMS modules using CANopen protocol.

Implements CANopen SDO segmented download for firmware upload to device bootloader.

Copyright (C) 2025 Epsilon V2 Firmware Updater Contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import can
import time
import struct
import sys
from typing import Optional

class FirmwareUpdater:
    """
    CANopen firmware updater for Epsilon V2 BMS modules.
    
    Uses CANopen SDO protocol for bootloader communication and firmware upload.
    """
    
    def __init__(self, bus: can.Bus, node_id: int):
        self.bus = bus
        self.node_id = node_id
        self.sdo_tx = 0x600 + node_id
        self.sdo_rx = 0x580 + node_id
        
    def set_program(self, program: int) -> bool:
        """
        Control bootloader/application mode via CANopen SDO.
        
        Writes to SDO 0x1F51:01 (Program Control):
        - Value 0: Enter bootloader mode
        - Value 1: Enter application mode
        
        Args:
            program: 0 for bootloader, 1 for application
            
        Returns:
            True if successful, False otherwise
        """
        print(f"[SetProgram] Writing {program} to SDO 0x1F51:01 (8017:1)")
        
        # Write 1 byte to SDO 0x1F51:01
        # Command: 0x2F = write 1 byte expedited
        msg = can.Message(
            arbitration_id=self.sdo_tx,
            data=[0x2F, 0x51, 0x1F, 0x01, program, 0x00, 0x00, 0x00],
            is_extended_id=False
        )
        self.bus.send(msg)
        
        # Wait for response
        response = self.bus.recv(timeout=2.0)
        if response and response.arbitration_id == self.sdo_rx:
            if response.data[0] == 0x60:  # Write OK
                print(f"[SetProgram] ✓ SDO Write confirmed")
                return True
            elif response.data[0] == 0x80:  # Abort
                abort_code = struct.unpack('<I', response.data[4:8])[0]
                print(f"[SetProgram] ✗ SDO Abort: 0x{abort_code:08X}")
                return False
        
        print(f"[SetProgram] ✗ No response")
        return False
    
    def program_firmware(self, firmware_data: bytes) -> bool:
        """
        Upload firmware to device bootloader via CANopen SDO segmented download.
        
        Writes firmware data to SDO 0x1F50:01 using CANopen segmented download.
        
        Args:
            firmware_data: Complete firmware file contents (Intel HEX ASCII format)
            
        Returns:
            True if upload successful, False otherwise
        """
        print(f"[ProgramFirmware] Uploading {len(firmware_data)} bytes to SDO 0x1F50:01 (8016:1)")
        print(f"[ProgramFirmware] Transfer mode: Segmented (NoBlock)")
        print(f"[ProgramFirmware] Timeout: 30000ms")
        
        return self._sdo_segmented_download(0x1F50, 0x01, firmware_data, timeout=30.0)
    
    def _sdo_segmented_download(self, index: int, subindex: int, data: bytes, timeout: float = 30.0) -> bool:
        """
        CANopen SDO Segmented Download protocol implementation (CiA 301).
        
        Uploads data in 7-byte segments with toggle bit protocol.
        """
        # Clear receive queue first
        print(f"[SDO] Clearing receive queue...")
        while self.bus.recv(timeout=0.01):
            pass
        
        # Step 1: Initiate Download (size indicated)
        # Command: 0x21 = download initiate, size indicated
        size_bytes = struct.pack('<I', len(data))
        msg = can.Message(
            arbitration_id=self.sdo_tx,
            data=[0x21, index & 0xFF, (index >> 8) & 0xFF, subindex] + list(size_bytes),
            is_extended_id=False
        )
        self.bus.send(msg)
        print(f"[SDO] Initiate download: {len(data)} bytes to 0x{index:04X}:{subindex}")
        
        # Wait for initiate response with proper filtering
        start_time = time.time()
        while time.time() - start_time < 5.0:
            response = self.bus.recv(timeout=0.5)
            if not response:
                continue
            # Only process messages from our node
            if response.arbitration_id != self.sdo_rx:
                continue
            # Only process messages from our node
            if response.arbitration_id != self.sdo_rx:
                continue
            
            if response.data[0] == 0x80:
                abort_code = struct.unpack('<I', response.data[4:8])[0]
                print(f"[SDO] ✗ Initiate abort: 0x{abort_code:08X}")
                return False
            if response.data[0] != 0x60:
                print(f"[SDO] ✗ Unexpected initiate response: 0x{response.data[0]:02X}")
                continue  # Keep waiting
            
            print(f"[SDO] ✓ Download initiated")
            break
        else:
            print(f"[SDO] ✗ Initiate timeout after 5 seconds")
            return False
        
        # Step 2: Send segments
        offset = 0
        toggle = 0
        segment_num = 0
        last_progress = 0
        start_time = time.time()
        
        while offset < len(data):
            # Get segment data (7 bytes max)
            segment_data = data[offset:offset+7]
            bytes_in_segment = len(segment_data)
            is_last = (offset + bytes_in_segment >= len(data))
            
            # Build command byte
            # Bit 4: toggle (0 or 1)
            # Bit 0: last segment flag
            # Bits 1-3: number of bytes that do NOT contain data (7 - bytes_in_segment)
            cmd = (toggle << 4)
            if is_last:
                cmd |= 0x01
            n = 7 - bytes_in_segment
            cmd |= (n << 1)
            
            # Pad to 7 bytes
            segment_data = segment_data + bytes([0] * (7 - len(segment_data)))
            
            # Send segment
            msg = can.Message(
                arbitration_id=self.sdo_tx,
                data=[cmd] + list(segment_data),
                is_extended_id=False
            )
            self.bus.send(msg)
            
            # Wait for segment response - filter out heartbeats (0x701)
            response = None
            start_wait = time.time()
            while time.time() - start_wait < 2.0:  # 2 second timeout per segment
                msg = self.bus.recv(timeout=0.1)
                if not msg:
                    continue
                # Skip heartbeat messages
                if msg.arbitration_id == 0x700 + self.node_id:
                    continue
                # Check for SDO response
                if msg.arbitration_id == self.sdo_rx:
                    response = msg
                    break
            
            if not response:
                print(f"[SDO] ✗ Segment {segment_num} timeout (no SDO response)")
                return False
            
            if response.data[0] == 0x80:
                abort_code = struct.unpack('<I', response.data[4:8])[0]
                print(f"[SDO] ✗ Segment {segment_num} abort: 0x{abort_code:08X}")
                return False
            
            # Verify response toggle bit
            if (response.data[0] & 0xE0) != 0x20:
                print(f"[SDO] ✗ Unexpected segment response: 0x{response.data[0]:02X}")
                return False
            
            response_toggle = (response.data[0] >> 4) & 0x01
            if response_toggle != toggle:
                print(f"[SDO] ✗ Toggle bit mismatch at segment {segment_num}")
                return False
            
            # Update progress
            offset += bytes_in_segment
            toggle = 1 - toggle
            segment_num += 1
            
            # Progress reporting
            progress = int((offset / len(data)) * 100)
            if progress >= last_progress + 10 or is_last:
                elapsed = time.time() - start_time
                rate = offset / elapsed if elapsed > 0 else 0
                print(f"[SDO] {progress}% ({offset}/{len(data)} bytes, {rate:.0f} B/s)")
                last_progress = progress
        
        print(f"[SDO] ✓ Upload complete ({segment_num} segments)")
        return True
    
    def get_firmware_status(self) -> Optional[tuple]:
        """
        Read firmware update status from device.
        
        Reads SDO 0x1F57:01 to check firmware verification status:
        - Bit 0: 0=Complete, 1=Busy
        - Bits 8-14: Error code (if bit 0 is 0)
        
        SDO 8023 = 0x1F57 (Firmware Status)
        Subindex: 1
        Bit 0: Status (0=OK/Error, 1=Busy)
        
        Returns:
            Tuple of (status_string, error_code) or None if read fails
        """
        # Read SDO 0x1F57:01
        msg = can.Message(
            arbitration_id=self.sdo_tx,
            data=[0x40, 0x57, 0x1F, 0x01, 0x00, 0x00, 0x00, 0x00],
            is_extended_id=False
        )
        self.bus.send(msg)
        
        response = self.bus.recv(timeout=1.0)
        if not response or response.arbitration_id != self.sdo_rx:
            return None
        
        if response.data[0] == 0x4F:  # 1-byte upload response
            value = response.data[4]
            status_bit = value & 1
            
            if status_bit == 0:  # OK or Error
                error_code = (value >> 8) & 0x7F
                if error_code > 0:
                    return ("Error", error_code)
                return ("Ok", 0)
            elif status_bit == 1:  # Busy
                return ("Busy", 0)
        
        return None


def parse_intel_hex(hex_file: str) -> bytes:
    """Parse Intel HEX file to binary"""
    data = bytearray()
    extended_addr = 0
    base_address = None
    
    with open(hex_file, 'r') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or not line.startswith(':'):
                continue
            
            try:
                byte_count = int(line[1:3], 16)
                address = int(line[3:7], 16)
                record_type = int(line[7:9], 16)
                
                if record_type == 0x04:  # Extended Linear Address
                    extended_addr = int(line[9:13], 16) << 16
                elif record_type == 0x00:  # Data Record
                    full_addr = extended_addr + address
                    
                    if base_address is None:
                        base_address = full_addr
                    
                    offset = full_addr - base_address
                    if offset + byte_count > len(data):
                        data.extend([0xFF] * (offset + byte_count - len(data)))
                    
                    for i in range(byte_count):
                        byte_val = int(line[9 + i*2:11 + i*2], 16)
                        data[offset + i] = byte_val
            except Exception as e:
                print(f"Warning: Line {line_num}: {e}")
    
    return bytes(data)


def main():
    if len(sys.argv) < 4:
        print("Usage: update_firmware.py <node_id> <hex_file> <can_interface>")
        print()
        print("Arguments:")
        print("  node_id        CANopen node ID (1-127)")
        print("  hex_file       Path to Intel HEX firmware file")
        print("  can_interface  SocketCAN interface (e.g., can0, vcan0)")
        print()
        print("Examples:")
        print("  python3 update_firmware.py 1 firmware.hex can0")
        print("  python3 update_firmware.py 2 Epsilon_V2_v1.2.5.hex can1")
        sys.exit(1)
    
    node_id = int(sys.argv[1])
    hex_file = sys.argv[2]
    can_interface = sys.argv[3]
    
    print("="*70)
    print(f"FIRMWARE UPDATE - Node {node_id}")
    print(f"File: {hex_file}")
    print("="*70)
    print()
    
    # Read firmware file AS-IS (ASCII text, not parsed!)
    print("Loading firmware file...")
    with open(hex_file, 'rb') as f:
        firmware_data = f.read()
    print(f"✓ Loaded {len(firmware_data)} bytes (Intel HEX ASCII format)")
    print()
    
    # Connect to CAN
    print("Connecting to CAN bus...")
    bus = can.Bus(interface='socketcan', channel=can_interface, bitrate=250000)
    print(f"✓ Connected to {can_interface}")
    print()
    
    updater = FirmwareUpdater(bus, node_id)
    
    try:
        print("="*70)
        print("STEP 1: ENTER BOOTLOADER")
        print("="*70)
        if not updater.set_program(0):  # 0 = Bootloader
            raise Exception("Failed to enter bootloader")
        print("✓ Bootloader mode activated")
        print("Waiting 5 seconds for device to reboot...")
        time.sleep(5)
        print()
        
        print("="*70)
        print("STEP 2: UPLOAD FIRMWARE")
        print("="*70)
        if not updater.program_firmware(firmware_data):
            raise Exception("Firmware upload failed")
        print("✓ Firmware uploaded successfully")
        print()
        
        print("="*70)
        print("STEP 3: VERIFICATION (Internal)")
        print("="*70)
        print("Device is verifying firmware internally...")
        print("This may take 30 seconds...")
        time.sleep(30)
        print("✓ Verification period complete")
        print()
        
        print("="*70)
        print("STEP 4: EXIT TO APPLICATION")
        print("="*70)
        if not updater.set_program(1):  # 1 = Application
            print("⚠ Warning: Could not send exit command (device may have already rebooted)")
        else:
            print("✓ Application mode command sent")
        print("Waiting 3 seconds for reboot...")
        time.sleep(3)
        print()
        
        print("="*70)
        print("✅ FIRMWARE UPDATE COMPLETE!")
        print("="*70)
        
    except Exception as e:
        print()
        print("="*70)
        print(f"❌ UPDATE FAILED: {e}")
        print("="*70)
        import traceback
        traceback.print_exc()
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
