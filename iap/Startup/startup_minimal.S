/*
 * Minimal Startup for IAP Bootloader
 *
 * This startup code does NOT copy .highcode to RAM.
 * It only sets up gp, sp, and jumps to main.
 * This ensures RAM 0x20000000 is clean for the App's vector table.
 */

    .section .init,"ax",@progbits
    .global _start
    .align 1
_start:
    j handle_reset

    .section .handle_reset,"ax",@progbits
    .global handle_reset
    .align 1
handle_reset:
    /* Initialize gp */
    .option push
    .option norelax
    la gp, __global_pointer$
    .option pop

    /* Initialize sp */
    la sp, _eusrstack

    /* Clear bss section */
    la a0, _sbss
    la a1, _ebss
    bgeu a0, a1, 2f
1:
    sw zero, 0(a0)
    addi a0, a0, 4
    bltu a0, a1, 1b
2:

    /* Copy data section from Flash to RAM */
    la a0, _data_lma
    la a1, _data_vma
    la a2, _edata
    bgeu a1, a2, 4f
3:
    lw t0, 0(a0)
    sw t0, 0(a1)
    addi a0, a0, 4
    addi a1, a1, 4
    bltu a1, a2, 3b
4:

    /* Disable interrupts - IAP doesn't need them */
    li t0, 0x80
    csrw 0x800, t0

    /* Jump to main (not mret, just a regular call) */
    call main

    /* Should never return */
5:
    j 5b
