# EXE 版工业缺陷检测系统技术实施方案

## 1. 建设目标

将当前“本地浏览器访问 FastAPI + React 页面”的运行方式改造成 Windows 桌面 EXE。用户启动程序后，把一个数据文件夹拖入程序窗口，程序自动完成：

`读取数据文件夹 -> 校验和识别数据组 -> 生成 DAS 图像 -> 提取 A 扫结果 -> YOLO 图像识别 -> A 扫与图像结果融合 -> 生成标注图和报告`

本次改造只改变运行形态和输入方式，不新增检测算法、业务判断、账号系统、云端服务或其他非必要功能。现有算法和输出证据必须继续保留。

目标运行方式：

- 用户只需要操作一个 EXE。
- 默认输入方式是将文件夹拖动到窗口。
- 不要求用户打开浏览器、启动前端开发服务器或手工填写 API 地址。
- 后端分析核心在本地进程内运行。
- 所有原始数据、中间结果、标注图、结构化结果和报告保存在本机任务目录。
- 处理过程中必须可以看到当前阶段、进度、错误和最终结果。

## 2. 当前项目必须保留的业务能力

以下能力属于现有业务核心，EXE 版必须保留，不得因删除网页端而删除：

### 2.1 普通工业图片检测

- 读取图片文件。
- 调用本地 YOLO 模型进行缺陷检测。
- 保存检测框、类别、置信度、像素坐标和必要的物理坐标。
- 生成带框标注图片。
- 保存缺陷记录到数据库。
- 生成 CSV、PDF 或现有报告格式。
- 支持人工查看现有检测结果。

核心代码主要位于：

- `backend/app/services/detection_service.py`
- `backend/app/main.py` 中的任务、检测、标注和报告逻辑
- `backend/app/services/report_service.py`

### 2.2 文件夹数据集分析

这是 EXE 版的主流程。必须保留现有 `analysis_service` 的处理顺序和结果含义：

- 识别单组数据目录或包含多组数据的父目录。
- 每个有效数据组默认由至少 16 个数字命名的 `.txt` 文件组成。
- 按组调用 `DAS.m` 生成 DAS 图像和相关矩阵结果。
- 按组调用 `Ascan.m` 生成 A 扫结果。
- 读取 `ascan_results.csv` 或现有支持的 A 扫结果。
- 从 DAS 输出重建用于识别的干净图像。
- 在该组图像生成后立即调用 YOLO。
- 将 YOLO 候选框、A 扫候选和 DAS 信息进行融合。
- 生成 `das_annotated` 标注图。
- 将每组结果汇总为任务级结果。
- 任务结束后生成可追溯报告。

核心代码：

- `backend/app/services/analysis_service.py`
- `backend/app/services/matlab_service.py`
- `backend/app/paut/realtime.py`

### 2.3 PAUT 数据处理

现有 PAUT 数据上传、DAS、A 扫和结果保存逻辑应保留在核心层。EXE 版不再通过网页上传 ZIP，而是把拖入的文件夹作为数据源；ZIP 只作为兼容性输入，不作为默认入口。

涉及代码：

- `backend/app/services/paut_service.py`
- `backend/app/paut/` 下的适配器、模型、DAS、A 扫和批次分析模块
- `backend/app/services/report_service.py`

### 2.4 Agent 辅助分析

Agent 不是 YOLO 检测的前置条件，必须继续保持解耦：

- 本地 A 扫、DAS、YOLO 结果先完成并保存。
- 如果配置了 OpenAI 兼容接口，再使用已保存的结构化证据进行辅助分析。
- 如果未配置外部大模型，使用现有安全降级结果，不影响本地检测、标注图和报告生成。
- Agent 失败不能导致检测任务整体失败。

核心代码：`backend/app/services/agent_service.py`。

## 3. EXE 版的最终用户流程

### 3.1 启动

1. 用户双击 EXE。
2. 程序创建本地运行目录、日志目录和输出目录。
3. 程序检查 YOLO 模型、Python 运行依赖、MATLAB 运行能力和必要文件。
4. 检查失败时显示明确的“缺少什么、如何处理”，不能只显示堆栈信息。
5. 检查通过后显示空闲状态和拖拽区域。

