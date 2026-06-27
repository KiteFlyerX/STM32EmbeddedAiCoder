# STM32G0B1 采集板固件需求 v1

## 通信
- **USART3**:调试日志输出,TX=PB10, RX=PB11,115200 8N1
- **I2C1**:温湿度传感器 SHT40,SCL=PB6, SDA=PB7,400kHz

## 采样
- 传感器缓冲 `SENSOR_BUF_SIZE = 8`,每轮读取写入 sensor_buf
- 采样率 10Hz,通过 TIM6 触发

## 已知约束
- RS485(DIR=PA8)半双工,发送前需拉高 DIR
- LED(PC13)低电平点亮
