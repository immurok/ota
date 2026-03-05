/*
 * immurok CH592F JumpIAP - Minimal Bootloader
 *
 * This is the simplest possible bootloader - just one jump instruction.
 * It jumps directly to IAP at 0x6D000.
 *
 * Flash Layout (matches SDK BackupUpgrade):
 *   0x00000000 - 0x00001000: JumpIAP (4KB) <- This program
 *   0x00001000 - 0x00037000: IMAGE_A / App (216KB)
 *   0x00037000 - 0x0006D000: IMAGE_B / OTA (216KB)
 *   0x0006D000 - 0x00070000: IAP (12KB)
 */

    .section .init,"ax",@progbits
    .global _start
    .align 1
_start:
    j 0x6D000           /* Jump directly to IAP at 0x6D000 */
