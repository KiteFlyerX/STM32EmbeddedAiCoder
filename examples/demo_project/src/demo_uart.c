/*
 * demo_uart.c — UART 输出桩实现(供 tokenbase 索引 / 编译占位)
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (c) KiteFlyerX
 */
#include "demo_uart.h"

static uint32_t s_baud;

void uart_init(uint32_t baudrate)
{
    s_baud = baudrate;
    (void)s_baud;
}

int uart_send(const uint8_t *data, uint32_t len)
{
    (void)data;
    (void)len;
    return (int)len;
}
