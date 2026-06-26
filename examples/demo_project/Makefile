# 演示工程 Makefile(占位,仅用于 tokenbase 索引;arm-gcc 不在 M1 必备项)
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) KiteFlyerX

CC      = arm-none-eabi-gcc
CFLAGS  = -mcpu=cortex-m4 -mthumb -O2 -g -Wall
SRC     = src/main.c src/demo_sensor.c src/demo_uart.c

all: demo.elf

demo.elf: $(SRC)
	$(CC) $(CFLAGS) $(SRC) -o $@

clean:
	rm -f demo.elf