### 3.2 拖入文件夹

1. 用户将数据文件夹拖到窗口中央。
2. 程序取得该文件夹的绝对路径，不移动、不修改、不删除原始数据。
3. 程序扫描目录并建立数据清单。
4. 程序判断这是普通图片任务、PAUT 数据组、多个数据组父目录，或无法识别的目录。
5. 如果目录可以处理，自动创建任务并开始处理；不再要求用户点击多个“校验、开始、检测”按钮。
6. 如果目录不符合规则，显示缺失文件、实际发现的文件数和支持的目录示例。

### 3.3 自动处理

每个有效数据组按以下顺序处理：

1. 创建唯一 `job_id` 和 `group_id`。
2. 记录输入文件清单和文件指纹。
3. 执行 DAS 成像。
4. 执行 A 扫分析。
5. 确认用于 YOLO 的图像已经写入且可以读取。
6. 调用 YOLO。
7. 融合 YOLO、A 扫和 DAS 结果。
8. 绘制标注图。
9. 保存该组结果。
10. 更新界面进度，然后处理下一组。

任务级汇总和 Agent 辅助分析只能在所有数据组完成后执行。这样既能尽早显示每组图像，也能保证最终综合结论使用完整数据。

### 3.4 结束

任务完成后显示：

- 任务状态：完成、部分完成或失败。
- 有效组数、成功组数、失败组数。
- YOLO 检出数量和 A 扫候选数量。
- 标注图预览。
- 任务输出目录。
- 报告打开入口。
- 失败组的具体阶段和原因。

“部分完成”表示某一组的 YOLO 或 Agent 失败，但其他证据和结果仍已保存；不能把这种情况伪装成“无缺陷”。

## 4. 输入目录规范

### 4.1 默认输入

默认输入是文件夹，不是单张图片、手工路径或网页表单。程序应支持拖入目录本身；拖入目录中的文件时，应提示用户拖入文件夹。

### 4.2 支持的目录形态

至少支持以下两种现有形态：

```text
数据根目录/
  0001.txt
  0002.txt
  ...
  0016.txt
```

```text
数据根目录/
  group_001/
    0001.txt ... 0016.txt
  group_002/
    0001.txt ... 0016.txt
```

同时保留普通图片目录识别能力：

```text
图片目录/
  image_001.jpg
  image_002.png
```

目录扫描器必须复用或统一现有 `_find_data_groups` 的规则，不要在桌面层重复实现一套不同的判断逻辑。

### 4.3 输入约束

- 默认验收数据为 16 发 16 收数据。
- 数字 `.txt` 文件的通道顺序和格式继续由现有适配器处理。
- 原始数据只读。
- 软链接、临时文件、隐藏缓存文件和不支持扩展名不进入处理清单。
- 任务开始前保存清单，处理中不悄悄替换输入文件；如发现文件变化，任务应标记为输入被修改并停止或跳过该组。
- 数据格式不支持时必须在校验阶段失败，不应等到 MATLAB 或 YOLO 阶段才出现难以理解的错误。

## 5. 桌面程序架构

推荐采用“PySide6 原生桌面界面 + Python 分析核心”的单 EXE 架构：

```text
EXE
  ├─ Desktop UI（PySide6）
  ├─ Task Controller（任务状态和取消）
  ├─ Input Scanner（拖拽目录和数据识别）
  ├─ Existing Analysis Core（DAS/A扫/YOLO/融合/报告）
  ├─ Local Storage（SQLite、outputs、logs）
  └─ Runtime Dependency Check（模型、MATLAB、配置）
```

桌面界面不得通过 HTTP 请求本机 FastAPI，也不得启动浏览器。分析核心应通过普通 Python 函数、服务对象或任务控制器直接调用。

### 5.1 推荐新增目录

```text
desktop/
  main.py                 # EXE 入口
  app_window.py           # 主窗口、拖拽区、结果区
  task_controller.py      # 后台任务、进度、取消、错误
  input_scanner.py        # 输入目录扫描和分类
  view_models.py          # UI 使用的任务状态
  resources/              # 图标、默认配置
packaging/
  build_exe.ps1           # 可重复执行的打包脚本
  yolo_inspection.spec    # PyInstaller 配置
```

