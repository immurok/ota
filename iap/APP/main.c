/*
 * immurok CH592F IAP Bootloader
 *
 * This bootloader checks the ImageFlag in DataFlash:
 * - IMAGE_A_FLAG (0x01): Jump to Image A directly
 * - IMAGE_B_FLAG (0x02): Jump to Image A directly (legacy)
 * - IMAGE_IAP_FLAG (0x03): Copy Image B to Image A, then jump
 *
 * Flash Layout:
 *   0x00000000 - 0x00001000: Reserved / Bootloader vector (4KB)
 *   0x00001000 - 0x00037000: Image A (216KB) - Main application
 *   0x00037000 - 0x0006D000: Image B (216KB) - OTA download area
 *   0x0006D000 - 0x00070000: IAP Bootloader (12KB) - This program
 */

#include "CH59x_common.h"

#ifdef DEBUG
#include <stdarg.h>

// Minimal dbg_printf for IAP — supports %d %u %x %X %02X %s %%
static void uart_putc(char c)
{
    while (R8_UART3_TFC >= UART_FIFO_SIZE);
    R8_UART3_THR = c;
}

static void uart_puts(const char *s)
{
    while (*s) uart_putc(*s++);
}

static void uart_puthex(uint32_t v, int width, int upper)
{
    char tmp[8];
    int i = 0;
    if (v == 0) { tmp[i++] = '0'; }
    else {
        while (v) {
            int d = v & 0xF;
            tmp[i++] = d < 10 ? '0' + d : (upper ? 'A' : 'a') + d - 10;
            v >>= 4;
        }
    }
    while (i < width) tmp[i++] = '0';
    for (int j = i - 1; j >= 0; j--) uart_putc(tmp[j]);
}

static void uart_putdec(int v)
{
    if (v < 0) { uart_putc('-'); v = -v; }
    char tmp[10];
    int i = 0;
    if (v == 0) { tmp[i++] = '0'; }
    else {
        while (v) { tmp[i++] = '0' + v % 10; v /= 10; }
    }
    for (int j = i - 1; j >= 0; j--) uart_putc(tmp[j]);
}

int dbg_printf(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);

    while (*fmt) {
        if (*fmt != '%') { uart_putc(*fmt++); continue; }
        fmt++;
        int zero = 0, width = 0;
        if (*fmt == '0') { zero = 1; fmt++; }
        while (*fmt >= '0' && *fmt <= '9') { width = width * 10 + (*fmt - '0'); fmt++; }
        if (*fmt == 'l') fmt++;
        switch (*fmt) {
        case 'd': uart_putdec(va_arg(ap, int)); break;
        case 'u': { unsigned v = va_arg(ap, unsigned); uart_putdec((int)v); break; }
        case 'x': uart_puthex(va_arg(ap, unsigned), width, 0); break;
        case 'X': uart_puthex(va_arg(ap, unsigned), width, 1); break;
        case 's': uart_puts(va_arg(ap, const char *)); break;
        case '%': uart_putc('%'); break;
        case '\0': goto done;
        default: uart_putc('%'); uart_putc(*fmt); break;
        }
        fmt++;
    }
done:
    va_end(ap);
    return 0;
}
#endif

/* Use V2 layout if IAP_AT_ZERO is defined */
#ifdef IAP_AT_ZERO
/* V2 Layout: IAP at 0x0000, App at 0x4000
 *
 * Flash Layout:
 *   0x00000000 - 0x00004000: IAP Bootloader (16KB)
 *   0x00004000 - 0x0003A000: Image A / App (216KB)
 *   0x0003A000 - 0x00070000: Image B / OTA (216KB)
 */
