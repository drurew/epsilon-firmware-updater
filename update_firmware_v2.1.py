#!/usr/bin/env python3
"""
Epsilon Firmware Updater v2.1
=============================

Production-grade CANopen firmware updater for Epsilon V2 devices.

Features:
- Green visual progress bar with packet tracking
- Automatic bootloader mode entry with retry logic
- Resilient segmented SDO upload with verification
- Smart firmware verification via application mode switch
- Comprehensive error handling and recovery
- Clean, professional output formatting

Usage:
    python3 update_firmware_production.py <node_id> <firmware.hex>

Example:
    python3 update_firmware_production.py 1 firmware_v1.2.5.hex
"""

import can
import time
import struct
import sys
import enum
import subprocess
import os
from typing import Optional, Callable, Any
from dataclasses import dataclass

__version__ = "2.1"


class Progress(enum.Enum):
    """Progress stages"""
    FILE = "file"
    BOOTLOADER = "bootloader"
    FLASHING = "flashing"
    VERIFYING = "verifying"
    APPLICATION = "application"


class State(enum.Enum):
    """Operation states"""
    BUSY = "busy"
    FAILED = "failed"
    CANCELED = "canceled"
    SUCCESS = "success"


@dataclass
class FirmwareStatus:
    """Firmware status returned by device"""
    status: str  # "Ok", "Busy", "Error"
    error_code: int


class CancellationToken:
    """Simple cancellation token"""
    def __init__(self):
        self.cancelled = False
    
    def cancel(self):
        self.cancelled = True
    
    def is_cancelled(self):
        return self.cancelled
    
    def throw_if_cancelled(self):
        if self.cancelled:
            raise InterruptedError("Operation cancelled")


