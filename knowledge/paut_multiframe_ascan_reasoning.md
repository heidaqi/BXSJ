# PAUT多帧A扫/DAS辅助推理知识

## 适用边界

本文件只作为Agent辅助复核的本地知识，不作为验收标准、缺陷定性结论或自动判废依据。A扫单帧只能提供回波时间、幅值、包络、主频、信噪比和通道一致性等证据，不能单独确定缺陷长度、走向/倾角、类型或真实尺寸。上述属性必须结合多帧DAS/PAUT图像、扫查位置、焊缝几何、工艺资料、校准试块和人工复核。

## 权威依据摘要

- ASNT Materials Evaluation 2026 PAUT焊缝教程指出，PAUT可靠性来自扫查计划和多角度覆盖；平面型缺陷的反射强烈依赖声束与缺陷面的相对方向，不能假设相控阵自动保证覆盖。
- FHWA PAUT技术说明指出，PAUT可通过阵元延时实现声束偏转和聚焦，用于钢结构裂纹、焊缝缺陷和截面损失检测；成像增强了结果可读性，但仍依赖波束路径和被检体几何。
- TWI焊接缺陷资料将制造类缺陷分为未熔合、未焊透、裂纹、气孔、夹杂等；未熔合可能出现在根部、侧壁或层间，气孔常与焊接工艺和保护条件有关。
- 近年超声焊缝综述指出，PAUT的A/B/C/S扫图像能比单独A扫更好地表达空间位置；多角度询问有助于提高平面型、裂纹型不连续的检出概率，但幅值受方向、耦合、衰减和设置影响。

## 缺陷推理线索

### 裂纹或未熔合等平面型目标

- 更依赖入射角和缺陷面方向，可能在某些发射-接收组合或某些帧中响应强，在另一些角度下变弱或消失。
- DAS图中可能呈现线状、带方向性的强反射区域；多帧中若强反射点沿某一方向连续变化，可作为长度和走向推理线索。
- 若目标靠近根部、坡口面或层间区域，同时多角度响应具有明显方向性，可提示未焊透、根部未熔合、侧壁未熔合或裂纹的候选可能，但不能只凭A扫定性。

### 气孔等体积型目标

- 单个圆形气孔更接近点状/近似球形反射体，方向性通常弱于平面型目标。
- DAS图中可能表现为局部聚焦亮点；多帧位置一致但不形成明显线状连续趋势时，可作为体积型目标候选线索。
- 群孔或密集气孔可能表现为多个离散反射点或局部散射区，仍需结合图像、真值或其他NDT方法确认。

### 夹渣、夹杂等非规则目标

- 可能表现为局部或条状散射，响应形态受尺寸、形状和取向影响较大。
- 如果多帧中呈现非规则连续区域，但缺少明确平面反射特征，Agent应输出“夹杂/夹渣候选”而非确定结论。

## Agent使用规则

1. 优先使用整批数据，不对单帧输出缺陷类型确定结论。
2. 对每个检测结果同时记录DAS峰值位置、A扫闸门响应、相关Tx-Rx通道、质量状态和仿真真值。
3. 若校验后结果缺失，应标记证据不足，不使用预校验候选代替正式结论。
4. 若多帧目标中心接近且响应沿线段分布，可推理长度/走向候选；若只出现单个局部聚焦点，只能推理点状或体积型候选。
5. 所有类型判断都必须附带“不替代人工和标准验收”的边界说明。

## 来源

- ASNT Materials Evaluation: Phased Array UT Weld Inspection: From Scan Plan to Reliable Evaluation, 2026-06-09, https://www.asnt.org/me/26/6/phased-array-ut-weld-inspection-from-scan-plan-to-reliable-evaluation
- FHWA InfoTechnology: Phased-Array Ultrasonic Testing (PAUT), https://infotechnology.fhwa.dot.gov/phased-array-ultrasonic-testing-paut-2/
- TWI Job Knowledge: Weld Defects / Imperfections - Incomplete Root Fusion or Penetration, https://www.twi-global.com/technical-knowledge/job-knowledge/weld-defects-imperfections-incomplete-root-fusion-or-penetration-040
- TWI Job Knowledge: Weld Defects - Lack of Sidewall and Inter-Run Fusion, https://www.twi-global.com/technical-knowledge/job-knowledge/weld-defects-imperfections-in-welds-lack-of-sidewall-and-inter-run-fusion-041
- Applied Sciences review: Ultrasonic Nondestructive Evaluation of Welded Steel Infrastructure: Techniques, Advances, and Applications, https://www.mdpi.com/2076-3417/16/7/3206
