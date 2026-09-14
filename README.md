# 工业图像缺陷智能检测系统

本项目支持两类流程：现有工业图片的 YOLO 检测，以及相控阵原始 A扫/FMC 数据的接入、DAS 成像和后续联合分析。当前 PAUT 模块处于 COMSOL 仿真数据验证阶段，优先处理团队确认的 16发16收 数据组。

## 桌面版（推荐）

桌面版使用 PySide6 QtWebEngine 承载项目 React 界面，并在后台自动启动仅监听本机的 FastAPI 服务。最终用户不需要安装 Python、Node.js 或浏览器。

开发电脑第一次配置：

```powershell
.\setup_desktop_dev.ps1
```

以后开发运行只需双击或执行：

```powershell
.\run_desktop_dev.ps1
```

生成便携目录和压缩包：

```powershell
.\build_desktop.ps1
```

输出位于 `dist/BXSJ/` 和 `dist/BXSJ-portable-v0.1.1.zip`。交付后解压并双击 `BXSJ.exe`。开发版配置和数据默认保存在项目目录；便携版配置、数据库、上传和报告默认保存在 EXE 同级目录，方便整体复制和清理。

MATLAB 缺失不会阻止桌面程序启动，只会停用 PAUT 成像。当前可使用 MATLAB Engine 或 `matlab.exe`；团队以后用 MATLAB Compiler 提供 Runtime 组件后，在“应用设置”中配置组件模块即可切换。现有 `DAS.m`、`Ascan.m` 和其他成像脚本没有被改写。

Agent API、模型名、MATLAB 路径、YOLO 模型和输出目录均在“应用设置”中填写。API Key 保存到当前运行目录的 `config/secrets.env`，该目录已被 Git 忽略；保存设置后会在当前程序中立即生效。

## PAUT 实时模块（第一阶段）

前端“PAUT 数据流成像”页面目前使用本地目录模拟设备输入。默认 profile 为 `comsol_16x16_das_m`，参数来自 `DAS.m`：16个发射、16个接收、阵元位置 `[30,32,34,36,38,40,42,44,49,51,53,55,57,59,61,63] mm`，钢中纵波声速 `5900 m/s`，板厚 `40 mm`，采样间隔 `2e-8 s`，成像范围 `x=20-70 mm`、`z=0-40 mm`，包络后默认使用 `sigma=0.4 mm` 二维高斯平滑。非16发16收数据暂不进入本阶段验收。

页面既可选择单组 `tx_N` 目录，也可选择包含多组采集目录的父目录：

COMSOL平铺格式使用16个以Tx物理位置命名的文件，默认顺序为
`30,32,34,36,38,40,42,44,49,51,53,55,57,59,61,63 mm`。每个文件应包含
`tx_pos、Time、16个Rx通道`共18列；`tx_pos`允许使用m或mm，程序会校验单位及文件名的一致性。
数据目录或其同级存在合法的 `none` 目录时，系统自动将其作为公共背景参考并从检测帧中排除；
实际采用的背景路径和比例会写入帧结果包，供Agent、报告和实验复现使用。

```text
一组采集数据/
  tx_1/001.txt ...
  tx_2/001.txt ...

多组仿真数据/
  数据组1/tx_1/001.txt ...
  数据组2/tx_1/001.txt ...
```

每个文本文件支持“时间、幅值”两列或单列幅值。当前 `comsol_text_folder` 适配器会将其统一为 `[tx, rx, sample]`，其他设备格式后续通过新增适配器接入，DAS、YOLO 和 Agent 不依赖设备文件格式。

操作顺序：进入“PAUT 数据流成像”，填写单组16发16收数据目录或多组数据的父目录，点击“校验数据源”，再点击“开始处理”。父目录中的16发16收数据组会按名称依次成像；主画面默认自动显示最新成像，也可由操作者明确暂停，暂停只冻结显示，不会停止后台处理。停止处理并等待YOLO队列清空后，点击“分析本次采集”统一执行Agent综合分析。

