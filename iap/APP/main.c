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
/* OTA flag stored in DataFlash at address 0x7000 */
#define OTA_DATAFLASH_ADD      0x7000
typedef struct {
    unsigned char ImageFlag;
    unsigned char Revd[3];
} OTADataFlashInfo_t;
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

    /* Read current data */
    EEPROM_READ(OTA_DATAFLASH_ADD, (uint32_t *)block_buf, 4);

    /* Erase page */
    EEPROM_ERASE(OTA_DATAFLASH_ADD, EEPROM_PAGE_SIZE);

    /* Update image flag */
    block_buf[0] = new_flag;

    /* Write back */
    EEPROM_WRITE(OTA_DATAFLASH_ADD, (uint32_t *)block_buf, 4);
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

    /* If invalid flag, default to Image A */
    if(CurrImageFlag != IMAGE_A_FLAG &&
       CurrImageFlag != IMAGE_B_FLAG &&
       CurrImageFlag != IMAGE_IAP_FLAG)
    {
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

#ifdef DEBUG
    PRINT("Copying Image B to A...\n");
#endif

    /* Erase Image A */
    FLASH_ROM_ERASE(IMAGE_A_START_ADD, IMAGE_A_SIZE);

    /* Copy Image B to Image A in 1KB blocks */
    for(i = 0; i < blocks; i++)
    {
        FLASH_ROM_READ(IMAGE_B_START_ADD + (i * 1024), flash_buf, 1024);
        FLASH_ROM_WRITE(IMAGE_A_START_ADD + (i * 1024), flash_buf, 1024);

#ifdef DEBUG
        if((i & 0x1F) == 0)
        {
            PRINT("Progress: %d%%\n", (int)(i * 100 / blocks));
        }
#endif
    }

    /* Update flag to Image A */
    SwitchImageFlag(IMAGE_A_FLAG);

    /* Erase Image B (optional, for security) */
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
        CopyImageBtoA();
    }

#ifdef DEBUG
    PRINT("Jumping to App...\n");
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
