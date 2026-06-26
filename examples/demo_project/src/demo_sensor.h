/*
 * demo_sensor.h — 传感器采样接口
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (c) KiteFlyerX
 */
#ifndef DEMO_SENSOR_H
#define DEMO_SENSOR_H

#include <stdint.h>

void sensor_init(void);
int sensor_samples_read(const int16_t *src, uint8_t count);
int16_t sensor_get(uint8_t index);

#endif /* DEMO_SENSOR_H */