YOLO/YOLO-Seg 后处理默认忽略图像顶部 8% 的候选区域，用于过滤贴近入射波源/上边缘的常见误检；需要调整时修改 `YOLO_IGNORE_TOP_RATIO`，设为 `0` 可关闭。

仿真真值来自目录名：`(0.035,0.025)-(0.045,0.02)` 表示两点连线裂纹，换算为 `[35,25] mm` 到 `[45,20] mm`；`d5mm(0.05,0.02)` 表示以 `[50,20] mm` 为中心、直径 `5 mm` 的圆形气孔。含义不明确的数据只标记为待确认。

实时接口：

- `POST /api/realtime/config`：配置并校验数据源和成像参数。
- `POST /api/realtime/start`、`POST /api/realtime/stop`：启动或停止监听。
- `GET /api/realtime/status`：查询连接和处理状态。
- `GET /api/realtime/frames/latest`：查询最近一帧结果。
- `GET /api/realtime/frames/{frame_id}`：按帧查询可追溯结果。
- `GET /api/realtime/frames`：查询最近帧，支持前端固定查看历史帧。
- `POST /api/realtime/batches`：立即冻结尚未入批的帧并提交后台分析。
- `GET /api/realtime/batches`、`GET /api/realtime/batches/{batch_id}`：查询持久化批次结果。
- `POST /api/realtime/batches/{batch_id}/retry`：重试失败或中断批次。
- `WS /api/realtime/ws`：订阅状态与帧更新。

每份数据的原始 A扫、DAS 矩阵、显示图、YOLO候选框、A扫物理特征、profile 和仿真真值均保存在 `./data/outputs/realtime/{frame_id}/`。所有证据使用同一个 `frame_id`，防止图像与原始波形错配。

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

当前联合分析包括：

- DAS内部最强聚焦位置；
- 聚焦位置对应的发射-接收通道回波一致性；
- 自发自收通道的门控回波、信噪比和主频；
- 预计底波是否落入采样时间窗；
- YOLO候选框从图像像素到DAS横向位置和深度的换算；
- 停止采集后的整批Agent辅助解释。

实时监听期间不启动Agent。停止监听后，系统冻结本次会话的全部A扫、DAS和YOLO结果，再创建一个独立分析批次；模型未检出时系统不会输出“无缺陷”结论。使用演示检测时会明确标识，演示框不参与缺陷证据判断。

A扫单帧不能直接判断缺陷长度、走向、类型和真实尺寸。采集结束后，LangGraph 会以A扫候选位置、响应强度、通道支撑和跨帧持续性为主证据，按空间位置形成彼此独立的二维候选；DAS和YOLO-Seg只用于辅助定位及估计当前x-z垂直截面内的长度、宽度与朝向。图像提示附近没有A扫响应时仅保留为复核线索，不单独形成主候选；没有分割边界时只报告“A扫响应范围”，不冒充缺陷真实长度。

长链在调用大模型前先运行本地确定性风险节点，按低、中、高、严重四级给出复核优先级及“继续使用并按计划复检、复检、维修评估、停用待评估”等辅助建议。分级综合A扫强度、通道支撑、跨帧持续性、多个独立候选和二维范围；大模型只能解释，不能修改本地等级。系统同时列出疲劳扩展、泄漏、承载下降、腐蚀减薄或焊缝失效等条件性风险，以及材料、载荷、环境、校准和验收标准等缺失条件。该等级不是产品标准验收等级，不构成自动判废。

实时模式按通道文件实际内容计算数据源指纹，可识别覆盖写时文件大小和时间戳未变化的情况。单组覆盖目录未变化时明确显示正在等待设备写入；多组父目录显示已处理组数和总组数。DAS与A扫完成后立即发布，YOLO在独立后台队列处理；YOLO为0或失败时，采集后批次仍使用A扫与DAS证据分析。

