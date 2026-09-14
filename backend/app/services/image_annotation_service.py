from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def draw_annotated_image(image_path: Path, detections: list[dict[str, Any]], output_path: Path) -> None:
    """Draw review annotations without loading any retired image model runtime."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path).convert("RGB") as image:
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        for item in detections:
            x_min, y_min, x_max, y_max = [float(item[key]) for key in ("x_min", "y_min", "x_max", "y_max")]
            confidence = item.get("confidence")
            label = str(item.get("class_name") or "疑似缺陷")
            if confidence is not None:
                label += f" {float(confidence):.2f}"
            draw.rectangle((x_min, y_min, x_max, y_max), outline=(220, 38, 38), width=3)
            polygon = item.get("mask_polygon") or []
            if len(polygon) >= 3:
                draw.line([tuple(point) for point in polygon] + [tuple(polygon[0])], fill=(255, 196, 0), width=3)
            text_y = max(0, y_min - 16)
            text_box = draw.textbbox((x_min, text_y), label, font=font)
            draw.rectangle(text_box, fill=(220, 38, 38))
            draw.text((x_min, text_y), label, fill=(255, 255, 255), font=font)
        image.save(output_path, quality=95)
