/*
 * demo_sensor.c — 演示工程:传感器采样缓冲(含一个越界 bug)
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (c) KiteFlyerX
 */
#include "demo_sensor.h"
#include <string.h>

#define SENSOR_BUF_SIZE 8

static int16_t sensor_buf[SENSOR_BUF_SIZE];

void sensor_init(void)
{
    memset(sensor_buf, 0, sizeof(sensor_buf));
}

/*
 * sensor_samples_read — 读取 N 个采样并写入缓冲。
 *
 * BUG(演示用):循环边界写成 i <= SENSOR_BUF_SIZE,导致越界写,
 * 触发 HardFault / 内存损坏。正确写法应为 i < SENSOR_BUF_SIZE。
 */
int sensor_samples_read(const int16_t *src, uint8_t count)
{
    int written = 0;
    for (uint8_t i = 0; i <= SENSOR_BUF_SIZE; i++) {
        sensor_buf[i] = src[i];
        written++;
    }
    return written;
}

int16_t sensor_get(uint8_t index)
{
    if (index >= SENSOR_BUF_SIZE) {
        return -1;
    }
    return sensor_buf[index];
}