## PAUT仿真批量验证

使用项目内清单批量验证全部仿真A扫数据：

```powershell
.\.venv\Scripts\python.exe .\scripts\validate_paut.py --data-root "你的仿真数据根目录"
```

默认只验证当前阶段的16发16收COMSOL数据；非16发16收数据会记录为跳过，不进入本阶段误差统计。输出保存在 `./data/validation/`：

- `comsol_16x16_validation.json`：DAS.m逻辑复现、仿真真值、峰值距离、目标邻域响应和风险说明；
- `comsol_16x16_validation.csv`：适合表格查看的16发16收验证摘要；
- 各数据集子目录：Python DAS预览图和数值矩阵。

如果需要旧清单模式，可显式运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\validate_paut.py --mode manifest --data-root "你的仿真数据根目录"
```

旧清单模式输出：

- `validation_report.json`：完整参数、定位结果、MATLAB对照和风险说明；
- `validation_report.csv`：适合表格查看的定位误差摘要；
- 各数据集子目录：Python DAS预览图和数值矩阵。

清单位于 `validation/simulation_manifest.json`。`confirmed_by_DAS_m` 只表示阵元坐标与现有 `DAS.m` 一致；`assumed` 表示文件未提供阵元坐标，只能作暂定验证。目录名推断的缺陷位置也单独标记，未得到COMSOL模型或建模人员确认前不作为正式真值。

当前未提供MATLAB `.mat` 输出文件，因此验证报告不会声明与MATLAB数值矩阵完全一致，只说明Python按新版 `DAS.m` 逻辑复现后的结果与仿真文件名真值之间的距离。报告同时区分“全局最强峰”和“目标邻域响应”，避免把非缺陷强反射误当作缺陷位置。

结构化仿真实例库位于 `knowledge/paut_simulation_cases.json`。原始数据中可识别43个案例，但知识库只选择13个典型原型供Agent参考：3个裂纹、3个气孔、4个未熔合和3个夹渣。选择覆盖短/长/多裂纹，小/中/大孔群，坡口/根部/层间未熔合，以及浅/中/深层夹渣；全量案例仍由独立验证流程管理。实例库记录相应二维真值、FMC维度、采样参数和验证状态，不保存个人电脑绝对路径。需要从团队完整仿真数据根目录重新生成时运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_simulation_case_library.py --data-root "仿真数据目录" --validation-json .\data\validation\comsol_16x16_validation.json --output .\knowledge\paut_simulation_cases.json
```

案例匹配只用于相似性解释和算法验证，不会被当作真实工件的缺陷类型、风险或验收阈值。风险分级的使用边界和权威来源记录在 `knowledge/paut_risk_assessment_basis.md`。

## 浏览器开发模式

需要分别调试前后端时，第一次安装环境：

```powershell
cd <项目目录>
powershell -ExecutionPolicy Bypass -File .\scripts\setup_all.ps1
```

以后启动浏览器开发模式：

```powershell
cd <项目目录>
powershell -ExecutionPolicy Bypass -File .\scripts\run_all.ps1
```

浏览器访问：

```text
http://127.0.0.1:5173
```

## 基本流程

1. 上传图片或图片集，可同时上传同名 JSON 元数据。
2. 检查坐标标定信息；没有比例尺时系统只输出像素坐标。
3. 点击“开始检测”，系统调用 `models/best.pt` 进行 YOLO 推理。
4. 在结果表格中确认、标记误检或人工补录缺陷。
5. 生成 PDF 报告、导出缺陷明细或运行 Agent 分析。

## 元数据格式

图片和 JSON 文件应同名，例如：

```text
weld_001.jpg
weld_001.json
```

推荐 JSON：

