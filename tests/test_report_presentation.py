from backend.app.services.report_service import _fmt, _report_time, _batch_calibration_text
from backend.app.services.report_service import _user_quality_notes, _clean_report_sentence
from backend.app.services.report_service import _slag_measurement_text


def test_operator_quality_notes_hide_model_details():
    notes = _user_quality_notes('高CNN概率保留边界裂纹; 裂纹长度超出标定范围; 候选区域接近检测边界')
    assert len(notes) == 3
    assert not any('CNN' in note for note in notes)
    assert any('数值仅供参考' in note for note in notes)
    assert any('扩大检测范围' in note for note in notes)
    assert _user_quality_notes('unknown warning')


def test_report_sentence_has_no_double_punctuation():
    assert _clean_report_sentence('异常较清晰。；尺寸需确认。；') == '异常较清晰；尺寸需确认。'


def test_receipt_time_is_readable_beijing_time():
    assert _report_time("2026-09-07T10:50:01+00:00") == "2026-09-07 18:50:01"
    assert _report_time("2026-09-07T18:50:01+08:00") == "2026-09-07 18:50:01"
    assert _report_time(None) == "未记录"
    assert _report_time("bad") == "未记录"


def test_count_is_integer_and_calibration_uses_saved_values():
    assert _fmt(1) == "1"
    assert _fmt(0.25) == "0.25"
    assert _batch_calibration_text([{}]) == "本批次未记录人工图片标定。"
    assert "0.0500 mm/px" in _batch_calibration_text([
        {"calibration": {"calibration_method": "两点比例尺", "mm_per_pixel": 0.05}}
    ])


def test_slag_measurement_text_contains_major_minor_range_and_nominal_area():
    text = _slag_measurement_text({
        "corrected_length_mm": 10.8,
        "minor_axis_nominal_mm": 1.9,
        "minor_axis_lower_mm": 1.5,
        "minor_axis_upper_mm": 2.3,
        "area_mm2": 16.12,
    })
    assert "长轴 10.80 mm" in text
    assert "短轴名义值 1.90 mm（范围 1.50–2.30 mm）" in text
    assert "名义面积 16.12 mm²" in text
