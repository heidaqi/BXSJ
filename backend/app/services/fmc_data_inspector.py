from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np


DEFAULT_16X16_POSITIONS_MM = [
    30.0, 32.0, 34.0, 36.0, 38.0, 40.0, 42.0, 44.0,
    49.0, 51.0, 53.0, 55.0, 57.0, 59.0, 61.0, 63.0,
]
LEGACY_16X16_POSITIONS_MM = [
    22.0, 24.0, 26.0, 28.0, 30.0, 32.0, 34.0, 36.0,
    56.0, 58.0, 60.0, 62.0, 64.0, 66.0, 68.0, 70.0,
]
SUPPORTED_16X16_PROFILES = {
    "30-63": DEFAULT_16X16_POSITIONS_MM,
    "22-70": LEGACY_16X16_POSITIONS_MM,
}


@dataclass(frozen=True)
class ValidatedDataGroup:
    path: str
    format: str
    probe_geometry_source: str
    status: str
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    sample_count: int | None = None
    sample_rate_hz: float | None = None
    tx_count: int | None = None
    rx_count: int | None = None
    tx_column_unit: str | None = None
    probe_profile: str | None = None
    tx_positions_mm: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InspectionResult:
    groups: list[ValidatedDataGroup]

    @property
    def valid_groups(self) -> list[ValidatedDataGroup]:
        return [item for item in self.groups if item.status == "valid"]

    @property
    def skipped_groups(self) -> list[ValidatedDataGroup]:
        return [item for item in self.groups if item.status == "skipped"]

    @property
    def valid_paths(self) -> list[Path]:
        return [Path(item.path) for item in self.valid_groups]

    def to_dict(self) -> dict[str, Any]:
        valid = self.valid_groups
        skipped = self.skipped_groups
        if valid and skipped:
            status = "部分通过"
        elif valid:
            status = "通过"
        else:
            status = "未通过"
        formats = {item.format for item in valid}
        contract = "comsol-flat-16x16/v1" if formats == {"legacy_tx_table"} else "fmc-16x16-compatible/v2"
        return {
            "status": status,
            "contract": contract,
            "inspector_contract": "fmc-16x16-compatible/v2",
            "discovered_count": len(self.groups),
            "valid_count": len(valid),
            "skipped_count": len(skipped),
            "group_count": len(valid),
            "tx_positions_mm": self._common_positions(valid),
            "supported_probe_profiles": {
                name: list(positions) for name, positions in SUPPORTED_16X16_PROFILES.items()
            },
            "tx_column_unit": self._common_value(item.tx_column_unit for item in valid) or "not_applicable",
            "groups": [item.to_dict() for item in self.groups],
            "warnings": [
                f"{Path(item.path).name}: {item.reason}"
                for item in skipped
            ],
        }

    @staticmethod
    def _common_value(values: Iterable[str | None]) -> str | None:
        present = {value for value in values if value}
        return next(iter(present)) if len(present) == 1 else ("mixed" if present else None)

    @staticmethod
    def _common_positions(groups: list[ValidatedDataGroup]) -> list[float] | str:
        values = {tuple(item.tx_positions_mm) for item in groups if item.tx_positions_mm}
        if len(values) == 1:
            return list(next(iter(values)))
        return "mixed" if values else []


