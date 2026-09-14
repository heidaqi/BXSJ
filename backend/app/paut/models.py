from pydantic import BaseModel, Field


class ImagingConfig(BaseModel):
    """DAS 成像参数配置"""
    x_min_mm: float = Field(default=20.0, description="成像横向起点 (mm)")
    x_max_mm: float = Field(default=80.0, description="成像横向终点 (mm)")
    z_min_mm: float = Field(default=0.0, description="成像深度起点 (mm)")
    z_max_mm: float = Field(default=40.0, description="成像深度终点 (mm)")
    pixel_step_mm: float = Field(default=0.2, description="像素步长 (mm)")
    dynamic_range_db: float = Field(default=20.0, description="动态范围 (dB)")
    gaussian_smoothing_sigma_mm: float = Field(default=0.4, description="高斯平滑标准差 (mm)")
