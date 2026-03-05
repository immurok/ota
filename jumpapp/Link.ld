/*
 * Linker Script for immurok CH592F JumpIAP
 *
 * Minimal bootloader at 0x0000 - just one jump instruction
 */

ENTRY( _start )

MEMORY
{
    FLASH (rx) : ORIGIN = 0x00000000, LENGTH = 4K
}

SECTIONS
{
    .init :
    {
        KEEP(*(SORT_NONE(.init)))
    } >FLASH
}