实际文件名可以调整，但职责必须保持清晰。不要把桌面事件、YOLO 推理、MATLAB 调用和数据库写入全部堆在一个窗口文件里。

### 5.2 任务控制器要求

任务控制器负责：

- 创建任务和组状态。
- 在后台线程或进程执行耗时工作，不能阻塞 UI。
- 发布 `scanning / das / ascan / yolo / fusion / reporting / completed / failed` 状态。
- 发布当前组、总组数、已完成组数和错误信息。
- 保证每组失败后能记录失败并继续其他组；只有输入无效或系统依赖缺失时才终止整个任务。
- 支持停止当前任务，并在安全边界结束后释放 MATLAB、模型和文件句柄。
- 任务取消后状态必须是 `cancelled`，不能显示为成功。

## 6. 需要修改的部分

### 6.1 新增桌面入口

新增 PySide6 主窗口，至少包含：

- 程序标题和当前任务状态。
- 大面积文件夹拖拽区域。
- 可选的“选择文件夹”按钮，仅作为拖拽失败时的辅助入口。
- 数据扫描结果摘要。
- 处理进度和当前阶段。
- 当前组的原图、DAS 图、标注图和关键结果。
- 任务错误列表。
- 输出目录和报告打开入口。
- “停止处理”按钮。

不要新增用户管理、在线升级、复杂配置中心、数据编辑器或与检测无关的页面。

### 6.2 抽取统一分析入口

新增一个统一入口，例如：

```python
run_folder_inspection(source_dir: Path, options: InspectionOptions) -> InspectionResult
```

它应负责调用现有服务，而不是复制算法。建议内部阶段为：

```text
scan_input
  -> classify_input
  -> create_job
  -> analyze_each_group
       -> run_das
       -> run_ascan
       -> build_yolo_image
       -> detect_image
       -> fuse_results
       -> render_annotated
  -> aggregate_job_result
  -> optional_agent_analysis
  -> create_report
```

普通图片目录可以进入现有图片检测分支；PAUT/数字 `.txt` 目录进入数据集分析分支。两个分支共享任务状态、输出目录、日志和报告接口。

### 6.3 统一结果模型

桌面层不能依赖网页返回 JSON 的偶然字段。应定义稳定的内部结果对象，至少包含：

- `job_id`
- `source_dir`
- `status`
- `input_type`
- `total_groups`
- `completed_groups`
- `failed_groups`
- `groups`
- `defect_count`
- `ascan_candidates`
- `annotated_images`
- `report_path`
- `errors`
- `started_at`、`finished_at`

每个组至少包含：

- `group_id`
- 原始文件清单
- DAS 原图和矩阵路径
- A 扫结果路径和结构化结果
- YOLO 原始结果
- 融合结果
- 标注图路径
- 当前组状态
- 失败阶段和错误信息

### 6.4 统一输出路径

建议将 EXE 的可写数据放到用户数据目录，而不是写入 EXE 安装目录：

```text
用户数据目录/
  jobs/
    {job_id}/
      input_manifest.json
      groups/
        {group_id}/
          raw_reference.json
          das/
          ascan/
          yolo/
          fusion/
          images/
            das.png
            das_annotated.jpg
      result.json
      report.pdf
      report.csv
  logs/
  config/
```

原始输入仍保留在用户选中的位置；输出目录保存引用、复制的必要中间结果和最终证据。所有界面图片必须使用标注图路径，不能误用原始 DAS 图路径。

### 6.5 统一日志和错误处理

每条错误至少带：

- `job_id`
- `group_id`
- 阶段名称
- 时间
- 输入路径
- 原始错误
- 用户可读的处理建议

重点错误必须单独处理：

- 输入目录不存在或无权限。
- 数据组少于 16 个有效通道文件。
- MATLAB 或 MATLAB Runtime 不可用。
- `DAS.m`、`Ascan.m` 或输出文件缺失。
- 输出图无法读取。
- YOLO 模型不存在、模型加载失败或推理失败。
- YOLO 正常运行但零检出。
- 数据库或报告写入失败。