```json
{
  "scan_area": "焊缝A区",
  "probe_or_channel": "PAUT通道1",
  "image_note": "当前图片内坐标，不包含小车全局位置",
  "mm_per_pixel": 0.05,
  "calibration_method": "手动比例",
  "calibration_note": "比例尺来源说明"
}
```

字段说明：

- `scan_area`：扫查区域或图片对应部位。
- `probe_or_channel`：探头、通道或设备输出通道，未知可留空。
- `image_note`：图片备注。
- `mm_per_pixel`：每像素代表多少毫米。没有比例尺时设为 `null` 或留空。
- `calibration_method`：`未标定`、`手动比例` 或 `两点比例尺`。

前端也提供“下载元数据模板”按钮。没有 JSON 时，可以上传后在“坐标标定”区域手动选择“手动比例”或“两点比例尺”。如果整批图片的扫查区域、探头/通道、备注或比例尺一致，可以使用“应用到整批”一次性保存到全部图片。当前系统只计算图片内位置和尺寸，不推断小车全局位置。

## 配置

推荐保持相对路径，方便交付到其他电脑：

```env
APP_DATA_DIR=./data
UPLOAD_DIR=./data/uploads
OUTPUT_DIR=./outputs
DATABASE_URL=sqlite:///./data/app.db
YOLO_MODEL_PATH=./models/best.pt
DEMO_DETECTION=false
```

真实 YOLO 推理依赖由安装脚本装入项目 `.venv`。不要依赖个人电脑上的外部 Python 环境。

## Agent 分析

Agent 用于辅助复核和报告措辞建议，不替代持证检测人员结论。

```env
AGENT_ENABLED=true
OPENAI_API_KEY=你的APIKey
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
AGENT_LANGUAGE=zh-CN
AGENT_SEND_PROJECT_CONTEXT=true
WEB_SEARCH_ENABLED=true
WEB_SEARCH_SEND_PROJECT_CONTEXT=false
WEB_SEARCH_PROVIDER=duckduckgo
```

默认策略：

- 缺陷结果会发送给你配置的大模型 API，用于结合当前批次分析。
- Agent按“检测对象、标准适用性、证据链、缺失条件、结论边界、复核建议”输出，不把检测方法标准当作验收标准。
- 本地标准目录记录标准编号、现行状态、适用范围、用途和国家标准全文公开系统来源；报告只列与当前对象相关的候选标准。
- PAUT批次分析以多帧A扫为主要证据，图像分割只辅助二维边界量化；结果页只展示总体结论、二维候选、A扫依据、风险和复核建议，内部变量不直接展示给操作者。
- 引用来源由程序检索层提供，模型自行生成的标准链接不会进入分析结果。
- 联网搜索默认脱敏，只使用通用检索词，不发送客户名、项目名、图片名、坐标和尺寸。
- 网络不可用或搜索失败时，Agent 会降级为基于当前结果的通用复核建议。

## 报告

PDF 报告包含：

- 批次基本信息
- 范围
- 规范性引用文件
- 术语和定义
- 测试条件
- 对比试样和标定信息
- 测试内容与方法
- 测试结果与缺陷明细
- 典型缺陷图
- 结果评定与说明

报告不列出无关的软件路径和模型地址。长备注和长建议会自动换行；图片名称只在典型图和必要代表项中出现，避免报告表格被文件名撑乱。

报告按钮位于“Agent分析”页面。桌面版生成报告时会先弹出保存对话框，由用户选择PDF文件名和保存目录；实时监控页面不提供报告按钮。

## 注意事项

- `DEMO_DETECTION=false` 表示正式调用真实 YOLO；如果模型不存在，检测会明确失败并提示原因。
- 未标定图片只输出像素坐标，不会伪造图片内物理坐标或工件全局坐标。
- `.env`、`.venv`、`data/`、`models/*.pt` 已被 `.gitignore` 排除。
- 交付别人时建议提供源码、安装脚本、模型文件和 `.env.example`，由对方本机执行安装。
