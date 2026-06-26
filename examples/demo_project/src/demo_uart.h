/*
 * demo_uart.h — UART 输出接口(桩实现)
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (c) KiteFlyerX
 */
#ifndef DEMO_UART_H
#define DEMO_UART_H

#include <stdint.h>

void uart_init(uint32_t baudrate);
int uart_send(const uint8_t *data, uint32_t len);

#endif /* DEMO_UART_H */
