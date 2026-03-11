/*
 * OTA Protocol Definitions for immurok CH592F IAP Bootloader
 * Shared with main application
 */

#ifndef __OTA_H
#define __OTA_H

#include "CH59x_common.h"

/* Flash block size */
#define FLASH_BLOCK_SIZE       EEPROM_BLOCK_SIZE  /* 4KB */
#define IMAGE_SIZE             (216 * 1024)       /* 216KB per image */

/* Image A (current application) */
#define IMAGE_A_FLAG           0x01
#define IMAGE_A_START_ADD      (4 * 1024)         /* 0x00001000 */
#define IMAGE_A_SIZE           IMAGE_SIZE

/* Image B (OTA target) */
#define IMAGE_B_FLAG           0x02
#define IMAGE_B_START_ADD      (IMAGE_A_START_ADD + IMAGE_SIZE)  /* 0x00037000 */
#define IMAGE_B_SIZE           IMAGE_SIZE

/* Image IAP (bootloader) - this program */
#define IMAGE_IAP_FLAG         0x03
#define IMAGE_IAP_START_ADD    (IMAGE_B_START_ADD + IMAGE_SIZE)  /* 0x0006D000 */
#define IMAGE_IAP_SIZE         (12 * 1024)

/* OTA DataFlash address (legacy, kept for reference) */
#define OTA_DATAFLASH_ADD      (0x00076000 - FLASH_ROM_MAX_SIZE)

/* OTA Flag in Flash ROM (bypasses BLE library EEPROM cache) */
#define OTA_FLAG_FLASH_ADDR    (IMAGE_B_START_ADD + IMAGE_B_SIZE - 4)  /* 0x6CFFC */
#define OTA_FLAG_MAGIC         0x4F544103  /* "OTA\x03" = copy B→A needed */

/* OTA DataFlash structure */
typedef struct {
    unsigned char ImageFlag;
    unsigned char Revd[3];
} OTADataFlashInfo_t;

/* Current image flag */
extern unsigned char CurrImageFlag;

#endif /* __OTA_H */