class FmcDataInspector:
    """Discover and validate the three FMC layouts supported by MATLAB load_data.m."""

    def __init__(
        self,
        positions_mm: Iterable[float] | None = None,
        *,
        max_depth: int = 8,
        supported_profiles: dict[str, Iterable[float]] | None = None,
    ) -> None:
        self.positions_mm = [float(value) for value in (positions_mm or DEFAULT_16X16_POSITIONS_MM)]
        profiles = supported_profiles or SUPPORTED_16X16_PROFILES
        self.supported_profiles = {
            name: [float(value) for value in values]
            for name, values in profiles.items()
        }
        self.tx_count = len(self.positions_mm)
        self.rx_count = self.tx_count
        self.max_depth = max_depth
        self._cache: dict[tuple[str, tuple[tuple[str, int, int], ...]], ValidatedDataGroup] = {}

    def inspect(self, source_path: Path | str) -> InspectionResult:
        source = Path(source_path)
        candidates = self._discover(source)
        groups = [self._inspect_cached(path, format_name) for path, format_name in candidates]
        groups.sort(key=lambda item: item.path.casefold())
        return InspectionResult(groups)

    def _discover(self, source: Path) -> list[tuple[Path, str]]:
        if source.is_file():
            return [(source, "wide_fmc")] if source.suffix.lower() == ".txt" else []
        if not source.is_dir():
            return []

        found: list[tuple[Path, str]] = []
        seen: set[str] = set()

        def add(path: Path, format_name: str) -> None:
            key = str(path.resolve()).casefold()
            if key not in seen:
                seen.add(key)
                found.append((path, format_name))

        def walk(directory: Path, depth: int) -> None:
            if depth > self.max_depth:
                return
            try:
                children = list(directory.iterdir())
            except OSError:
                return
            tx_dirs = [item for item in children if item.is_dir() and self._tx_index(item) is not None]
            if tx_dirs:
                add(directory, "tx_rx_directory")
                return

            txt_files = [item for item in children if item.is_file() and item.suffix.lower() == ".txt"]
            wide_files: list[Path] = []
            legacy_files: list[Path] = []
            for file_path in txt_files:
                columns = self._first_numeric_column_count(file_path)
                if columns >= 1 + self.tx_count * self.rx_count:
                    wide_files.append(file_path)
                elif columns >= 2 + self.rx_count:
                    legacy_files.append(file_path)
            for file_path in wide_files:
                add(file_path, "wide_fmc")
            if len(legacy_files) >= self.tx_count:
                add(directory, "legacy_tx_table")
                return
            for child in sorted((item for item in children if item.is_dir()), key=lambda item: item.name.casefold()):
                if child.name.strip().lower() in {"none", "background", "background_reference", "reference"}:
                    continue
                walk(child, depth + 1)

        walk(source, 0)
        return found

    def _inspect_cached(self, path: Path, format_name: str) -> ValidatedDataGroup:
        signature = self._signature(path, format_name)
        key = (str(path.resolve()).casefold(), signature)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            if format_name == "legacy_tx_table":
                result = self._validate_legacy(path)
            elif format_name == "tx_rx_directory":
                result = self._validate_tx_rx(path)
            else:
                result = self._validate_wide(path)
        except (OSError, ValueError) as exc:
            result = ValidatedDataGroup(
                path=str(path.resolve()),
                format=format_name,
                probe_geometry_source="file" if format_name == "legacy_tx_table" else "default_profile",
                status="skipped",
                reason=str(exc),
                tx_count=self.tx_count,
                rx_count=self.rx_count,
            )
        self._cache[key] = result
        return result

    def _validate_legacy(self, directory: Path) -> ValidatedDataGroup:
        files_with_positions: list[tuple[float, str, Path]] = []
        for file_path in directory.glob("*.txt"):
            if self._first_numeric_column_count(file_path) < 2 + self.rx_count:
                continue
            matrix = self._read_matrix(file_path)
            tx_column = matrix[:, 0]
            if not np.allclose(tx_column, tx_column[0], rtol=0, atol=1e-12):
                continue
            raw_position = float(tx_column[0])
            unit = "m" if abs(raw_position) < 1.0 else "mm"
            tx_mm = raw_position * 1000.0 if unit == "m" else raw_position
            files_with_positions.append((tx_mm, unit, file_path))
        files_with_positions.sort(key=lambda item: item[0])
        files = [item[2] for item in files_with_positions]
        if len(files) != self.tx_count:
            raise ValueError(f"Tx文件数量应为{self.tx_count}，实际{len(files)}")
        positions = [item[0] for item in files_with_positions]
        profile = next((
            name for name, supported in self.supported_profiles.items()
            if len(positions) == len(supported) and np.allclose(positions, supported, rtol=0, atol=1e-6)
        ), None)
        if profile is None:
            raise ValueError(f"Tx顺序或阵元位置不属于受支持的30-63或22-70配置，实际为{positions}")

        expected_time: np.ndarray | None = None
        warnings: list[str] = []
        units: set[str] = set()
        sample_count = 0
        sample_rate_hz: float | None = None
        for tx_mm, unit, file_path in files_with_positions:
            matrix = self._read_matrix(file_path)
            required = 2 + self.rx_count
            if matrix.shape[1] < required:
                raise ValueError(f"{file_path.name}列数不足：需要至少{required}列，实际{matrix.shape[1]}列")
            if matrix.shape[1] > required:
                warnings.append(f"{file_path.name}存在额外列，仅使用前{required}列")
            units.add(unit)
            expected_time = self._validate_time_axis(file_path, matrix[:, 1], expected_time)
            sample_count = matrix.shape[0]
            sample_rate_hz = self._sample_rate(matrix[:, 1])
        return self._valid(
            directory, "legacy_tx_table", "data_column", sample_count, warnings,
            tx_column_unit=next(iter(units)) if len(units) == 1 else "mixed",
            positions_mm=positions,
            probe_profile=profile,
            sample_rate_hz=sample_rate_hz,
        )

    def _validate_tx_rx(self, directory: Path) -> ValidatedDataGroup:
        tx_dirs = sorted(
            [item for item in directory.iterdir() if item.is_dir() and self._tx_index(item) is not None],
            key=lambda item: self._tx_index(item) or 0,
        )
        indices = [self._tx_index(item) for item in tx_dirs]
        if indices != list(range(1, self.tx_count + 1)):
            raise ValueError(f"应包含tx_1至tx_{self.tx_count}，实际为{indices}")
        expected_time: np.ndarray | None = None
        sample_count = 0
        sample_rate_hz: float | None = None
        for tx_dir in tx_dirs:
            rx_files = self._numeric_txt_files(tx_dir)
            if len(rx_files) != self.rx_count:
                raise ValueError(f"{tx_dir.name}内Rx文件应为{self.rx_count}个，实际{len(rx_files)}个")
            for rx_file in rx_files:
                matrix = self._read_matrix(rx_file)
                if matrix.shape[1] < 2:
                    raise ValueError(f"{tx_dir.name}/{rx_file.name}需要时间、幅值两列")
                expected_time = self._validate_time_axis(rx_file, matrix[:, 0], expected_time)
                sample_count = matrix.shape[0]
                sample_rate_hz = self._sample_rate(matrix[:, 0])
        return self._valid(
            directory, "tx_rx_directory", "format_profile", sample_count,
            warnings=[f"数据未显式记录阵元坐标，按tx_N目录格式约定自动采用{self._profile_name(self.positions_mm)}探头配置"],
            positions_mm=self.positions_mm,
            probe_profile=self._profile_name(self.positions_mm),
            sample_rate_hz=sample_rate_hz,
        )

    def _validate_wide(self, file_path: Path) -> ValidatedDataGroup:
        matrix = self._read_matrix(file_path)
        required = 1 + self.tx_count * self.rx_count
        if matrix.shape[1] < required:
            raise ValueError(f"FMC宽表需要至少{required}列，实际{matrix.shape[1]}列")
        warnings = []
        if matrix.shape[1] > required:
            warnings.append(f"存在额外列，仅使用前{required}列")
        self._validate_time_axis(file_path, matrix[:, 0], None)
        return self._valid(
            file_path, "wide_fmc", "format_profile", matrix.shape[0], [
                *warnings,
                f"数据未显式记录阵元坐标，按FMC宽表格式约定自动采用{self._profile_name(self.positions_mm)}探头配置",
            ],
            positions_mm=self.positions_mm,
            probe_profile=self._profile_name(self.positions_mm),
            sample_rate_hz=self._sample_rate(matrix[:, 0]),
        )

    def _valid(
        self,
        path: Path,
        format_name: str,
        geometry_source: str,
        sample_count: int,
        warnings: list[str] | None = None,
        tx_column_unit: str | None = None,
        positions_mm: list[float] | None = None,
        probe_profile: str | None = None,
        sample_rate_hz: float | None = None,
    ) -> ValidatedDataGroup:
        return ValidatedDataGroup(
            path=str(path.resolve()),
            format=format_name,
            probe_geometry_source=geometry_source,
            status="valid",
            warnings=list(dict.fromkeys(warnings or [])),
            sample_count=sample_count,
            tx_count=self.tx_count,
            rx_count=self.rx_count,
            tx_column_unit=tx_column_unit,
            probe_profile=probe_profile,
            tx_positions_mm=list(positions_mm or self.positions_mm),
            sample_rate_hz=sample_rate_hz,
        )

    @staticmethod
    def _sample_rate(time_axis: np.ndarray) -> float:
        dt = float(np.median(np.diff(np.asarray(time_axis, dtype=float))))
        if not math.isfinite(dt) or dt <= 0:
            raise ValueError("无法由时间轴计算有效采样率")
        return 1.0 / dt

    def _profile_name(self, positions: list[float]) -> str:
        return next((
            name for name, supported in self.supported_profiles.items()
            if len(positions) == len(supported) and np.allclose(positions, supported, rtol=0, atol=1e-6)
        ), "custom")

    @staticmethod
    def _read_matrix(path: Path) -> np.ndarray:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
            matrix = np.loadtxt(handle, comments="%")
        matrix = np.asarray(matrix, dtype=float)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.ndim != 2 or matrix.size == 0:
            raise ValueError(f"{path.name}没有有效二维数值数据")
        if not np.isfinite(matrix).all():
            raise ValueError(f"{path.name}包含NaN或Inf")
        return matrix

    @staticmethod
    def _validate_time_axis(path: Path, time_axis: np.ndarray, expected: np.ndarray | None) -> np.ndarray:
        values = np.asarray(time_axis, dtype=float)
        if values.size < 2 or np.any(np.diff(values) <= 0):
            raise ValueError(f"{path.name}时间轴必须严格递增且至少包含2个采样点")
        if expected is not None and (
            values.shape != expected.shape or not np.allclose(values, expected, rtol=1e-9, atol=1e-15)
        ):
            raise ValueError(f"{path.name}采样点数或时间轴与同组其他通道不一致")
        return values if expected is None else expected

    @staticmethod
    def _numeric_stem(path: Path) -> float | None:
        try:
            value = float(path.stem)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    @classmethod
    def _numeric_txt_files(cls, directory: Path) -> list[Path]:
        files = [
            item for item in directory.glob("*.txt")
            if item.is_file() and cls._numeric_stem(item) is not None
        ]
        return sorted(files, key=lambda item: cls._numeric_stem(item) or 0.0)

    @staticmethod
    def _tx_index(path: Path) -> int | None:
        name = path.name.lower()
        if not name.startswith("tx_"):
            return None
        try:
            value = int(name[3:])
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    def _first_numeric_column_count(path: Path) -> int:
        try:
            with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
                row = np.genfromtxt(handle, comments="%", max_rows=1)
            return int(np.asarray(row).size)
        except (OSError, ValueError):
            return 0

    def _signature(self, path: Path, format_name: str) -> tuple[tuple[str, int, int], ...]:
        files: list[Path]
        if path.is_file():
            files = [path]
        elif format_name == "tx_rx_directory":
            files = [item for item in path.glob("tx_*/*.txt") if item.is_file()]
        else:
            files = [item for item in path.glob("*.txt") if item.is_file()]
        rows = []
        for item in sorted(files, key=lambda value: str(value).casefold()):
            try:
                stat = item.stat()
                rows.append((str(item.resolve()).casefold(), stat.st_size, stat.st_mtime_ns))
            except OSError:
                rows.append((str(item.resolve()).casefold(), -1, -1))
        return tuple(rows)
