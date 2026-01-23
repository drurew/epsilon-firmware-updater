# Forensic Analysis: The v1.3.5 Firmware Crisis

**Date:** January 23, 2026  
**Subject:** Root Cause Analysis of "Silent Boot Loop" in Epsilon V2  

---

## 1. Executive Summary
Forensic analysis of the Epsilon V2 firmware binary **v1.3.5** confirms a severe architectural flaw in the update process. The update successfully writes the new Application code but relies on a standard "Vector Table" mechanism that is incompatible with the preserved Bootloader layout.

This results in a **Vector Table Mismatch** where the CPU jumps to invalid memory addresses immediately after boot, causing a HardFault loop. The battery hardware is not damaged ("bricked"), but the software is trapped in an infinite crash cycle.

---

## 2. Technical Findings

### 2.1 The "Silent Boot Loop" Mechanism
The crash is deterministic and rooted in the ARM Cortex-M interrupt handling process.

1.  **Bootloader Handoff**: The Bootloader (Sector 0, `0x08000000`) runs and jumps to the Application Reset Vector (`0x08018201`).
2.  **Missing VTOR Update**: The Application (v1.3.5) code begins execution but fails to immediately write the new Vector Table address (`0x08018000`) to the `SCB->VTOR` register.
3.  **The Trigger**: Approximately 1ms later, the **SysTick** timer fires an interrupt.
4.  **The Crash**:
    *   The CPU uses the *Bootloader's* active Vector Table (usually at `0x00000000`).
    *   It retrieves the SysTick Handler address associated with the *old* firmware layout (v1.2.5: `0x0801A38F`).
    *   It jumps to `0x0801A38F`.
    *   **Fatal Mismatch**: In v1.3.5, the address `0x0801A38F` contains random code instructions, not the handler.
    *   **Result**: UsageFault / HardFault.

### 2.2 Memory Map Analysis
Comparing the HEX files reveals the shift:

| Vector | v1.2.5 (Old) | v1.3.5 (New) | Status |
| :--- | :--- | :--- | :--- |
| **Reset Vector** | `0x08018201` | `0x08018201` | **Identical** (Allows initial boot) |
| **SysTick Handler** | `0x0801A38F` | `0x0801A537` | **MOVED** (Causes crash) |
| **HardFault Handler**| `0x0801A2F5` | `0x0801A49D` | **MOVED** |

### 2.3 The "Degradation" Lockout (NVM Policy)
Beyond the crash, v1.3.5 enforces a "One-Strike" policy on voltage errors.
*   **Trigger**: Any historical record of low voltage.
*   **Action**: Writes `Degraded` status to NVM and **Locks the Settings Filter**.
*   **Impact**: Permanent disablement of the battery, requiring factory tools to unlock.

---

## 3. Recovery Strategy
Our recovery tool bypasses these issues by initiating a **Segmented Download** to restore the **v1.2.5 Firmware**.

*   **Why v1.2.5?**: Its Vector Table matches the layout expected by the factory Bootloader.
*   **Safety**: v1.2.5 is the officially certified firmware.
*   **Method**: Uses standard CANopen SDO (`0x1F50`), bypassing the fragile "Block Download" used by official tools.
