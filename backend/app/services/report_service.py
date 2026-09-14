from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from html import escape as html_escape
from pathlib import Path
from typing import Any

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as PdfImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..config import settings
from .standard_service import assess_standard_applicability
from .priority_assessment_service import build_priority_assessment
from .validated_result_service import canonical_frame_candidates, validated_package


def _user_quality_notes(message: str) -> list[str]:
    rules = [
        (("标定范围", "安全边界"), "尺寸超出当前标定适用范围，数值仅供参考，需重新测量确认。"),
        (("角度", "旧模型"), "部分方向或长度测量超出适用条件，需核实测量结果。"),
        (("SNR", "信噪比"), "回波质量影响尺寸测量可靠性，建议改善检测条件后确认。"),
        (("检测边界",), "异常接近检测区域边缘，可能显示不完整，建议扩大检测范围。"),
        (("CNN", "分类阈值", "长宽比"), "缺陷类别判断存在不确定性，需结合原始波形确认。"),
    ]
    return list(dict.fromkeys(note for keys, note in rules if any(key in message for key in keys))) or ["检测结果存在质量限制，请核对原始检测记录后使用。"]


def _clean_report_sentence(value: Any) -> str:
    text = re.sub(r"[。；;]+\s*", "；", str(value)).strip(" ；。;")
    return text + "。" if text else ""


def _register_font() -> str:
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simsun.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for path in candidates:
        if path.exists():
            pdfmetrics.registerFont(TTFont("CJKFont", str(path)))
            return "CJKFont"
    return "Helvetica"


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _slag_measurement_text(defect: dict[str, Any]) -> str:
    """Format validated slag quantities without filling any missing measurement."""
    parts: list[str] = []
    major = defect.get("corrected_length_mm")
    minor = defect.get("minor_axis_nominal_mm")
    minor_lower = defect.get("minor_axis_lower_mm")
    minor_upper = defect.get("minor_axis_upper_mm")
    area = defect.get("area_mm2")
    if major is not None:
        parts.append(f"长轴 {_fmt(major)} mm")
    if minor is not None:
        minor_text = f"短轴名义值 {_fmt(minor)} mm"
        if minor_lower is not None and minor_upper is not None:
            minor_text += f"（范围 {_fmt(minor_lower)}–{_fmt(minor_upper)} mm）"
        parts.append(minor_text)
    if area is not None:
        parts.append(f"名义面积 {_fmt(area)} mm²")
    return "；".join(parts) or "定量结果不可用"


def _sample_rate_mhz(frame: dict[str, Any]) -> str:
    value = frame.get("sample_rate_hz")
    if value in {None, ""}:
        value = (frame.get("acquisition") or {}).get("sample_rate_hz")
    if value in {None, ""}:
        value = frame.get("configured_sample_rate_hz")
    if value in {None, ""}:
        value = (frame.get("acquisition") or {}).get("configured_sample_rate_hz")
    try:
        rate_hz = float(value)
    except (TypeError, ValueError):
        return "未提供"
    if rate_hz <= 0:
        return "未提供"
    return _fmt(rate_hz / 1e6, 3)


def _data_format_label(value: Any) -> str:
    return {
        "comsol_text_folder": "超声仿真数据", "legacy_tx_table": "多发射通道数据",
        "tx_rx_directory": "发射/接收通道数据", "wide_fmc": "全矩阵采集数据",
        "paut": "相控阵超声数据", "realtime": "超声采集数据",
    }.get(str(value or ""), "超声数据" if value else "未提供")


def _channel_label(shape: Any) -> str:
    if isinstance(shape, (list, tuple)) and len(shape) == 3:
        return f"{shape[1]}发{shape[2]}收"
    return "未提供"


def _report_number(identifier: str) -> str:
    return "PAUT-" + hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12].upper()


def _para(value: Any, style: ParagraphStyle) -> Paragraph:
    text = html_escape(_fmt(value))
    return Paragraph(text, style)


def _report_time(value: Any) -> str:
    if not value:
        return "未记录"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone(timedelta(hours=8)))
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "未记录"


def _batch_calibration_text(frames: list[dict[str, Any]]) -> str:
    descriptions = []
    for frame in frames:
        calibration = frame.get("calibration") or (frame.get("engineering_context") or {}).get("calibration") or {}
        scale = calibration.get("mm_per_pixel")
        if scale:
            descriptions.append(f"{calibration.get('calibration_method') or '图片比例标定'}，比例 {_fmt(scale, 4)} mm/px")
    if not descriptions:
        return "本批次未记录人工图片标定。"
    return "图片标定：" + "；".join(dict.fromkeys(descriptions)) + "。"


def _short_name(value: str, limit: int = 42) -> str:
    if len(value) <= limit:
        return value
    path = Path(value)
    suffix = path.suffix
    stem = path.stem
    keep = max(10, limit - len(suffix) - 3)
    return f"{stem[:keep]}...{suffix}"


def _calibration_summary(images: list[dict[str, Any]]) -> str:
    calibrated = sum(1 for image in images if image.get("mm_per_pixel"))
    if not images:
        return "无图片"
    if calibrated == 0:
        return "未标定"
    if calibrated == len(images):
        return "全部已标定"
    return f"部分已标定（{calibrated}/{len(images)}）"


