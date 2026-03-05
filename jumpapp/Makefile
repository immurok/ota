# immurok CH592F JumpIAP - Minimal Bootloader
#
# This creates a minimal bootloader that just jumps to IAP at 0x6D000

TARGET = immurok_JumpIAP

# Toolchain
TOOLCHAIN_PATH ?= /opt/riscv-wch-gcc
TOOLCHAIN_BIN = $(TOOLCHAIN_PATH)/bin
CC = "$(TOOLCHAIN_BIN)/riscv-wch-elf-gcc"
OBJCOPY = "$(TOOLCHAIN_BIN)/riscv-wch-elf-objcopy"
SIZE = "$(TOOLCHAIN_BIN)/riscv-wch-elf-size"

BUILD_DIR = build

ASM_SOURCES = startup.S

MCU_FLAGS = -march=rv32imac -mabi=ilp32

ASFLAGS = $(MCU_FLAGS) -x assembler-with-cpp

LDSCRIPT = Link.ld
LDFLAGS = $(MCU_FLAGS) -T$(LDSCRIPT) -nostartfiles -nostdlib

OBJECTS = $(addprefix $(BUILD_DIR)/,$(notdir $(ASM_SOURCES:.S=.o)))

all: $(BUILD_DIR)/$(TARGET).hex $(BUILD_DIR)/$(TARGET).bin size

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
	@echo "=== JumpIAP Size ==="
	@$(SIZE) $<
	@echo ""
	@xxd -l 16 $(BUILD_DIR)/$(TARGET).bin

clean:
	@rm -rf $(BUILD_DIR)
	@echo "Clean done"

.PHONY: all clean size
