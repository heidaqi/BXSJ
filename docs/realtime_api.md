# PAUT 实时接口

所有接口前缀为 `/api/realtime`。当前接口用于“数据流式处理”，不是单帧智能判定：后端逐组处理16发16收 COMSOL 数据，前端异步读取已完成结果；每个 `frame_id` 绑定原始A扫、DAS数据、图像检测、真值解析、Agent结果和人工复核。

## 配置与运行

- `POST /config`：配置目录适配器、profile、声速、板厚、阵元位置、成像范围和批次策略。默认 `profile=comsol_16x16_das_m`，会套用 `DAS.m` 参数并只接受16发16收数据组。
- `POST /start`：启动目录数据流处理。父目录中的合格数据组会按名称依次处理。
- `POST /stop`：停止处理；当前处理中的数据组完成后退出。Agent分析必须在停止后启动。
- `GET /status`：返回连接状态、序列进度、YOLO队列长度、采集后分析可用状态、最近错误和处理耗时。
- `GET /ws`：WebSocket状态事件，轮询接口仍作为兼容方式保留。

## 帧查询与复核

- `GET /frames?limit=50`：最近帧摘要。
- `GET /frames/latest`：最新完整帧。
- `GET /frames/{frame_id}`：指定冻结帧。
- `GET /frames/{frame_id}/waveform?tx=0&rx=0&max_points=1200`：通道波形、包络和闸门。Tx/Rx使用从0开始的索引。
- `POST /frames/{frame_id}/focus`：按DAS物理点反查贡献通道，请求体示例：`{"x_mm": 35.0, "z_mm": 12.0, "top_k": 8}`。
- `PUT /frames/{frame_id}/review`：保存人工复核，请求体示例：`{"status":"已确认","reviewer":"张三","note":"结合A扫包络确认"}`。状态仅允许“待复核、已确认、误检”。

## 采集后分析

- `POST /batches`：冻结本次会话全部帧并启动整批Agent综合分析。处理运行中或YOLO尚未完成时返回错误；同一会话重复调用返回已创建的批次。
- `GET /batches`、`GET /batches/{batch_id}`：查看历史批次和分析状态。
- `POST /batches/{batch_id}/retry`：重试失败或中断的分析批次。
- `POST /batches/{batch_id}/report`：为冻结批次生成报告。

## 批次与报告

- `POST /batches`：冻结当前待分析帧并进入后台Agent队列。
- `GET /batches`、`GET /batches/{batch_id}`：读取批次及持久化结果。
- `POST /batches/{batch_id}/retry`：重试失败或中断批次。
- `POST /batches/{batch_id}/report`：根据冻结帧生成PDF，返回 `report_url`。

错误约定：参数或帧错误返回400，缺失批次返回404，证据帧缺失返回409，队列满返回429，报告写入失败返回507。外部Agent失败不删除本地A扫、DAS和YOLO结果。

## 单帧存储结构

```text
./data/outputs/realtime/{frame_id}/
  raw/raw_frame.npz
  das/das_data.npz
  das/das.png
  yolo/das_annotated.jpg
  yolo/detections.json
  analysis/ascan_features.json
  analysis/profile.json
  analysis/truth.json
  frame.json
```

`frame.json` 中的 `source_group` 记录数据组目录名，`truth` 记录由目录名解析出的裂纹线段或圆形气孔真值。A扫单帧不直接判定缺陷类型、长度、走向/倾角和真实尺寸，Agent只在批次层面综合多帧证据。