#define IMAGE_A_START_ADD      (16 * 1024)        /* 0x00004000 */
#define IMAGE_A_SIZE           (216 * 1024)       /* 216KB */
#define IMAGE_B_START_ADD      (IMAGE_A_START_ADD + IMAGE_A_SIZE)  /* 0x0003A000 */
#define IMAGE_B_SIZE           IMAGE_A_SIZE
#define IMAGE_A_FLAG           0x01
#define IMAGE_B_FLAG           0x02
#define IMAGE_IAP_FLAG         0x03
/* OTA Flag in Flash ROM (bypasses BLE library EEPROM cache) */
#define OTA_FLAG_FLASH_ADDR    (IMAGE_B_START_ADD + IMAGE_B_SIZE - 4)
#define OTA_FLAG_MAGIC         0x4F544103
#else
#include "ota.h"
#endif

/* Current image flag */
unsigned char CurrImageFlag = 0xFF;

/* Flash buffer for copy operation */
__attribute__((aligned(8))) static uint8_t flash_buf[1024];

/* Jump to application macro */
#define jumpApp    ((void (*)(void))((int *)IMAGE_A_START_ADD))

/*********************************************************************
 * @fn      SwitchImageFlag
 * @brief   Update image flag in DataFlash
 */
static void SwitchImageFlag(uint8_t new_flag)
{
    __attribute__((aligned(8))) uint8_t block_buf[16];

    EEPROM_READ(OTA_DATAFLASH_ADD, (uint32_t *)block_buf, 4);
    EEPROM_ERASE(OTA_DATAFLASH_ADD, EEPROM_PAGE_SIZE);
    block_buf[0] = new_flag;
    EEPROM_WRITE(OTA_DATAFLASH_ADD, (uint32_t *)block_buf, 4);

#ifdef DEBUG
    uint8_t verify = 0xFF;
    EEPROM_READ(OTA_DATAFLASH_ADD, (uint32_t *)block_buf, 4);
    verify = block_buf[0];
    PRINT("  Flag: 0x%02X -> verify: 0x%02X %s\n",
          new_flag, verify, (verify == new_flag) ? "OK" : "FAIL!");
#endif
}

/*********************************************************************
 * @fn      ReadImageFlag
 * @brief   Read image flag from DataFlash
 */
static void ReadImageFlag(void)
{
    OTADataFlashInfo_t info;

    EEPROM_READ(OTA_DATAFLASH_ADD, &info, 4);
    CurrImageFlag = info.ImageFlag;

#ifdef DEBUG
    PRINT("OTA_DATAFLASH_ADD=0x%x\n", OTA_DATAFLASH_ADD);
    PRINT("Raw flag: 0x%02X (bytes: %02X %02X %02X %02X)\n",
          CurrImageFlag, info.ImageFlag, info.Revd[0], info.Revd[1], info.Revd[2]);
#endif

    if(CurrImageFlag != IMAGE_A_FLAG &&
       CurrImageFlag != IMAGE_B_FLAG &&
       CurrImageFlag != IMAGE_IAP_FLAG)
    {
#ifdef DEBUG
        PRINT("Invalid flag, defaulting to IMAGE_A\n");
#endif
        CurrImageFlag = IMAGE_A_FLAG;
    }

#ifdef DEBUG
    PRINT("Image Flag: 0x%02X\n", CurrImageFlag);
#endif
}

/*********************************************************************
 * @fn      CopyImageBtoA
 * @brief   Copy Image B to Image A (for OTA upgrade)
 */
static void CopyImageBtoA(void)
{
    uint32_t i;
    uint32_t blocks = IMAGE_A_SIZE / 1024;
    uint8_t status;

#ifdef DEBUG
    PRINT("Copying Image B (0x%x) to A (0x%x), %d KB...\n",
          IMAGE_B_START_ADD, IMAGE_A_START_ADD, IMAGE_A_SIZE / 1024);
#endif

    /* Erase Image A */
    status = FLASH_ROM_ERASE(IMAGE_A_START_ADD, IMAGE_A_SIZE);
#ifdef DEBUG
    PRINT("  Erase A: %d\n", status);
#endif

    /* Copy Image B to Image A in 1KB blocks */
    for(i = 0; i < blocks; i++)
    {
        FLASH_ROM_READ(IMAGE_B_START_ADD + (i * 1024), flash_buf, 1024);
        status = FLASH_ROM_WRITE(IMAGE_A_START_ADD + (i * 1024), flash_buf, 1024);

        if(status != 0)
        {
#ifdef DEBUG
            PRINT("  Write FAIL at block %d: %d\n", (int)i, status);
#endif
        }

#ifdef DEBUG
        if((i & 0x3F) == 0)
        {
            PRINT("  %d/%d (%d%%)\n", (int)i, (int)blocks, (int)(i * 100 / blocks));
        }
#endif
    }

    /* Update flag to Image A */
    SwitchImageFlag(IMAGE_A_FLAG);

    /* Erase Image B */
    FLASH_ROM_ERASE(IMAGE_B_START_ADD, IMAGE_B_SIZE);

#ifdef DEBUG
    PRINT("Copy complete!\n");
#endif
}