YOLO 零检出和 YOLO 执行失败必须区分显示。零检出不是错误，也不能自动得出“无缺陷”。

## 7. 需要移除或退出运行链路的部分

以下内容不再作为 EXE 的运行依赖：

- `frontend/` 的 React 页面运行时。
- Vite 开发服务器和浏览器访问流程。
- 前端调用 `/api/...` 的 HTTP 客户端逻辑。
- 网页端的图片上传、ZIP 上传、手工输入目录路径作为主入口。
- 依赖 WebSocket 的实时页面刷新。
- 为网页服务而存在的静态文件 URL 拼接。

这些源码在首次 EXE 验收前可以保留在仓库中作为迁移参考，不能被 EXE 构建脚本打包，也不能成为运行时前置条件。验收完成后是否归档或删除属于独立清理任务，不应与本次迁移混在一起。

现有 FastAPI 路由也不应直接删除。建议第一阶段保留后端模块作为兼容测试入口，第二阶段确认桌面入口完全覆盖后再移除仅服务网页的路由。核心服务不能因为路由移除而删除。

## 8. 现有代码的保留、改造和暂不纳入范围

### 保留并复用

- `backend/app/services/analysis_service.py`
- `backend/app/services/detection_service.py`
- `backend/app/services/matlab_service.py`
- `backend/app/services/paut_service.py`
- `backend/app/services/report_service.py`
- `backend/app/services/agent_service.py`
- `backend/app/paut/` 中仍被测试和主流程使用的模块
- `models/best.pt`
- `algorithms/`、`knowledge/`、`validation/` 中被现有流程引用的资源
- `DAS.m`、`Ascan.m` 及其调用配置

### 需要改造

- `backend/app/config.py`：支持 EXE 资源路径和用户可写数据路径分离。
- `backend/app/services/matlab_service.py`：区分开发环境路径和打包后资源路径，提供启动前检查和可读错误。
- `backend/app/services/analysis_service.py`：抽取可被桌面任务控制器直接调用的统一入口，保留逐组完成后立即 YOLO 的顺序。
- `backend/app/services/detection_service.py`：确保模型只加载一次，保证结果和标注图路径稳定。
- `backend/app/services/report_service.py`：支持从统一任务结果生成报告。
- 新增 `desktop/`：实现桌面界面和任务控制器。
- 新增 `packaging/`：实现 PyInstaller 打包和资源收集。

### 暂不作为本次新增功能

- 新的缺陷类别或新模型训练。
- 新的 A 扫、DAS 数学算法。
- 新的设备通信协议。
- 云端推理或云端数据存储。
- 用户权限和账号体系。
- 远程协作、在线升级、数据库服务化。
- 复杂的实时硬件采集控制。

## 9. 实时 PAUT 模块的处理边界

当前实时 PAUT 路径与“拖入文件夹后一次性自动分析”不是同一个完整流程。现有 `backend/app/paut/realtime.py` 仍有实时帧管理、监听和批次概念，但部分路径中 `annotated_url` 仍可能指向原始图，且实时帧的 YOLO 结果并未完全接入实际推理。

EXE 迁移时必须做出以下处理：

- 将拖入文件夹的离线多组数据作为默认、可验收的主流程。
- 不把当前网页的实时监听按钮原样搬到桌面端。
- 如果保留实时模块入口，必须明确标为“实时/实验功能”，不能宣称与离线主流程同等完整。
- 在实时模块正式纳入 EXE 验收前，补齐“生成图 -> YOLO -> 标注图 -> 帧结果”的真实链路，并增加对应测试。
- 桌面主流程不能依赖实时模块才能完成 A 扫、DAS、YOLO 和报告。

## 10. 依赖和打包方案

### 10.1 打包方式

推荐使用 PyInstaller `onedir`，而不是第一阶段使用 `onefile`：

- YOLO 模型较大，`onedir` 启动和排错更稳定。
- MATLAB 脚本、字体、报告模板和动态库更容易明确收集。
- 出现模型或 DLL 问题时便于定位。
- 最终如确实需要单文件，再在功能验收通过后增加 `onefile` 构建。

