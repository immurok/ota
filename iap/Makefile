# immurok CH592F IAP Bootloader Makefile
#
# This bootloader is responsible for:
# - Checking ImageFlag in DataFlash
# - Copying Image B to Image A if IAP flag is set
# - Jumping to main application
#
# Build: make           (V1: IAP at 0x6D000)
# Build: make V2=1      (V2: IAP at 0x0000, acts as bootloader)
# Clean: make clean

# Project name
TARGET = immurok_IAP

# Toolchain
TOOLCHAIN_PATH ?= /opt/riscv-wch-gcc
TOOLCHAIN_BIN = $(TOOLCHAIN_PATH)/bin
CC = "$(TOOLCHAIN_BIN)/riscv-wch-elf-gcc"
OBJCOPY = "$(TOOLCHAIN_BIN)/riscv-wch-elf-objcopy"
OBJDUMP = "$(TOOLCHAIN_BIN)/riscv-wch-elf-objdump"
SIZE = "$(TOOLCHAIN_BIN)/riscv-wch-elf-size"

# SDK paths (use main firmware's SDK)
SDK_PATH = ../../firmware/SDK/EVT/EXAM
SRC_PATH = $(SDK_PATH)/SRC

# Directories
BUILD_DIR = build
APP_DIR = APP

# Source files (minimal for bootloader)
C_SOURCES = \
	$(APP_DIR)/main.c \
	$(SRC_PATH)/StdPeriphDriver/CH59x_gpio.c \
	$(SRC_PATH)/StdPeriphDriver/CH59x_sys.c \
	$(SRC_PATH)/StdPeriphDriver/CH59x_clk.c \
	$(SRC_PATH)/StdPeriphDriver/CH59x_uart3.c \
	$(SRC_PATH)/StdPeriphDriver/CH59x_flash.c \
	$(SRC_PATH)/RVMSIS/core_riscv.c

# Always use minimal startup (no .highcode copy, no vector table)
# This keeps RAM 0x20000000 clean for the App's vector table
ASM_SOURCES = \
	Startup/startup_minimal.S

# Include paths
C_INCLUDES = \
	-I$(APP_DIR)/include \
	-I$(SRC_PATH)/StdPeriphDriver/inc \
	-I$(SRC_PATH)/RVMSIS

# Defines
C_DEFS = \
	-DCH592

# V2 layout option (IAP at 0x0000)
ifdef V2
    C_DEFS += -DIAP_AT_ZERO
    TARGET = immurok_IAP_V2
endif

# Debug build option
ifdef DEBUG
    C_DEFS += -DDEBUG=3
endif

# Compiler flags
MCU_FLAGS = -march=rv32imac_zicsr -mabi=ilp32 -msmall-data-limit=8

CFLAGS = $(MCU_FLAGS) $(C_DEFS) $(C_INCLUDES)
CFLAGS += -std=gnu99 -Os -ffunction-sections -fdata-sections -fno-common
CFLAGS += -Wall -Wno-unused-function
ifdef DEBUG
    CFLAGS += -g
endif

ASFLAGS = $(MCU_FLAGS) $(C_DEFS) $(C_INCLUDES)
ASFLAGS += -x assembler-with-cpp

# Linker flags
ifdef V2
    LDSCRIPT = Ld/Link_Boot.ld
else
    LDSCRIPT = Ld/Link.ld
endif
LIBS = -L$(SRC_PATH)/StdPeriphDriver -lISP592 -lm
LDFLAGS = $(MCU_FLAGS)
LDFLAGS += -T$(LDSCRIPT)
LDFLAGS += -nostartfiles -Xlinker --gc-sections
LDFLAGS += -Wl,-Map=$(BUILD_DIR)/$(TARGET).map,--cref
LDFLAGS += --specs=nano.specs --specs=nosys.specs
LDFLAGS += $(LIBS)

# Object files
OBJECTS = $(addprefix $(BUILD_DIR)/,$(notdir $(C_SOURCES:.c=.o)))
vpath %.c $(sort $(dir $(C_SOURCES)))

OBJECTS += $(addprefix $(BUILD_DIR)/,$(notdir $(ASM_SOURCES:.S=.o)))
vpath %.S $(sort $(dir $(ASM_SOURCES)))

# Build targets
all: $(BUILD_DIR)/$(TARGET).elf $(BUILD_DIR)/$(TARGET).hex $(BUILD_DIR)/$(TARGET).bin size

$(BUILD_DIR)/%.o: %.c Makefile | $(BUILD_DIR)
	@echo "CC $<"
	@$(CC) -c $(CFLAGS) $< -o $@

$(BUILD_DIR)/%.o: %.S Makefile | $(BUILD_DIR)
	@echo "AS $<"
	@$(CC) -c $(ASFLAGS) $< -o $@

$(BUILD_DIR)/$(TARGET).elf: $(OBJECTS) Makefile
	@echo "LD $@"
	@$(CC) $(OBJECTS) $(LDFLAGS) -o $@

$(BUILD_DIR)/$(TARGET).hex: $(BUILD_DIR)/$(TARGET).elf
	@echo "HEX $@"
	@$(OBJCOPY) -O ihex $< $@

$(BUILD_DIR)/$(TARGET).bin: $(BUILD_DIR)/$(TARGET).elf
	@echo "BIN $@"
	@$(OBJCOPY) -O binary -S $< $@

$(BUILD_DIR):
	@mkdir -p $@

size: $(BUILD_DIR)/$(TARGET).elf
	@echo ""
	@echo "=== IAP Bootloader Memory Usage ==="
	@$(SIZE) $<
	@echo ""
ifdef V2
	@echo "Note: V2 layout - Must be < 16KB to fit in bootloader region"
else
	@echo "Note: V1 layout - Must be < 12KB to fit in IAP region"
endif

clean:
	@rm -rf $(BUILD_DIR)
	@echo "Clean done"

# Debug build
debug:
	@$(MAKE) DEBUG=1 all

# V2 build (IAP at 0x0000)
v2:
	@$(MAKE) V2=1 all

# V2 debug build
v2-debug:
	@$(MAKE) V2=1 DEBUG=1 all

.PHONY: all clean size debug v2 v2-debug