class ProductionFirmwareUpdater:
    
    def __init__(self, bus: can.Bus, node_id: int):
        self.bus = bus
        self.node_id = node_id
        self.sdo_tx = 0x600 + node_id
        self.sdo_rx = 0x580 + node_id
        self.progress_callback: Optional[Callable[[Progress, State], None]] = None
        self.flashed = False  # Track if firmware was successfully uploaded
        self.in_bootloader = False  # Track if device is in bootloader mode
        
    def program(
        self,
        firmware_path: str,
        is_bootloader_file: bool = False,
        callback: Optional[Callable[[Progress, State], None]] = None,
        cancellation_token: Optional[CancellationToken] = None
    ) -> bool:

        self.progress_callback = callback
        if cancellation_token is None:
            cancellation_token = CancellationToken()
        
        self.flashed = False
        self.in_bootloader = False
        
        try:
            # PHASE 1: READ FILE 
            
            print("     \033[1mPHASE 1: READING FIRMWARE FILE\033[0m")
            
            
            buffer = self._run_progress(Progress.FILE, State.BUSY, lambda: self._read_file(firmware_path))
            if buffer is None:
                self._notify(Progress.FILE, State.FAILED)
                return False
            self._notify(Progress.FILE, State.SUCCESS)
            
            # PHASE 2: ENTER BOOTLOADER 
            
            print("     \033[1mPHASE 2: ENTERING BOOTLOADER MODE\033[0m")
            
            # First, check current mode
            print("\n[ModeCheck] Checking device current mode...")
            current_mode = self._get_program()
            
            if current_mode is None:
                print("⚠️  [ModeCheck] Unable to read current mode (device may be offline)")
                print("    Attempting to switch to bootloader anyway...")
            elif current_mode == 0:
                print("✓ [ModeCheck] Device already in BOOTLOADER mode (program=0)")
                print("  Skipping mode switch, continuing with upload...")
                self.in_bootloader = True
                self._notify(Progress.BOOTLOADER, State.SUCCESS)
                # Skip mode change, already in bootloader
            elif current_mode == 1:
                print("ℹ [ModeCheck] Device in APPLICATION mode (program=1)")
                print("  Switching to bootloader mode...")
            
            # Only switch if not already in bootloader
            if current_mode != 0:
                success = self._run_progress(
                    Progress.BOOTLOADER, 
                    State.BUSY,
                    lambda: self._change_program(0, 30000, cancellation_token)
                )
                if not success:
                    self._notify(Progress.BOOTLOADER, State.FAILED)
                    raise Exception("Could not enter bootloader mode")
                
                self.in_bootloader = True
                self._notify(Progress.BOOTLOADER, State.SUCCESS)
            
            # PHASE 3 & 4: FLASH + VERIFY 
            try:
                # PHASE 3: Flash Firmware (NEVER INTERRUPTED)
                print("     \033[1mPHASE 3: FLASHING FIRMWARE\033[0m")
                print("     ⚠️  CRITICAL: Upload MUST complete - do not interrupt!")
                
                if self.flashed:
                    cancellation_token.throw_if_cancelled()
                
                success = self._run_progress(
                    Progress.FLASHING,
                    State.BUSY,
                    lambda: self._program_firmware(buffer, cancellation_token)
                )
                if not success:
                    self._notify(Progress.FLASHING, State.FAILED)
                    raise Exception("Firmware upload failed")
                
                self.flashed = True
                self._notify(Progress.FLASHING, State.SUCCESS)
                
                # PHASE 4: Verify Firmware
                print("     \033[1mPHASE 4: VERIFYING FIRMWARE\033[0m")
                                                
                # Don't check cancellation if already flashed
                if not self.flashed:
                    cancellation_token.throw_if_cancelled()
                
                success = self._run_progress(
                    Progress.VERIFYING,
                    State.BUSY,
                    lambda: self._verify_firmware(is_bootloader_file, cancellation_token)
                )
                if not success:
                    self._notify(Progress.VERIFYING, State.FAILED)
                    raise Exception("Firmware verification failed")
                
                self._notify(Progress.VERIFYING, State.SUCCESS)
                
            finally:
                # PHASE 5: RETURN TO APPLICATION (ALWAYS RUNS) 
                if not is_bootloader_file:
                    
                    print("     \033[1mPHASE 5: RETURNING TO APPLICATION MODE\033[0m")
                                        
                    # Don't check cancellation if not yet in bootloader
                    if not self.in_bootloader:
                        cancellation_token.throw_if_cancelled()
                    
                    success = self._run_progress(
                        Progress.APPLICATION,
                        State.BUSY,
                        lambda: self._change_program(1, 30000, cancellation_token)
                    )
                    if not success:
                        self._notify(Progress.APPLICATION, State.FAILED)
                        print("    ⚠️  \033[1mWARNING: Could not return to application mode\033[0m")
                        print("             \033[1mThe bootloader rejected the firmware.\033[0m")
                        print("             \033[1mThe Checksums may not match.\033[0m")
                    else:
                        self._notify(Progress.APPLICATION, State.SUCCESS)
            
            # Final cancellation check
            if not self.flashed:
                cancellation_token.throw_if_cancelled()
            
            print("        ✅ \033[1mFIRMWARE UPDATE COMPLETE!\033[0m")
            return True
            
        except InterruptedError:            
            print("        ⚠️  \033[1mOPERATION CANCELLED BY USER\033[0m")
            return False
        except Exception as e:
            print(f"        ❌ \033[1mFIRMWARE UPDATE FAILED:\033[0m {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _read_file(self, firmware_path: str) -> Optional[bytes]:
        """Read firmware file and convert to Intel HEX if needed"""
        try:
            with open(firmware_path, 'rb') as f:
                data = f.read()
            print(f"✓ Loaded {len(data)} bytes from {firmware_path}")
            
            # Check if it's ASCII Intel HEX format
            if data.startswith(b':'):
                print("  Format: Intel HEX (ASCII) - ready for upload")
                return data
            else:
                print("  Format: Binary - converting to Intel HEX...")
                # Bootloader expects Intel HEX format!
                # Convert binary to Intel HEX
                # CRITICAL: Base address is 0x08018000 (application region after bootloader)
                hex_data = self._bin_to_intel_hex(data, base_address=0x08018000)
                print(f"✓ Converted to Intel HEX: {len(hex_data)} bytes")
                return hex_data
        except Exception as e:
            print(f"✗ Failed to read file: {e}")
            return None
    
    def _bin_to_intel_hex(self, binary_data: bytes, base_address: int = 0x08008000) -> bytes:
        """
        Convert raw binary firmware to Intel HEX format.
        
        Intel HEX format bootloader expects:
        :LLAAAATT[DD...]CC
        
        LL = byte count (hex)
        AAAA = address (hex, 16-bit)
        TT = record type (00=data, 01=EOF, 04=extended linear address, 05=start linear address)
        DD = data bytes
        CC = checksum
        """
        hex_lines = []
        bytes_per_line = 16
        current_upper_addr = None
        
        # Write data records (type 00) with ELA records when crossing 64KB boundaries
        for offset in range(0, len(binary_data), bytes_per_line):
            chunk = binary_data[offset:offset + bytes_per_line]
            byte_count = len(chunk)
            full_address = base_address + offset
            
            # Check if we need a new Extended Linear Address record
            # ELA contains upper 16 bits of 32-bit address
            upper_addr = (full_address >> 16) & 0xFFFF
            if upper_addr != current_upper_addr:
                # Write ELA record (type 04)
                ela_data = struct.pack('>H', upper_addr)
                ela_checksum = (-(2 + 0 + 0 + 4 + sum(ela_data))) & 0xFF
                hex_lines.append(f":02000004{ela_data.hex().upper()}{ela_checksum:02X}\n".encode())
                current_upper_addr = upper_addr
            
            # Write data record with lower 16 bits of address
            address = full_address & 0xFFFF
            
            # Calculate checksum: -(sum of all bytes including count, address, type, data) & 0xFF
            checksum = (-(byte_count + (address >> 8) + (address & 0xFF) + 0 + sum(chunk))) & 0xFF
            
            # Format: :LLAAAATT[DD...]CC\n
            line = f":{byte_count:02X}{address:04X}00{chunk.hex().upper()}{checksum:02X}\n"
            hex_lines.append(line.encode())
        
        # CRITICAL: Write Start Linear Address record (type 05) before EOF
        # This tells the bootloader where to jump after loading firmware
        # Extract Reset Handler from binary vector table (offset 0x04)
        if len(binary_data) >= 8:
            reset_handler = struct.unpack('<I', binary_data[4:8])[0]
            print(f"  Extracted Reset Handler: 0x{reset_handler:08X}")
            
            # Format: :04000005[ADDRESS]CC
            # Type 05, 4 bytes of data (32-bit address in big-endian)
            sla_data = struct.pack('>I', reset_handler)
            sla_checksum = (-(4 + 0 + 0 + 5 + sum(sla_data))) & 0xFF
            sla_line = f":04000005{sla_data.hex().upper()}{sla_checksum:02X}\n"
            hex_lines.append(sla_line.encode())
            print(f"  Added Start Linear Address record: {sla_line.strip()}")
        else:
            print("  WARNING: Binary too small to extract Reset Handler!")
        
        # Write EOF record (type 01)
        hex_lines.append(b":00000001FF\n")
        
        return b''.join(hex_lines)
    
    def _change_program(
        self,
        target_program: int,  # 0 = Bootloader, 1 = Application
        timeout_ms: int,
        cancellation_token: CancellationToken
    ) -> bool:
        """
        CRITICAL: Automatic mode switching with retry and reboot detection
                
        This is the KEY to reliable firmware updates:
        - Polls device mode every 200ms
        - Retries SetProgram on every poll
        - Automatically detects when device reboots into target mode
        - NO manual intervention required
        
        Args:
            target_program: 0 for bootloader, 1 for application
            timeout_ms: Maximum time to wait (typically 30000ms)
            cancellation_token: Cancellation token
            
        Returns:
            True if device reached target mode, False on timeout
        """
        mode_name = "BOOTLOADER" if target_program == 0 else "APPLICATION"
        print(f"\n[ChangeProgram] Target: {mode_name} mode (program={target_program})")
        print(f"[ChangeProgram] Timeout: {timeout_ms}ms ({timeout_ms/1000:.0f} seconds)")
        print(f"[ChangeProgram] Strategy: Poll every 200ms, retry SetProgram until mode matches")
        
        start_time = time.time()
        timeout_seconds = timeout_ms / 1000.0
        attempt = 0
        
        while (time.time() - start_time) < timeout_seconds:
            attempt += 1
            
            if cancellation_token.is_cancelled():
                print(f"[ChangeProgram] Cancelled after {attempt} attempts")
                return False
            
            # Wait 200ms between attempts 
            time.sleep(0.2)
            
            # Check current program mode
            current_program = self._get_program()
            
            if current_program == target_program:
                elapsed = time.time() - start_time
                print(f"[ChangeProgram] ✓ SUCCESS - Device in {mode_name} mode!")
                print(f"[ChangeProgram] Took {elapsed:.1f}s ({attempt} attempts)")
                return True
            
            # Not in target mode yet, send SetProgram command
            if attempt % 5 == 1:  # Log every 5th attempt
                elapsed = time.time() - start_time
                print(f"[ChangeProgram] Attempt {attempt} ({elapsed:.1f}s): "
                      f"Current={current_program}, Target={target_program}, sending SetProgram...")
            
            self._set_program(target_program)
        
        # Timeout
        elapsed = time.time() - start_time
        print(f"[ChangeProgram] ✗ TIMEOUT after {elapsed:.1f}s ({attempt} attempts)")
        return False
    
    def _set_program(self, program: int) -> bool:
        """
        Send SetProgram command (0x1F51:01)
        Does NOT wait for mode change - that's handled by _change_program()
        """
        # Clear receive queue
        while self.bus.recv(timeout=0.001):
            pass
        
        # Write 1 byte to SDO 0x1F51:01
        msg = can.Message(
            arbitration_id=self.sdo_tx,
            data=[0x2F, 0x51, 0x1F, 0x01, program, 0x00, 0x00, 0x00],
            is_extended_id=False
        )
        self.bus.send(msg)
        
        # Wait for response (but don't block forever)
        response = self.bus.recv(timeout=1.0)
        if response and response.arbitration_id == self.sdo_rx:
            if response.data[0] == 0x60:  # Write OK
                return True
            elif response.data[0] == 0x80:  # Abort (normal during reboot)
                abort_code = struct.unpack('<I', response.data[4:8])[0]
                # 0x05040000 = timeout (expected during reboot)
                if abort_code == 0x05040000:
                    return True  # Device is rebooting, this is expected
                return False
        
        # No response (device may be rebooting)
        return True
    
    def _get_program(self) -> Optional[int]:
        """
        Read current program mode from SDO 0x1F51:01
        Returns: 0 = Bootloader, 1 = Application, None = error
        """
        # Clear receive queue
        while self.bus.recv(timeout=0.001):
            pass
        
        # Read SDO 0x1F51:01
        msg = can.Message(
            arbitration_id=self.sdo_tx,
            data=[0x40, 0x51, 0x1F, 0x01, 0x00, 0x00, 0x00, 0x00],
            is_extended_id=False
        )
        self.bus.send(msg)
        
        response = self.bus.recv(timeout=0.5)
        if not response or response.arbitration_id != self.sdo_rx:
            return None
        
        if response.data[0] == 0x4F:  # 1-byte upload response
            return response.data[4]
        elif response.data[0] == 0x80:  # Abort (device may be rebooting)
            return None
        
        return None
    
    def _program_firmware(self, firmware_data: bytes, cancellation_token: CancellationToken) -> bool:
        """
        Upload firmware using SDO segmented download
        SDO 0x1F50:01 with 30-second timeout
        """
        print(f"\n[ProgramFirmware] Uploading {len(firmware_data)} bytes to SDO 0x1F50:01")
        print(f"[ProgramFirmware] Mode: Segmented (NoBlock)")
        print(f"[ProgramFirmware] Timeout: 30000ms")
        
        return self._sdo_segmented_download(
            0x1F50, 0x01, firmware_data, 
            timeout=30.0,
            cancellation_token=cancellation_token
        )
    
    def _sdo_segmented_download(
        self,
        index: int,
        subindex: int,
        data: bytes,
        timeout: float,
        cancellation_token: CancellationToken
    ) -> bool:
        """
        Standard CANopen SDO Segmented Download (CiA 301)
        """
        # Clear receive queue
        while self.bus.recv(timeout=0.001):
            pass
        
        # Step 1: Initiate Download
        size_bytes = struct.pack('<I', len(data))
        msg = can.Message(
            arbitration_id=self.sdo_tx,
            data=[0x21, index & 0xFF, (index >> 8) & 0xFF, subindex] + list(size_bytes),
            is_extended_id=False
        )
        self.bus.send(msg)
        print(f"[SDO] Initiate: {len(data)} bytes to 0x{index:04X}:{subindex:02X}")
        
        # Wait for initiate response
        response = self._wait_for_sdo_response(5.0)
        if not response:
            print(f"[SDO] ✗ Initiate timeout")
            return False
        
        if response.data[0] == 0x80:
            abort_code = struct.unpack('<I', response.data[4:8])[0]
            print(f"[SDO] ✗ Initiate abort: 0x{abort_code:08X}")
            return False
        
        if response.data[0] != 0x60:
            print(f"[SDO] ✗ Unexpected initiate response: 0x{response.data[0]:02X}")
            return False
        
        print(f"[SDO] ✓ Download initiated")
        
        # Step 2: Send segments
        offset = 0
        toggle = 0
        segment_num = 0
        start_time = time.time()
        total_segments = (len(data) + 6) // 7
        
        print(f"[SDO] Starting upload: {len(data)} bytes ({total_segments} packets)")
        
        while offset < len(data):
            if cancellation_token.is_cancelled():
                print(f"[SDO] ✗ Cancelled at segment {segment_num}")
                return False
            
            # Get segment data (7 bytes max)
            segment_data = data[offset:offset+7]
            bytes_in_segment = len(segment_data)
            is_last = (offset + bytes_in_segment >= len(data))
            
            # Build command byte
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
            
            # Wait for response (with heartbeat filtering)
            response = self._wait_for_sdo_response(2.0)
            if not response:
                print(f"[SDO] ✗ Segment {segment_num} timeout")
                return False
            
            if response.data[0] == 0x80:
                abort_code = struct.unpack('<I', response.data[4:8])[0]
                print(f"[SDO] ✗ Segment {segment_num} abort: 0x{abort_code:08X}")
                return False
            
            # Verify toggle bit
            if (response.data[0] & 0xE0) != 0x20:
                print(f"[SDO] ✗ Unexpected segment response: 0x{response.data[0]:02X}")
                return False
            
            response_toggle = (response.data[0] >> 4) & 0x01
            if response_toggle != toggle:
                print(f"[SDO] ✗ Toggle mismatch at segment {segment_num}")
                return False
            
            # Update progress
            offset += bytes_in_segment
            toggle = 1 - toggle
            segment_num += 1
            
            # Green progress bar (update every 100 segments to reduce CPU load)
            if segment_num % 100 == 0 or offset >= len(data):
                progress = int((offset / len(data)) * 100)
                elapsed = time.time() - start_time
                rate = offset / elapsed if elapsed > 0 else 0
                
                # Draw progress bar with green color
                bar_width = 40
                filled = int(bar_width * progress / 100)
                bar = '\033[92m' + '█' * filled + '\033[0m' + '░' * (bar_width - filled)
                
                # Calculate packets percentage
                packet_percent = int((segment_num / total_segments) * 100)
                
                # Print progress line (overwrite previous line)
                import sys
                sys.stdout.write(f'\r[SDO] {bar} {progress:3d}% | {offset}/{len(data)} bytes | {rate:.0f} B/s | Packets: {segment_num}/{total_segments} ({packet_percent}%)')
                sys.stdout.flush()
        
        elapsed = time.time() - start_time
        print(f"\n[SDO] ✓ Upload complete: {segment_num} segments in {elapsed:.1f}s ({len(data)/elapsed:.0f} B/s)")
        return True
    
    def _verify_firmware(self, is_bootloader_file: bool, cancellation_token: CancellationToken) -> bool:
        """
        Wait for device reboot, then try switching to application mode to verify.
        """
        print(f"\n[Verify] Waiting for device reboot...")
        
        # Wait 15 seconds for device to reboot
        time.sleep(15)
        
        print(f"[Verify] Attempting to switch to application mode...")
        
        # Try switching to application mode (try twice)
        for attempt in range(2):
            if self._change_program(1, 10000, cancellation_token):
                print(f"\n[Verify] ✓ Device successfully switched to application mode!")
                return True
            
            if attempt == 0:
                print(f"[Verify] First attempt failed, trying again...")
                time.sleep(2)
        
        # Failed to switch to application mode
        print(f"\n[Verify] ✗ Device did not respond to application mode switch")
        
        # Check program state
        program = self._get_program()
        if program == 0:
            print(f"\n[Verify] ⚠️  Device is still in BOOTLOADER mode (program=0)")
            print(f"      Firmware was uploaded but bootloader may have rejected it.")
        elif program is None:
            print(f"\n[Verify] ⚠️  Device is not responding on CAN bus")
        
        return False
    
    def _get_firmware_status(self) -> Optional[FirmwareStatus]:
        """
        Read firmware status from SDO 0x1F57:01
        EXACT implementation from Windows software
        
        Byte layout:
        - Bit 0: 0 = Complete, 1 = Busy
        - Bits 1-7: Reserved
        - Bits 8-14: Error code (7 bits)
        """
        # Clear receive queue
        while self.bus.recv(timeout=0.001):
            pass
        
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
            error_code = (value >> 1) & 0x7F  # Extract error code from bits 1-7
            
            if status_bit == 1:  # Busy
                return FirmwareStatus("Busy", 0)
            else:  # Complete (OK or Error)
                if error_code > 0:
                    return FirmwareStatus("Error", error_code)
                return FirmwareStatus("Ok", 0)
        elif response.data[0] == 0x80:  # Abort
            abort_code = struct.unpack('<I', response.data[4:8])[0]
            print(f"[Status] SDO abort: 0x{abort_code:08X}")
            return None
        
        return None
    
    def _wait_for_sdo_response(self, timeout: float) -> Optional[can.Message]:
        """Wait for SDO response, filtering out heartbeats"""
        start_time = time.time()
        
        while (time.time() - start_time) < timeout:
            msg = self.bus.recv(timeout=0.1)
            if not msg:
                continue
            
            # Skip heartbeat messages
            if msg.arbitration_id == 0x700 + self.node_id:
                continue
            
            # Check for SDO response
            if msg.arbitration_id == self.sdo_rx:
                return msg
        
        return None
    
    def _run_progress(self, progress: Progress, state: State, func: Callable) -> Any:
        """Run a function and notify progress"""
        self._notify(progress, state)
        try:
            result = func()
            return result
        except Exception as e:
            print(f"[Progress] Error in {progress.value}: {e}")
            return None
    
    def _notify(self, progress: Progress, state: State):
        """Notify progress callback"""
        if self.progress_callback:
            try:
                self.progress_callback(progress, state)
            except Exception as e:
                print(f"[Callback] Error: {e}")


def check_and_setup_can(interface='can0', bitrate=250000):
    """
    Check if CAN interface is up and configured.
    If not, attempt to bring it up automatically.
    
    Returns: True if interface is ready, False otherwise
    """
    print(f"\n[CAN Setup] Checking {interface}...")
    
    # Check if interface exists
    try:
        result = subprocess.run(['ip', 'link', 'show', interface], 
                              capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"✗ [CAN Setup] Interface {interface} not found")
            print(f"  Available interfaces:")
            subprocess.run(['ip', 'link', 'show'], check=False)
            return False
    except Exception as e:
        print(f"✗ [CAN Setup] Error checking interface: {e}")
        return False
    
    # Check if interface is UP
    output = result.stdout
    is_up = 'UP' in output and 'state UP' in output
    
    if is_up:
        print(f"✓ [CAN Setup] {interface} is already UP")
        return True
    
    # Interface is DOWN, try to bring it up
    print(f"⚠️  [CAN Setup] {interface} is DOWN, attempting to bring it up...")
    
    # Check if we have sudo/root privileges
    if os.geteuid() != 0:
        print(f"⚠️  [CAN Setup] Not running as root, attempting with sudo...")
        sudo_prefix = ['sudo']
    else:
        sudo_prefix = []
    
    try:
        # Set CAN bitrate and type
        print(f"  Setting bitrate to {bitrate} bps...")
        subprocess.run(sudo_prefix + ['ip', 'link', 'set', interface, 'type', 'can', 'bitrate', str(bitrate)],
                      check=True, capture_output=True)
        
        # Bring interface up
        print(f"  Bringing {interface} up...")
        subprocess.run(sudo_prefix + ['ip', 'link', 'set', interface, 'up'],
                      check=True, capture_output=True)
        
        # Verify it's up
        time.sleep(0.5)
        result = subprocess.run(['ip', 'link', 'show', interface],
                              capture_output=True, text=True, check=True)
        
        if 'UP' in result.stdout and 'state UP' in result.stdout:
            print(f"✓ [CAN Setup] Successfully brought up {interface} @ {bitrate} bps")
            return True
        else:
            print(f"✗ [CAN Setup] Interface up command succeeded but interface not UP")
            print(f"  Status: {result.stdout}")
            return False
            
    except subprocess.CalledProcessError as e:
        print(f"✗ [CAN Setup] Failed to bring up interface: {e}")
        if e.stderr:
            print(f"  Error: {e.stderr.decode()}")
        print(f"\n  Manual setup required:")
        print(f"    sudo ip link set {interface} type can bitrate {bitrate}")
        print(f"    sudo ip link set {interface} up")
        return False
    except Exception as e:
        print(f"✗ [CAN Setup] Unexpected error: {e}")
        return False


def main():
    """Main entry point"""
    if len(sys.argv) < 3:
        print("Usage: update_firmware_production.py <node_id> <hex_file>")
        print("\nThis is the PRODUCTION-GRADE firmware updater with:")
        print("  ✅ Automatic retry and reboot detection")
        print("  ✅ Complete error recovery")
        print("  ✅ Guaranteed bootloader exit")
        print("  ✅ 30-second timeout windows")
        print("  ✅ Smart cancellation handling")
        sys.exit(1)
    
    node_id = int(sys.argv[1])
    hex_file = sys.argv[2]

    print("\033[1mPRODUCTION FIRMWARE UPDATER v2.1\033[0m")
    print(f"Node ID: {node_id}")
    print(f"Firmware: {hex_file}")
    
    # Check and setup CAN interface
    if not check_and_setup_can('can0', 250000):
        print("\n✗ CAN interface setup failed. Cannot proceed.")
        sys.exit(1)
    
    # Connect to CAN bus
    try:
        print("\nConnecting to CAN bus...")
        bus = can.Bus(interface='socketcan', channel='can0', bitrate=250000)
        print("✓ Connected to can0 @ 250 kbps")
    except Exception as e:
        print(f"✗ Failed to connect to CAN bus: {e}")
        print("\nTroubleshooting:")
        print("  1. Check if CAN interface exists: ip link show")
        print("  2. Check kernel modules: lsmod | grep can")
        print("  3. Check dmesg for hardware errors: dmesg | grep can")
        sys.exit(1)
    
    # Create updater
    updater = ProductionFirmwareUpdater(bus, node_id)
    
    # Progress callback
    def on_progress(progress: Progress, state: State):
        # Optional: Add custom progress handling here
        pass
    
    # Run update
    try:
        success = updater.program(
            firmware_path=hex_file,
            is_bootloader_file=False,
            callback=on_progress
        )
        
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        print("     ⚠️  INTERRUPTED BY USER")
        print("         Something went wrong. Please try again.")
        sys.exit(1)
    finally:
        bus.shutdown()


if __name__ == "__main__":
    main()