最终交付可以是一个 EXE 入口加一个同目录运行包；这仍然是桌面 EXE 交付，不要求用户安装 Node.js 或打开浏览器。

### 10.2 资源路径

必须区分两类路径：

- 只读资源：模型、MATLAB 脚本、默认配置、知识库、报告模板，使用打包资源目录。
- 可写数据：任务输出、SQLite、日志、临时文件，使用用户数据目录。

不能把输出写到 PyInstaller 的临时解包目录，也不能假设当前工作目录就是项目根目录。

### 10.3 MATLAB 依赖

如果当前 `matlab_service.py` 依赖本机 MATLAB 命令，EXE 不能仅靠 PyInstaller 变成完全无依赖程序。交付方案必须二选一并在安装包中明确：

- 目标电脑预装 MATLAB，并配置可执行路径；或
- 使用 MATLAB Compiler 生成的 MATLAB Runtime，并把运行时检查、版本说明和安装步骤纳入交付包。

启动检查必须在用户拖入数据前完成。没有 MATLAB 能力时，程序可以查看历史结果，但不能开始需要 `DAS.m` 或 `Ascan.m` 的新任务。

### 10.4 YOLO 依赖

- 将 `models/best.pt` 作为明确的只读资源。
- 固定并验证 PyTorch、Ultralytics、图像处理库版本。
- 首次启动检查 CPU/GPU 设备能力。
- 不自动下载模型。
- 模型不存在或加载失败时给出明确错误。
- 正式模式不得静默切换到演示检测。

## 11. 配置处理

EXE 版保留现有环境变量和配置含义，但不要求普通用户手工编辑 `.env`：

- 首次启动从内置默认值创建用户配置。
- 模型路径、MATLAB 路径、输出根目录和 YOLO 置信度从用户配置读取。
- Agent 的 URL、模型名和密钥继续支持配置，但不在界面中新增复杂管理功能。
- 密钥不能写入任务报告、日志或结果 JSON。
- 配置错误必须在启动检查中显示。

## 12. 测试和验收标准

### 12.1 构建验收

- 在干净 Windows 环境安装后可以启动 EXE。
- 不安装 Node.js，不启动浏览器，不运行 Vite 也能使用。
- 资源路径、模型路径和用户输出路径均正确。
- MATLAB 依赖缺失时给出明确提示。

### 12.2 输入验收

- 拖入单组 16 发 16 收目录后自动开始。
- 拖入多组父目录后逐组开始并显示进度。
- 拖入普通图片目录后进入图片 YOLO 检测分支。
- 无效目录不会创建假成功任务。
- 原始输入文件内容不被修改。

### 12.3 流程验收

- 每组按 DAS -> A 扫 -> 图像准备 -> YOLO -> 融合 -> 标注图执行。
- 第一组完成后即可显示第一组结果，不必等待所有组结束。
- 所有组结束后才生成任务级汇总和 Agent 辅助分析。
- YOLO 零检出时仍保存 A 扫和 DAS 结果。
- YOLO 失败时组状态为部分完成或失败，并保留失败前结果。
- 标注图确实包含检测框时显示标注图；零检出时显示原图并标明“YOLO 无候选框”，不能误认为标注失败。

### 12.4 输出验收

- 每个任务都有唯一目录和 `result.json`。
- 每个数据组都有原始引用、DAS、A 扫、YOLO、融合和标注结果。
- 报告中的图片、数据和组编号可以相互追溯。
- 程序重启后可以查看历史任务结果。
- 日志可以定位到具体任务、组和阶段。

### 12.5 回归验收

- 现有后端单元测试继续通过。
- 现有 PAUT 算法验证脚本继续通过。
- 新增桌面任务控制器测试、目录扫描测试、资源路径测试和失败恢复测试。
- 至少使用一组真实或已确认的 16 发 16 收数据做端到端验收。
- 验收中必须检查生成图上是否有标注，不能只检查 API 返回成功。

## 13. 推荐实施顺序

