/*
 * main.c — 演示工程主程序(含一处空指针解引用 bug)
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (c) KiteFlyerX
 */
#include "demo_sensor.h"
#include "demo_uart.h"

static const int16_t fake_adc_data[10] = {
    100, 200, 300, 400, 500, 600, 700, 800, 900, 1000
};

static void app_log_fault(const char *msg)
{
    uart_send((const uint8_t *)msg, 64);
}

/*
 * app_run — 主循环。读取 10 个采样进 8 长度的缓冲(越界),
 * 随后解引用一个未初始化指针(空指针解引用)。
 */
void app_run(void)
{
    sensor_init();

    /* 越界:缓冲只有 8,这里传 10 且 sensor_samples_read 内部还会越界 */
    int n = sensor_samples_read(fake_adc_data, 10);
    (void)n;

    /* 空指针解引用(演示 bug #2) */
    volatile int *bad = (volatile int *)0;
    *bad = 0xDEADBEEF;

    app_log_fault("done");
}

int main(void)
{
    uart_init(115200);
    app_run();
    return 0;
}