/*********************************************************************
 * @fn      JumpToApp
 * @brief   Jump to main application
 *
 * Critical: We must disable all interrupts and set mtvec to App's
 * entry before jumping. Otherwise, if an exception occurs during
 * App's startup (while it's copying .highcode to RAM), the CPU
 * will jump to a partially-overwritten vector table and crash.
 */
static void JumpToApp(void)
{
    uint32_t irq_status;

    /* If IAP flag is set, copy Image B to A first */
    if(CurrImageFlag == IMAGE_IAP_FLAG)
    {
        /* Validate Image B before copying — if Image B is empty (all 0x00
         * or 0xFF), copying would destroy the working Image A.
         * This happens when wlink flash writes the combined hex (Image B
         * area is zero-filled) but DataFlash retains IMAGE_IAP_FLAG from
         * a previous OTA. */
        uint32_t img_b_header;
        FLASH_ROM_READ(IMAGE_B_START_ADD, (uint8_t *)&img_b_header, 4);
#ifdef DEBUG
        PRINT("Image B header: 0x%08X\n", (unsigned int)img_b_header);
#endif
        if(img_b_header != 0x00000000 && img_b_header != 0xFFFFFFFF)
        {
            CopyImageBtoA();
        }
        else
        {
#ifdef DEBUG
            PRINT("Image B is empty, skip copy! Fixing flag...\n");
#endif
            SwitchImageFlag(IMAGE_A_FLAG);
        }
    }
#ifdef DEBUG
    else
    {
        PRINT("No copy needed (flag=0x%02X)\n", CurrImageFlag);
    }
#endif

#ifdef DEBUG
    PRINT("Jumping to App @ 0x%x...\n", IMAGE_A_START_ADD);
    DelayMs(5);
#endif

    /* Disable all interrupts */
    SYS_DisableAllIrq(&irq_status);

    /* Disable global interrupt enable */
    PFIC_DisableAllIRQ();

    /* Set mtvec to App entry point before jumping */
    /* This ensures any exception during App startup goes to App's code */
    __asm volatile("csrw mtvec, %0" :: "r"(IMAGE_A_START_ADD | 0x3));

    /* Memory barrier to ensure all writes complete */
    __asm volatile("fence.i");
    __asm volatile("fence");

    /* Jump to App */
    jumpApp();
}

/*********************************************************************
 * @fn      main
 * @brief   IAP Bootloader entry point
 */
int main(void)
{
    /* Setup system clock */
    SetSysClock(CLK_SOURCE_PLL_60MHz);

#ifdef DEBUG
    /* Setup debug UART (PA5=TX, PA4=RX) */
    GPIOA_SetBits(GPIO_Pin_5);
    GPIOA_ModeCfg(GPIO_Pin_5, GPIO_ModeOut_PP_5mA);
    GPIOA_ModeCfg(GPIO_Pin_4, GPIO_ModeIN_PU);
    UART3_DefInit();

    PRINT("\n=== immurok IAP Bootloader ===\n");
#endif

    /* Read image flag from DataFlash */
    ReadImageFlag();

    /* Jump to application */
    JumpToApp();

    /* Should never reach here */
    while(1);
}