1. 固定当前业务核心的输入、输出和测试基线。
2. 抽取统一文件夹分析入口和统一任务结果模型。
3. 修正 EXE 资源路径、用户可写路径和 MATLAB/YOLO 启动检查。
4. 新增 PySide6 拖拽窗口和后台任务控制器。
5. 接入普通图片和 PAUT 文件夹两条现有分析分支。
6. 接入结果预览、进度、错误、报告和历史任务读取。
7. 使用真实数据做端到端回归，特别检查标注图路径和 YOLO 阶段。
8. 用 PyInstaller 生成 `onedir` 测试包，在干净电脑验证。
9. 功能验收通过后，再决定是否移除网页端运行依赖和是否压缩为 `onefile`。

## 14. 给 AI 编程的执行约束

实施时必须遵守以下约束：

- 先读取现有代码和测试，再修改；不得用桌面层重写现有算法。
- 不得删除用户已有的未提交改动。
- 不得把耗时 MATLAB、YOLO、报告生成放到 UI 线程。
- 不得为了显示结果而直接使用原始图路径替代标注图路径。
- 不得将 YOLO 零检出解释为“确认无缺陷”。
- 不得在 YOLO 或 Agent 失败时丢弃 A 扫、DAS 和原始输入证据。
- 不得新增网页服务器作为 EXE 的隐含运行前提。
- 不得自动下载模型、脚本或外部依赖。
- 每次修改后运行现有测试，并对拖入单组、多组和无效目录分别做端到端验证。
- 若发现当前实时 PAUT 路径与上述主流程不一致，应标记为未纳入主流程并记录原因，不得伪造为已完成。

## 15. 最终交付定义

本项目的 EXE 版完成标准是：用户启动一个桌面程序，拖入一个符合规范的数据文件夹后，无需浏览器、无需手工填写路径、无需手工逐步点击，即可看到 DAS 图、A 扫结果、YOLO 识别结果、融合结果、带标注图和报告；发生异常时能知道具体是哪一组、哪个阶段、什么原因，并且已经完成的原始证据和中间结果不会丢失。

## 16. 阵元数量和位置的交互配置

### 16.1 目标行为

EXE 启动后，阵元数量和阵元位置显示默认值。用户可以在开始处理前修改配置；点击或触发“开始处理”时，程序先校验配置，再把配置传给 MATLAB。未修改时，行为必须与当前默认 16 阵元配置一致。

默认配置应集中定义一次，不能分别散落在桌面界面、Python 服务和多个 `.m` 文件中：

```text
probe_count: 16
tx_positions_mm: [30, 32, 34, 36, 38, 40, 42, 44, 49, 51, 53, 55, 57, 59, 61, 63]
rx_positions_mm: [30, 32, 34, 36, 38, 40, 42, 44, 49, 51, 53, 55, 57, 59, 61, 63]
```

对于当前收发阵元位置相同的探头，界面默认只显示一组“阵元数量”和“阵元位置”；提交时将同一组位置赋给 Tx 和 Rx。底层仍保留 `tx_positions_mm` 和 `rx_positions_mm` 两个字段，为以后收发位置不同留下兼容能力，但本次不新增复杂的独立收发配置页面。

### 16.2 EXE 界面

在拖拽区旁或处理参数区域增加一个简洁的“阵元配置”面板：

- 阵元数量：正整数输入框，默认 `16`。
- 阵元位置：一行逗号分隔的毫米数值，默认填入当前 16 个位置。
- 单位固定显示为 `mm`，不允许用户输入单位文字。
- 配置摘要显示“共 N 个阵元，位置范围 X 到 Y mm”。
- 可选的“恢复默认值”按钮属于配置必需操作，不是新增业务功能。

如果后续确实存在 Tx 和 Rx 不同的硬件布局，再增加“Tx 位置”和“Rx 位置”两行；第一版不要因为兼容性预先增加复杂界面。

### 16.3 Python 配置模型

新增一个统一配置对象，例如 `ProbeGeometry`，作为桌面层、任务控制器和分析服务之间的唯一格式：