def _representative_images(images: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any, Any]] = set()
    for image in images:
        key = (
            image.get("scan_area") or image.get("area_label") or "",
            image.get("probe_or_channel") or "",
            image.get("mm_per_pixel"),
            image.get("calibration_method") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(image)
        if len(selected) >= limit:
            break
    return selected


def _representative_label(image: dict[str, Any]) -> str:
    area = image.get("scan_area") or image.get("area_label") or ""
    if area:
        return area
    return _short_name(image.get("original_name", "-"), 28)


def html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _source_label(value: str | None) -> str:
    return {
        "manual": "人工补录",
        "manual_review": "人工复核",
        "validated_team_result": "校验后检测结果",
    }.get(str(value or ""), "历史检测结果")


def _choose_report_path(default_dir: Path, default_name: str, destination_path: Path | None) -> Path:
    report_path = destination_path.expanduser() if destination_path else default_dir / default_name
    if report_path.suffix.lower() != ".pdf":
        raise ValueError("报告文件必须使用 .pdf 扩展名")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    return report_path.resolve()


def create_job_report(
    job: dict[str, Any],
    images: list[dict[str, Any]],
    defects: list[dict[str, Any]],
    destination_path: Path | None = None,
) -> Path:
    font = _register_font()
    report_dir = settings.output_dir / "reports"
    report_path = _choose_report_path(report_dir, f"{job['id']}_report.pdf", destination_path)
    temporary_path = report_path.with_suffix(".pdf.tmp")

    doc = SimpleDocTemplate(
        str(temporary_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=f"{job['batch_name']}检测报告",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="CnTitle", fontName=font, fontSize=20, leading=28, alignment=1, spaceAfter=12))
    styles.add(ParagraphStyle(name="CnH1", fontName=font, fontSize=13, leading=18, spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="CnBody", fontName=font, fontSize=9.5, leading=15))
    styles.add(ParagraphStyle(name="CnSmall", fontName=font, fontSize=8.5, leading=12))

    story: list[Any] = []
    story.append(Paragraph("工业图像缺陷检测报告", styles["CnTitle"]))
    summary_rows = [
        ["报告编号", job["id"], "报告日期", datetime.now().strftime("%Y-%m-%d")],
        ["项目名称", job.get("project_name") or "-", "委托单位", job.get("client_name") or "-"],
        ["检测对象", job.get("inspected_object") or "-", "检测批次", job.get("batch_name") or "-"],
        ["检测人员", job.get("inspector") or "-", "复核人员", job.get("reviewer") or "-"],
        ["检测图片数", str(len(images)), "缺陷数量", str(len(defects))],
    ]
    story.append(_table(summary_rows, font, [26 * mm, 62 * mm, 26 * mm, 62 * mm], styles["CnSmall"]))

    story.append(Paragraph("1 范围", styles["CnH1"]))
    story.append(
        Paragraph(
            "本报告适用于本批次工业检测图片中可见缺陷区域的识别、复核、定位和记录。报告结果用于辅助检测人员进行缺陷筛查、位置追溯和资料归档，不替代产品验收标准、工艺规范或持证检测人员的最终判定。",
            styles["CnBody"],
        )
    )

    story.append(Paragraph("2 规范性引用文件", styles["CnH1"]))
    standard_assessment = assess_standard_applicability({
        "object_name": job.get("inspected_object"), "project_name": job.get("project_name"),
        "client_name": job.get("client_name"), "technique": "工业检测图片",
    })
    story.append(
        Paragraph(
            "本报告格式参考 GB/T 25757-2010 等无损检测相关文件的章节组织方式，并结合图像检测项目进行适配。实际合格性评定应以委托方指定的产品标准、检测工艺规程、验收准则和合同技术条件为准。",
            styles["CnBody"],
        )
    )
    if standard_assessment["candidates"]:
        standard_rows = [["标准", "作用", "适用状态"]]
        standard_rows.extend([[item["code"], item["role"], item["status"]] for item in standard_assessment["candidates"][:6]])
        story.append(Spacer(1, 2 * mm))
        story.append(_table(standard_rows, font, [44 * mm, 68 * mm, 64 * mm], styles["CnSmall"]))

    story.append(Paragraph("3 术语和定义", styles["CnH1"]))
    term_rows = [
        ["缺陷区域", "检测结果或人工补录形成的疑似缺陷区域。"],
        ["像素坐标", "以图片左上角为原点的图像坐标，用于图像内追溯。"],
        ["图片内物理坐标", "根据图片比例尺换算得到的当前图片内位置，不代表小车位置或工件全局坐标。"],
        ["复核状态", "检测人员对识别结果进行确认、误检标记或人工修正后的状态。"],
    ]
    story.append(_table(term_rows, font, [30 * mm, 145 * mm], styles["CnSmall"]))

    story.append(Paragraph("4 测试条件", styles["CnH1"]))
    condition_rows = [
        ["图片数量", str(len(images)), "缺陷数量", str(len(defects))],
        ["检测对象", job.get("inspected_object") or "-", "检测批次", job.get("batch_name") or "-"],
        ["检测人员", job.get("inspector") or "-", "复核人员", job.get("reviewer") or "-"],
        ["置信度阈值", _fmt(job.get("confidence")), "坐标标定", _calibration_summary(images)],
    ]
    story.append(_table(condition_rows, font, [26 * mm, 62 * mm, 26 * mm, 62 * mm], styles["CnSmall"]))

    story.append(Paragraph("5 对比试样和标定信息", styles["CnH1"]))
    story.append(
        Paragraph(
            "若检测图片包含比例尺或对比试样，应在上传元数据或坐标标定中记录其来源。未提供标定信息的图片仅输出像素坐标，不进行物理尺寸推断；本报告不推断小车扫查位置或工件全局坐标。",
            styles["CnBody"],
        )
    )
    calibration_rows = [["序号", "扫查区域", "探头/通道", "mm/px", "标定方式", "图片备注/标定说明"]]
    for index, image in enumerate(_representative_images(images), start=1):
        calibration_rows.append(
            [
                str(index),
                _representative_label(image),
                image.get("probe_or_channel") or "-",
                _fmt(image.get("mm_per_pixel")),
                image.get("calibration_method") or "未标定",
                "；".join(part for part in [image.get("image_note") or "", image.get("calibration_note") or ""] if part) or "-",
            ]
        )
    story.append(_table(calibration_rows, font, [10 * mm, 28 * mm, 26 * mm, 20 * mm, 28 * mm, 60 * mm], styles["CnSmall"]))

    story.append(Paragraph("6 测试内容与方法", styles["CnH1"]))
    story.append(
        Paragraph(
            "测试内容包括图片导入、缺陷区域识别、人工复核、图片内坐标换算和报告记录。系统先对图片进行缺陷区域检测，随后由检测人员结合原图或标注图进行确认、误检标记或人工补录；存在有效比例尺时，按像素框中心和尺寸换算当前图片内位置与尺寸。",
            styles["CnBody"],
        )
    )

    story.append(Paragraph("7 测试结果", styles["CnH1"]))
    by_class: dict[str, int] = {}
    manual_count = 0
    for defect in defects:
        by_class[defect["class_name"]] = by_class.get(defect["class_name"], 0) + 1
        if defect.get("source") == "manual":
            manual_count += 1
    class_text = "；".join(f"{k}: {v}" for k, v in by_class.items()) or "未检出缺陷"
    story.append(
        Paragraph(
            f"本批次共检测 {len(images)} 张图片，记录缺陷 {len(defects)} 处，其中人工补录/修正 {manual_count} 处。分类统计：{class_text}。",
            styles["CnBody"],
        )
    )

    story.append(Paragraph("7.1 缺陷明细", styles["CnH1"]))
    defect_rows = [["序号", "区域", "缺陷类型", "置信度", "物理中心/mm", "物理尺寸/mm", "复核", "建议/备注"]]
    image_by_id = {image["id"]: image for image in images}
    for index, defect in enumerate(defects, start=1):
        image = image_by_id.get(defect["image_id"], {})
        note = "；".join(part for part in [defect.get("suggestion") or "", defect.get("remark") or ""] if part) or "-"
        defect_rows.append(
            [
                str(index),
                image.get("scan_area") or image.get("area_label") or "-",
                defect["class_name"],
                _fmt(defect["confidence"], 3) if defect.get("confidence_available", 1) else "-",
                f"({_fmt(defect.get('center_x_mm'))}, {_fmt(defect.get('center_y_mm'))})",
                (_slag_measurement_text(defect) if defect.get("class_name") == "夹渣"
                 else f"{_fmt(defect.get('width_mm'))} x {_fmt(defect.get('height_mm'))}"),
                defect.get("review_status") or "-",
                note,
            ]
        )
    story.append(_table(defect_rows, font, [10 * mm, 22 * mm, 24 * mm, 16 * mm, 30 * mm, 28 * mm, 18 * mm, 40 * mm], styles["CnSmall"]))

    annotated = [image for image in images if image.get("annotated_path")]
    if annotated:
        story.append(PageBreak())
        story.append(Paragraph("7.2 典型缺陷图", styles["CnH1"]))
        for index, image in enumerate(annotated[:6], start=1):
            path = Path(image["annotated_path"])
            if path.exists():
                story.append(KeepTogether([
                    Paragraph(f"图 {index}  {html_escape(_representative_label(image))}", styles["CnSmall"]),
                    _fit_image(path, max_width=160 * mm, max_height=90 * mm),
                    Spacer(1, 4 * mm),
                ]))

    story.append(KeepTogether([
        Paragraph("8 结果评定与说明", styles["CnH1"]),
        Paragraph(
            "本报告记录的缺陷区域、位置和尺寸为当前图片内的图像检测及标定换算结果。"
            + standard_assessment["conclusion_boundary"]
            + (" 当前缺失条件包括：" + "、".join(standard_assessment["missing_acceptance_conditions"]) + "。" if standard_assessment["missing_acceptance_conditions"] else "")
            + "缺陷是否满足验收要求，应依据委托方指定的产品标准、检测工艺规程和人工复核结论确定。报告生成后如继续修改复核结果，应重新生成报告。",
            styles["CnBody"],
        ),
    ]))
    sign_rows = [
        ["编制", job.get("inspector") or settings.report_prepared_by or "", "复核", job.get("reviewer") or settings.report_reviewed_by or ""],
        ["批准/授权签字", job.get("approver") or settings.report_approved_by or "", "日期", datetime.now().strftime("%Y-%m-%d")],
    ]
    story.append(Spacer(1, 8 * mm))
    story.append(_table(sign_rows, font, [30 * mm, 58 * mm, 30 * mm, 58 * mm], styles["CnSmall"]))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    temporary_path.replace(report_path)
    return report_path


def create_realtime_batch_report(
    batch: dict[str, Any],
    frames: list[dict[str, Any]],
    destination_path: Path | None = None,
) -> Path:
    font = _register_font()
    report_dir = settings.output_dir / "realtime_reports"
    report_path = _choose_report_path(report_dir, f"{batch['batch_id']}_report.pdf", destination_path)
    temporary_path = report_path.with_suffix(".pdf.tmp")
    doc = SimpleDocTemplate(
        str(temporary_path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=16 * mm, title=f"{batch['batch_id']} PAUT实时检测报告",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="RtTitle", fontName=font, fontSize=19, leading=26, alignment=1, spaceAfter=12))
    styles.add(ParagraphStyle(name="RtH1", fontName=font, fontSize=13, leading=18, spaceBefore=10, spaceAfter=6, keepWithNext=True))
    styles.add(ParagraphStyle(name="RtBody", fontName=font, fontSize=9.5, leading=15))
    styles.add(ParagraphStyle(name="RtSmall", fontName=font, fontSize=8.2, leading=11))
    review_statuses = []
    for frame in frames:
        defects = canonical_frame_candidates(frame)
        review_statuses.append(str((frame.get("review") or {}).get("status") or ("待复核" if defects else "无需复核")))
    confirmed = sum(status == "已确认" for status in review_statuses)
    false_positive = sum(status == "误检" for status in review_statuses)
    not_required = sum(status == "无需复核" for status in review_statuses)
    pending = sum(status == "待复核" for status in review_statuses)
    calibrated = sum(
        1 for frame in frames
        if (frame.get("calibration") or (frame.get("engineering_context") or {}).get("calibration") or {}).get("mm_per_pixel")
    )
    first = frames[0] if frames else {}
    probe_positions = first.get("probe_positions_mm") or []
    probe_text = (
        f"16阵元（{_fmt(probe_positions[0])}–{_fmt(probe_positions[-1])} mm配置）"
        if len(probe_positions) == 16 else "-"
    )
    team_packages = [validated_package(frame) for frame in frames]
    team_packages = [item for item in team_packages if item]
    quality_counts: dict[str, int] = {}
    for package in team_packages:
        quality_status = str((package.get("quality") or {}).get("status") or "未评价")
        quality_counts[quality_status] = quality_counts.get(quality_status, 0) + 1
    quality_text = "、".join(f"{name}{count}帧" for name, count in quality_counts.items()) or "未评价"
    stored_agent = (batch.get("result") or {}).get("agent") or {}
    evidence_revision = int(batch.get("evidence_revision") or 0)
    agent_revision_value = batch.get("agent_revision")
    agent_revision = int(agent_revision_value if agent_revision_value is not None else (0 if stored_agent else -1))
    agent_result = stored_agent if agent_revision == evidence_revision else {}
    report_context = batch.get("report_context") or {}
    disposition = report_context.get("disposition") or agent_result.get("risk_assessment") or {}
    candidates = [candidate for frame in frames for candidate in canonical_frame_candidates(frame)]
    standard_assessment = assess_standard_applicability({
        "object_name": "PAUT实时检测焊缝", "technique": "自动PAUT FMC DAS",
        "plate_thickness_mm": first.get("plate_thickness_mm"),
    })
    story: list[Any] = [Paragraph("PAUT实时成像与辅助分析报告", styles["RtTitle"])]
    story.append(_table([
        ["报告编号", _report_number(batch["batch_id"]), "报告日期", datetime.now().strftime("%Y-%m-%d")],
        ["数据帧数", len(frames), "分析状态", batch.get("status") or "-"],
        ["已确认/无需复核", f"{confirmed}/{not_required}", "误检/待复核", f"{false_positive}/{pending}"],
        ["结果质量", quality_text, "坐标性质", "检测结果x-z物理坐标" if team_packages else "当前图片内坐标"],
        ["起始扫查时间", _report_time(frames[0].get("received_at") if frames else None), "结束扫查时间", _report_time(frames[-1].get("received_at") if frames else None)],
    ], font, [26 * mm, 62 * mm, 28 * mm, 60 * mm], styles["RtSmall"]))
    story.append(Paragraph("1 范围", styles["RtH1"]))
    story.append(Paragraph("本报告记录超声检测发现的疑似缺陷、位置、尺寸和人工复核结果。尺寸与朝向为当前二维截面估计，不表示三维缺陷形态。", styles["RtBody"]))
    story.append(Paragraph("2 规范性引用文件", styles["RtH1"]))
    standard_codes = "、".join(str(item.get("code")) for item in standard_assessment["candidates"][:5] if item.get("code"))
    story.append(Paragraph(
        html_escape(standard_codes or "按项目指定的超声检测标准和检测工艺执行")
        + "。具体适用版本及验收等级以委托文件和检测工艺为准。",
        styles["RtBody"],
    ))
    story.append(Paragraph("3 术语和定义", styles["RtH1"]))
    story.append(_table([
        ["实时帧", "与原始数据、检测结果、智能分析和人工复核对应的数据单元。"],
        ["缺陷记录", "记录类别、位置和二维定量结果的疑似缺陷条目。"],
        ["复核状态", "检测人员对当前帧记录的待复核、已确认或误检状态。"],
    ], font, [34 * mm, 142 * mm], styles["RtSmall"]))
    story.append(Paragraph("4 测试条件", styles["RtH1"]))
    story.append(_table([
        ["数据格式", _data_format_label(first.get("source_type")), "通道配置", _channel_label(first.get("shape"))],
        ["采样率/MHz", _sample_rate_mhz(first), "材料声速/m/s", _fmt(first.get("velocity_mps"))],
        ["板厚/mm", _fmt(first.get("plate_thickness_mm")), "探头配置", probe_text],
    ], font, [28 * mm, 60 * mm, 30 * mm, 58 * mm], styles["RtSmall"]))
    story.append(Paragraph("5 对比试样和标定信息", styles["RtH1"]))
    story.append(Paragraph(html_escape(_batch_calibration_text(frames)), styles["RtBody"]))
    story.append(Paragraph("6 测试内容与方法", styles["RtH1"]))
    story.append(Paragraph("对原始超声数据进行成像与缺陷检测，记录位置和尺寸，结合人工复核结果给出处置建议。", styles["RtBody"]))
    scan_frames = [frame for frame in frames if frame.get("scan_receipt")]
    if scan_frames:
        story.append(Paragraph("6.1 采集记录（北京时间）", styles["RtH1"]))
        story.append(Paragraph("模拟采集：扫查距离由车速和时间估算，不作为缺陷定位依据。", styles["RtBody"]))
        scan_rows = [["帧序号", "扫查时间", "车速（mm/s）", "估算距离（mm）"]]
        for frame in scan_frames:
            receipt = frame["scan_receipt"]
            received = _report_time(receipt.get("received_at"))
            scan_rows.append([receipt["sequence"], received, _fmt(receipt.get("vehicle_speed_mm_s")), _fmt(receipt.get("estimated_scan_distance_mm"))])
        story.append(_table(scan_rows, font, [18 * mm, 78 * mm, 38 * mm, 42 * mm], styles["RtSmall"]))
    story.append(Paragraph("7 测试结果", styles["RtH1"]))
    story.append(Paragraph(
        f"本次检测 {len(frames)} 帧，已确认 {confirmed} 帧、无需复核 {not_required} 帧、"
        f"误检 {false_positive} 帧、待复核 {pending} 帧。",
        styles["RtBody"],
    ))
    quality_issues = [
        issue
        for package in team_packages
        for issue in ((package.get("quality") or {}).get("issues") or [])
    ]
    if quality_issues:
        story.append(Paragraph("测量限制与处理提示", styles["RtH1"]))
        note_frames: dict[str, list[int]] = {}
        for index, frame in enumerate(frames, 1):
            package = validated_package(frame) or {}
            for issue in (package.get("quality") or {}).get("issues") or []:
                for note in _user_quality_notes(str(issue.get("message") or "")):
                    indices = note_frames.setdefault(note, [])
                    if index not in indices:
                        indices.append(index)
        for note, indices in note_frames.items():
            story.append(Paragraph(html_escape(f"第{'、'.join(map(str, indices))}帧：{note}"), styles["RtBody"]))
    slag_candidates = [item for item in candidates if item.get("class_name") == "夹渣"]
    if slag_candidates:
        story.append(Paragraph("夹渣定量结果", styles["RtH1"]))
        slag_rows = [["序号", "中心x/z(mm)", "夹渣概率", "长轴/mm", "短轴名义值及范围/mm", "名义面积/mm²"]]
        for slag_index, candidate in enumerate(slag_candidates, start=1):
            slag_rows.append([
                slag_index,
                f"{_fmt(candidate.get('x_mm'))}/{_fmt(candidate.get('z_mm'))}",
                _fmt(candidate.get("slag_probability"), 3),
                _fmt(candidate.get("corrected_length_mm")),
                (f"{_fmt(candidate.get('minor_axis_nominal_mm'))} "
                 f"[{_fmt(candidate.get('minor_axis_lower_mm'))}–{_fmt(candidate.get('minor_axis_upper_mm'))}]"),
                _fmt(candidate.get("area_mm2")),
            ])
        story.append(_table(slag_rows, font, [10 * mm, 30 * mm, 22 * mm, 22 * mm, 54 * mm, 38 * mm], styles["RtSmall"]))
    if agent_result or disposition:
        story.append(Paragraph("7.1 综合结论", styles["RtH1"]))
        story.append(Paragraph(html_escape(str(disposition.get("conclusion") or agent_result.get("summary") or "未生成批次摘要")), styles["RtBody"]))
        risk_assessment = disposition
        if risk_assessment:
            story.append(_table([
                ["辅助风险等级", risk_assessment.get("risk_level") or "-", "处置建议", risk_assessment.get("recommendation") or "-"],
                ["可信程度", risk_assessment.get("confidence_level") or risk_assessment.get("confidence") or "-", "复核状态", {"not_required": "无需复核", "required": "待复核", "completed": "已完成"}.get(risk_assessment.get("review_requirement"), "-")],
            ], font, [30 * mm, 38 * mm, 28 * mm, 80 * mm], styles["RtSmall"]))
            if (risk_assessment.get("confidence_level") or risk_assessment.get("confidence")) == "低":
                story.append(Paragraph("结果使用说明：当前类型或尺寸的确定性有限，不能仅凭本报告决定维修或判废。风险等级表示异常的处置优先级；风险较高时应先核实关键测量，再进行工程评估。人工复核完成表示检测记录已审核，不代表测量误差已消除。", styles["RtBody"]))
            reasons = risk_assessment.get("reasons") or []
            if reasons:
                story.append(Paragraph("主要风险依据", styles["RtH1"]))
                for reason in reasons[:8]:
                    story.append(Paragraph(html_escape(_clean_report_sentence(reason)), styles["RtBody"]))
            potential = risk_assessment.get("potential_risks") or []
            if potential:
                for risk in potential[:8]:
                    story.append(Paragraph("潜在影响：" + html_escape(_clean_report_sentence(risk)), styles["RtBody"]))
        assessments = {
            item.get("candidate_id"): item
            for item in (agent_result.get("defect_assessments") or [])
            if isinstance(item, dict)
        }
        candidate_risks = {
            item.get("candidate_id"): item
            for item in (risk_assessment.get("candidate_risks") or [])
            if isinstance(item, dict)
        }
        candidate_rows = [["序号", "缺陷类型", "风险", "中心x/z(mm)", "二维尺寸", "复核状态"]]
        for candidate_index, candidate in enumerate(candidates, start=1):
            candidate_id = candidate.get("candidate_id") or candidate.get("defect_id") or candidate.get("id")
            centroid = candidate.get("centroid_mm") or {}
            assessment = assessments.get(candidate_id) or {}
            candidate_risk = candidate_risks.get(candidate_id) or {}
            geometry = candidate.get("geometry") or {}
            diameter = candidate.get("diameter_mm", geometry.get("diameter_mm"))
            length = candidate.get("corrected_length_mm")
            width = candidate.get("width_mm")
            orientation = candidate.get("orientation_deg", candidate.get("section_orientation_deg"))
            if candidate.get("class_name") == "夹渣":
                size_text = _slag_measurement_text(candidate)
            elif diameter is not None:
                size_text = f"等效直径 {_fmt(diameter)} mm"
            else:
                size_parts = []
                if length is not None:
                    size_parts.append(f"长 {_fmt(length)} mm")
                if width is not None:
                    size_parts.append(f"宽 {_fmt(width)} mm")
                size_text = "；".join(size_parts) or "未提供"
                if orientation is not None:
                    size_text += f"；{_fmt(orientation)}°"
            candidate_rows.append([
                candidate_index,
                candidate.get("class_name") or candidate.get("type") or assessment.get("probable_type") or "未定型异常",
                candidate_risk.get("risk_level") or "-",
                f"{_fmt(candidate.get('center_x_mm', candidate.get('x_mm', centroid.get('x'))))}/{_fmt(candidate.get('center_y_mm', candidate.get('z_mm', centroid.get('z'))))}",
                size_text,
                candidate.get("review_status") or (candidate.get("review") or {}).get("status") or "待复核",
            ])
        if len(candidate_rows) > 1:
            story.append(_table(candidate_rows, font, [31 * mm, 29 * mm, 15 * mm, 27 * mm, 38 * mm, 36 * mm], styles["RtSmall"]))
        frame_section = "7.2 逐帧结果"
        image_section = "7.3 典型DAS成像"
    else:
        frame_section = "7.1 逐帧结果"
        image_section = "7.2 典型DAS成像"
    story.append(Paragraph(frame_section, styles["RtH1"]))
    rows = [["序号", "数据组", "扫查时间", "峰值x/z(mm)", "缺陷数", "结果质量", "复核", "复核意见"]]
    for index, frame in enumerate(frames, start=1):
        review = frame.get("review", {})
        frame_candidates = canonical_frame_candidates(frame)
        peak_pairs = [
            f"{_fmt(item.get('peak_x_mm'))}/{_fmt(item.get('peak_z_mm'))}"
            for item in frame_candidates
            if item.get("peak_x_mm") is not None and item.get("peak_z_mm") is not None
        ]
        calibration = frame.get("calibration") or (frame.get("engineering_context") or {}).get("calibration") or {}
        calibration_note = (
            f"{calibration.get('calibration_method') or '未标定'}"
            + (f" {_fmt(calibration.get('mm_per_pixel'))}mm/px" if calibration.get("mm_per_pixel") else "")
        )
        rows.append([
            index, _short_name(frame.get("source_group") or f"第{index}组", 18), _report_time(frame.get("received_at")),
            "；".join(peak_pairs[:2]) or "-",
            len(frame_candidates),
            ((validated_package(frame) or {}).get("quality") or {}).get("status") or calibration_note,
            review.get("status") or ("无需复核" if not frame_candidates else "待复核"), review.get("note") or "-",
        ])
    story.append(_table(rows, font, [8 * mm, 24 * mm, 32 * mm, 23 * mm, 12 * mm, 23 * mm, 18 * mm, 30 * mm], styles["RtSmall"]))
    images = []
    for frame in frames:
        url = (frame.get("image") or {}).get("annotated_url") or (frame.get("image") or {}).get("url") or ""
        if url.startswith("/outputs/"):
            images.append(settings.output_dir / url.removeprefix("/outputs/"))
    existing = [path for path in images if path.exists()]
    if existing:
        story.append(PageBreak())
        story.append(Paragraph(image_section, styles["RtH1"]))
        for index, path in enumerate(existing[:6], start=1):
            story.append(KeepTogether([
                Paragraph(f"图 {index}  第{index}帧典型截面", styles["RtSmall"]),
                _fit_image(path, max_width=160 * mm, max_height=75 * mm),
                Spacer(1, 3 * mm),
            ]))
    priority = build_priority_assessment(
        candidates,
        frames,
        risk_assessment if (agent_result or disposition) else {},
        agent_result.get("priority_assessment") if isinstance(agent_result.get("priority_assessment"), dict) else None,
    )
    story.append(Paragraph("8 处置优先级判断", styles["RtH1"]))
    story.append(Paragraph(html_escape(priority["overall_judgment"]), styles["RtBody"]))
    priority_groups = (
        ("需要优先处理", priority["immediate_attention"]),
        ("需要重点关注", priority["follow_up_attention"]),
        ("建议常规跟踪", priority["routine_observation"]),
    )
    for heading, items in priority_groups:
        if not items:
            continue
        story.append(Paragraph(heading, styles["RtH1"]))
        priority_rows = [["扫查位置", "异常类型", "风险", "建议"]]
        for item in items[:6]:
            action = item["recommended_action"]
            if item.get("operator_note"):
                action = f"{action}；{item['operator_note']}"
            priority_rows.append([item["location"], item["defect_type"], item["risk_level"], action])
        story.append(_table(priority_rows, font, [72 * mm, 28 * mm, 16 * mm, 60 * mm], styles["RtSmall"]))
    story.append(Paragraph(priority["boundary"], styles["RtSmall"]))
    story.append(KeepTogether([
        Paragraph("9 结果评定与说明", styles["RtH1"]),
        Paragraph(
            ("待复核结果应完成确认后再形成最终检测结论。" if pending else "")
            + "辅助风险等级用于安排处置，不等同于标准验收等级。最终验收结论依据项目适用标准和检测工艺作出。",
            styles["RtBody"],
        ),
    ]))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    temporary_path.replace(report_path)
    return report_path


def create_inspection_report(result: dict[str, Any]) -> Path:
    """桌面端统一检测任务的 PDF 报告。

    参数 `result` 为 `inspection_service.InspectionResult.to_dict()` 的产物，
    同时兼容数据组分析（input_type="data"）与普通图片检测（input_type="images"）。
    """
    font = _register_font()
    report_dir = settings.output_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    job_id = result.get("job_id", "job")
    report_path = report_dir / f"{job_id}_inspection_report.pdf"

    doc = SimpleDocTemplate(
        str(report_path), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=16 * mm, title=f"{job_id} 检测报告",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="InsTitle", fontName=font, fontSize=19, leading=26, alignment=1, spaceAfter=12))
    styles.add(ParagraphStyle(name="InsH1", fontName=font, fontSize=13, leading=18, spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(name="InsBody", fontName=font, fontSize=9.5, leading=15))
    styles.add(ParagraphStyle(name="InsSmall", fontName=font, fontSize=8.2, leading=11))

    groups = result.get("groups") or []
    input_type = result.get("input_type") or "unknown"
    is_images = input_type == "images"
    started = result.get("started_at")
    finished = result.get("finished_at")
    status = result.get("status") or "-"

    def ts(value: Any) -> str:
        if not value:
            return "-"
        try:
            return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return str(value)

    story: list[Any] = [Paragraph("缺陷检测分析报告", styles["InsTitle"])]
    story.append(_table([
        ["任务编号", job_id, "报告日期", datetime.now().strftime("%Y-%m-%d")],
        ["输入类型", "数据组分析" if not is_images else "图片检测", "分析状态", status],
        ["输入目录", _short_name(result.get("source_dir", "-"), 60), "", ""],
        ["数据组/图片数", result.get("total_groups", 0), "完成", result.get("completed_groups", 0)],
        ["开始时间", ts(started), "结束时间", ts(finished)],
    ], font, [26 * mm, 62 * mm, 28 * mm, 60 * mm], styles["InsSmall"]))

    story.append(Paragraph("1 范围", styles["InsH1"]))
    if is_images:
        story.append(Paragraph("本报告记录历史图片批次中的人工复核结果。该兼容页面不形成新的自动检测结论；未标定图片仅保留像素位置。", styles["InsBody"]))
    else:
        story.append(Paragraph("本报告记录数据集文件夹的 DAS 成像、A 扫特征分析与 AI 图像识别综合结果，对缺陷位置与尺寸进行标注。结果用于检测过程追溯与辅助判断，不替代适用产品标准与持证人员结论。", styles["InsBody"]))

    story.append(Paragraph("2 结果摘要", styles["InsH1"]))
    story.append(Paragraph(result.get("summary") or "-", styles["InsBody"]))

    if not is_images:
        standard_assessment = assess_standard_applicability({
            "object_name": "PAUT缺陷检测", "technique": "自动PAUT FMC DAS",
        })
        story.append(Paragraph("3 规范性引用与适用条件", styles["InsH1"]))
        story.append(Paragraph("以下标准由系统按自动 PAUT 与焊缝上下文筛选为候选或支撑依据；是否正式适用仍应由项目技术文件确认。", styles["InsBody"]))
        standard_rows = [["标准", "作用", "适用状态"]]
        standard_rows.extend([[item["code"], item["role"], item["status"]] for item in standard_assessment["candidates"][:7]])
        story.append(_table(standard_rows, font, [50 * mm, 68 * mm, 58 * mm], styles["InsSmall"]))
        missing = standard_assessment["missing_acceptance_conditions"] or ["仍需按项目技术文件确认正式适用标准"]
        story.append(_table([["当前缺失或待确认条件", "说明"]] + [[item, "未由当前数据证明"] for item in missing], font, [70 * mm, 106 * mm], styles["InsSmall"]))

    story.append(Paragraph("4 检测结果明细", styles["InsH1"]))
    if is_images:
        rows = [["序号", "图片", "检出数", "状态"]]
        for index, group in enumerate(groups, start=1):
            rows.append([
                index, group.get("group_name", "-"), len(group.get("defects") or []),
                group.get("status", "-"),
            ])
        story.append(_table(rows, font, [12 * mm, 100 * mm, 22 * mm, 42 * mm], styles["InsSmall"]))
        all_defects = [d for g in groups for d in (g.get("defects") or [])]
        if all_defects:
            d_rows = [["图片", "类别", "置信度", "中心x(px)", "中心y(px)", "宽(px)", "高(px)"]]
            for group in groups:
                for defect in group.get("defects") or []:
                    d_rows.append([
                        group.get("group_name", "-"), defect.get("class_name", "-"),
                        _fmt(defect.get("confidence"), 3), defect.get("x_px", "-"),
                        defect.get("y_px", "-"), defect.get("width_px", "-"), defect.get("height_px", "-"),
                    ])
            story.append(_table(d_rows, font, [38 * mm, 22 * mm, 20 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm], styles["InsSmall"]))
    else:
        rows = [["序号", "数据组", "A扫候选", "AI候选", "联合缺陷", "峰值x/z(mm)", "状态"]]
        for index, group in enumerate(groups, start=1):
            joint = sum(1 for d in (group.get("defects") or []) if d.get("source") == "joint")
            rows.append([
                index, group.get("group_name", "-"), len(group.get("ascan_candidates") or []),
                len(group.get("yolo_detections") or []), joint,
                f"{_fmt(group.get('peak', {}).get('peak_x_mm'))}/{_fmt(group.get('peak', {}).get('peak_z_mm'))}",
                group.get("status", "-"),
            ])
        story.append(_table(rows, font, [12 * mm, 44 * mm, 20 * mm, 18 * mm, 18 * mm, 28 * mm, 24 * mm], styles["InsSmall"]))
        d_rows = [["数据组", "来源", "类别", "x(mm)", "z(mm)", "宽(mm)", "高(mm)", "置信度"]]
        for group in groups:
            for defect in group.get("defects") or []:
                d_rows.append([
                    group.get("group_name", "-"),
                    {"joint": "联合检测", "yolo": "历史检测", "ascan": "A扫"}.get(defect.get("source"), "历史检测结果"),
                    defect.get("class_name", "-"), defect.get("x_mm", "-"), defect.get("z_mm", "-"),
                    defect.get("width_mm", "-"), defect.get("height_mm", "-"),
                    _fmt(defect.get("confidence"), 3),
                ])
        story.append(_table(d_rows, font, [36 * mm, 16 * mm, 20 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 20 * mm], styles["InsSmall"]))

    annotated_images = [Path(p) for p in (result.get("annotated_images") or []) if p and Path(p).exists()]
    if annotated_images:
        story.append(PageBreak())
        story.append(Paragraph("5 标注图像", styles["InsH1"]))
        for index, path in enumerate(annotated_images[:12], start=1):
            story.append(KeepTogether([
                Paragraph(f"图 {index}  {_short_name(str(path), 52)}", styles["InsSmall"]),
                _fit_image(path, max_width=160 * mm, max_height=90 * mm),
                Spacer(1, 3 * mm),
            ]))

    errors = result.get("errors") or []
    if errors:
        story.append(Paragraph("6 异常与说明", styles["InsH1"]))
        err_rows = [["数据组", "阶段", "说明"]]
        err_rows.extend([
            [g.get("group_id", "-"), g.get("stage", "-"), _short_name(g.get("error", "-"), 80)]
            for g in errors
        ])
        story.append(_table(err_rows, font, [24 * mm, 22 * mm, 130 * mm], styles["InsSmall"]))

    story.append(KeepTogether([
        Paragraph("7 结果评定与说明", styles["InsH1"]),
        Paragraph(
            "本报告的缺陷区域、位置和尺寸为当前配置下的分析结果。"
            "AI 未检出不代表无缺陷，物理定位依赖声速、板厚与通道几何等输入；"
            "是否满足验收要求应依据适用产品标准、检测工艺规程和人工复核结论确定。",
            styles["InsBody"],
        ),
    ]))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return report_path


def _table(rows: list[list[Any]], font: str, widths: list[float], cell_style: ParagraphStyle | None = None) -> Table:
    processed_rows = rows
    if cell_style is not None:
        processed_rows = [[_para(cell, cell_style) for cell in row] for row in rows]
    header_labels = {"序号", "标准", "当前缺失或待确认条件"}
    repeat_rows = 1 if rows and str(rows[0][0]) in header_labels else 0
    table = Table(processed_rows, colWidths=widths, repeatRows=repeat_rows)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 8.2),
                ("LEADING", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9aa4b2")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _fit_image(path: Path, max_width: float, max_height: float) -> PdfImage:
    with Image.open(path) as image:
        width, height = image.size
    scale = min(max_width / width, max_height / height)
    return PdfImage(str(path), width=width * scale, height=height * scale)


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(200 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()
