/*
 * OTA Protocol Definitions V2 - IAP at 0x0000
 *
 * New Flash Layout:
 *   0x00000000 - 0x00004000: IAP Bootloader (16KB)
 *   0x00004000 - 0x0003A000: Image A / Application (216KB)
 *   0x0003A000 - 0x00070000: Image B / OTA Target (216KB)
 */

#ifndef __OTA_V2_H
#define __OTA_V2_H

#include "CH59x_common.h"

/* Flash block size */
#define FLASH_BLOCK_SIZE       EEPROM_BLOCK_SIZE  /* 4KB */
#define IMAGE_SIZE             (216 * 1024)       /* 216KB per image */

/* Image A (current application) - starts at 16KB */
#define IMAGE_A_FLAG           0x01
#define IMAGE_A_START_ADD      (16 * 1024)        /* 0x00004000 */
#define IMAGE_A_SIZE           IMAGE_SIZE

/* Image B (OTA target) */
#define IMAGE_B_FLAG           0x02
#define IMAGE_B_START_ADD      (IMAGE_A_START_ADD + IMAGE_SIZE)  /* 0x0003A000 */
#define IMAGE_B_SIZE           IMAGE_SIZE

/* Image IAP flag - signals upgrade needed */
#define IMAGE_IAP_FLAG         0x03

/* OTA DataFlash address */
#define OTA_DATAFLASH_ADD      (0x00076000 - FLASH_ROM_MAX_SIZE)

/* OTA DataFlash structure */
typedef struct {
    unsigned char ImageFlag;
    unsigned char Revd[3];
} OTADataFlashInfo_t;

/* Current image flag */
extern unsigned char CurrImageFlag;

#endif /* __OTA_V2_H */