```python
@dataclass(frozen=True)
class ProbeGeometry:
    probe_count: int
    tx_positions_mm: tuple[float, ...]
    rx_positions_mm: tuple[float, ...]

    def validate(self) -> None:
        if self.probe_count < 1:
            raise ValueError("阵元数量必须大于 0")
        if len(self.tx_positions_mm) != self.probe_count:
            raise ValueError("Tx 位置数量必须等于阵元数量")
        if len(self.rx_positions_mm) != self.probe_count:
            raise ValueError("Rx 位置数量必须等于阵元数量")
        if any(not math.isfinite(value) for value in self.tx_positions_mm + self.rx_positions_mm):
            raise ValueError("阵元位置必须是有限数字")
        if any(value < 0 for value in self.tx_positions_mm + self.rx_positions_mm):
            raise ValueError("阵元位置不能为负数")
        if len(set(self.tx_positions_mm)) != len(self.tx_positions_mm):
            raise ValueError("Tx 阵元位置不能重复")
        if len(set(self.rx_positions_mm)) != len(self.rx_positions_mm):
            raise ValueError("Rx 阵元位置不能重复")
```

实际实现可以使用 Pydantic，但必须保持相同约束。输入文本解析只允许逗号、中文逗号、空格或换行作为分隔符；解析失败时在界面提示具体的第几个值无效。

### 16.4 输入数据与阵元配置校验

提交任务前必须同时检查配置和数据：

1. 数据组中的 Tx 文件数量必须等于 `len(tx_positions_mm)`。
2. 每个 Tx 文件中的接收列数量必须等于 `len(rx_positions_mm)`。
3. 文件名代表的 Tx 位置应与配置位置匹配；如果当前数据格式使用通道编号而不是毫米位置，必须由适配器完成编号到数组索引的映射，不能在 MATLAB 中静默使用最近位置。
4. 如果数量不一致，任务不得开始 MATLAB 运行，应显示“配置数量、文件数量、每个文件接收列数量”三者的实际值。
5. 配置必须写入 `input_manifest.json`、任务 `result.json`、每组 `profile.json` 和报告，保证结果可追溯。

这里的“探头数目”必须明确为阵元数量，而不是 `.txt` 文件数量。当前数据结构通常是 `Tx 数量个文件 × 每文件 Rx 数量列`，因此不能简单把文件总数当成通道总数。

### 16.5 从 Python 传给 MATLAB

当前 `MatlabService` 已经支持通过环境变量传递参数。建议增加一个 JSON 参数，而不是把数组直接拼进 MATLAB 命令：

```json
{
  "probe_count": 16,
  "tx_positions_mm": [30, 32, 34, 36, 38, 40, 42, 44, 49, 51, 53, 55, 57, 59, 61, 63],
  "rx_positions_mm": [30, 32, 34, 36, 38, 40, 42, 44, 49, 51, 53, 55, 57, 59, 61, 63]
}
```

Python 在调用 `run_das`、`run_ascan`、`run_tofd` 和需要几何参数的 `run_realtime` 时传入：

```python
matlab_params["probe_geometry_json"] = json.dumps(
    {
        "probe_count": geometry.probe_count,
        "tx_positions_mm": list(geometry.tx_positions_mm),
        "rx_positions_mm": list(geometry.rx_positions_mm),
    },
    separators=(",", ":"),
)
```

`MatlabService._build_matlab_env` 将其写为 `MATLAB_PROBE_GEOMETRY_JSON`。需要注意：环境变量只适合传输序列化后的字符串；不要让 Python 列表直接依赖 `str(list)` 的格式，也不要把用户输入拼接进 `-batch` 命令。

### 16.6 MATLAB 脚本改造

在 `DAS.m`、`Ascan.m`、`TOFD.m` 和统一实时脚本的参数区，删除或保留为最后兜底的硬编码数组，改为优先读取 `MATLAB_PROBE_GEOMETRY_JSON`：

```matlab
geometry_json = getenv('MATLAB_PROBE_GEOMETRY_JSON');
if ~isempty(geometry_json)
    geometry = jsondecode(geometry_json);
    tx_positions = double(geometry.tx_positions_mm(:).');
    rx_positions = double(geometry.rx_positions_mm(:).');
else
    tx_positions = [30 32 34 36 38 40 42 44 49 51 53 55 57 59 61 63];
    rx_positions = tx_positions;
end

if length(tx_positions) ~= length(rx_positions)
    error('Tx 和 Rx 阵元数量必须一致。');
end
```

随后所有 `load_data`、DAS 延迟计算、A 扫 Tx/Rx 配对和 TOFD 计算都只能使用这两个变量。不能在脚本后面再次写死 16，也不能继续写死 `pair_tx`、`pair_rx` 为 8 组；这些配对必须根据当前 Tx/Rx 位置生成或由 Python 明确传入。

对于 Ascan：

- 如果算法要求对称的首尾配对，使用当前配置的首个 Tx 对最后一个 Rx、第二个 Tx 对倒数第二个 Rx，直到中间位置。
- 如果阵元数量为奇数，必须定义清楚是否保留中间自配对，不能靠 MATLAB 下标越界或静默少算一组。
- `n_pair` 必须由实际生成的配对数组计算。

对于 TOFD：

- 默认 `tofd_tx` 和 `tofd_rx` 不再固定为 `22`、`70`，而是使用配置中首尾阵元位置。
- 如果用户单独配置了 TOFD 通道，必须验证它们存在于当前 Tx/Rx 位置列表中。

### 16.7 分析服务的参数传递

`analysis_service.py` 当前已经有 `_normalize_params` 和 `_matlab_params`，应扩展为：

```python
params = {
    ...现有成像参数...,
    "probe_geometry": ProbeGeometry(...),
}
```

`_matlab_params` 负责把 `ProbeGeometry` 转为 JSON 字符串。`_analyze_group` 不应自行读取界面字段，也不应重新生成默认阵元位置；它只接收已经校验过的任务参数。

任务创建时就要把完整几何配置冻结下来。处理过程中用户修改界面输入不能影响正在运行的任务；下一次拖入或重新开始任务才使用新配置。

### 16.8 结果与图像坐标

阵元位置改变后，DAS 图的峰值位置、A 扫候选位置和 YOLO 像素到毫米的换算都可能改变。因此必须：

- 用 MATLAB 输出的 `x_img`、`z_img` 做图像坐标映射。
- 将本次阵元配置写入结果和报告。
- 不使用默认 `20~80 mm` 或默认 16 阵元去解释用户自定义配置的结果。
- 在结果页面显示当前使用的阵元数量和位置摘要。
- 如果 YOLO 识别在图像上没有候选框，仍保存无框标注图或明确的零检出状态，并保留 A 扫/DAS 证据。

### 16.9 需要修改的文件清单

本需求的最小修改范围是：

- 新增 `backend/app/paut/probe_geometry.py` 或等价的统一配置模型。
- 修改 `backend/app/services/analysis_service.py`：接收、冻结、校验并传递几何参数。
- 修改 `backend/app/services/matlab_service.py`：稳定序列化几何参数到 MATLAB 环境变量，并记录参数摘要。
- 修改 `algorithms/matlab/DAS.m`：读取并使用动态 Tx/Rx 位置。
- 修改 `algorithms/matlab/Ascan.m`：读取动态位置并动态生成 A 扫配对。
- 修改 `algorithms/matlab/TOFD.m`：使用动态位置和有效的首尾通道。
- 修改实时配置模型和实时 MATLAB 调用：保证已有 `tx_positions_mm`、`rx_positions_mm` 真正下发到 MATLAB。
- 新增或修改 `desktop/`：增加数量和位置输入控件、恢复默认、校验提示和任务参数冻结。
- 修改报告生成：加入本次阵元配置。
- 新增测试：默认配置回归、自定义 4/8/16 阵元、数量不匹配、重复位置、非数字位置、奇数阵元和 MATLAB 参数传递测试。

### 16.10 不能采用的实现方式

- 不能只在 EXE 界面显示输入框，但 MATLAB 仍使用硬编码数组。
- 不能直接修改 `DAS.m` 文件文本后再运行，避免并发任务互相覆盖和污染默认脚本。
- 不能把用户输入拼接到 MATLAB `-batch` 命令中，防止引号、路径和特殊字符造成执行错误。
- 不能只传阵元数量而不传位置；DAS 的延迟计算依赖实际位置。
- 不能只修改 DAS 而不修改 Ascan、TOFD 和实时脚本，否则同一任务的物理坐标会不一致。
- 不能在数量和数据通道数不一致时自动截断或补零并继续输出正式结果。
