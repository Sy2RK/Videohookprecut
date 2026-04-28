"""商标片段分析与合并模块

核心逻辑：检测出含商标的片段 → 计算需要剪除的时间段 → 
求补集得到需要保留的无商标片段
"""

import logging
from dataclasses import dataclass, field
from typing import List, Tuple

from .config import Config
from .detector import VideoDetectionResult, Detection

logger = logging.getLogger("videoprecut.segmenter")


@dataclass
class TimeSegment:
    """时间段"""

    start: float  # 起始时间（秒）
    end: float  # 结束时间（秒）

    @property
    def duration(self) -> float:
        """时长"""
        return max(0.0, self.end - self.start)

    def __repr__(self) -> str:
        return f"[{self.start:.3f}s - {self.end:.3f}s] ({self.duration:.3f}s)"


@dataclass
class SegmentResult:
    """片段分析结果"""

    # 需要剪除的商标片段
    trademark_segments: List[TimeSegment] = field(default_factory=list)
    # 需要保留的无商标片段
    keep_segments: List[TimeSegment] = field(default_factory=list)
    # 视频总时长
    total_duration: float = 0.0
    # 是否整个视频都有商标
    all_trademark: bool = False
    # 是否没有检测到任何商标
    no_trademark: bool = False

    @property
    def total_trademark_duration(self) -> float:
        """商标片段总时长"""
        return sum(seg.duration for seg in self.trademark_segments)

    @property
    def total_keep_duration(self) -> float:
        """保留片段总时长"""
        return sum(seg.duration for seg in self.keep_segments)

    @property
    def trademark_ratio(self) -> float:
        """商标占比"""
        if self.total_duration <= 0:
            return 0.0
        return self.total_trademark_duration / self.total_duration


def analyze_segments(
    detection_result: VideoDetectionResult,
    config: Config,
) -> SegmentResult:
    """分析检测结果，计算需要保留的无商标片段

    处理流程：
    1. 将检测到商标的帧按时间排序
    2. 合并连续帧为商标片段
    3. 添加前后缓冲区间
    4. 合并重叠或间隔极小的片段
    5. 过滤掉时长极短的片段
    6. 计算商标片段的补集 = 需要保留的无商标片段

    Args:
        detection_result: YOLO 检测结果
        config: 全局配置

    Returns:
        SegmentResult 包含商标片段和保留片段
    """
    total_duration = detection_result.duration
    detections = detection_result.detections

    result = SegmentResult(total_duration=total_duration)

    # ── 情况1: 没有检测到任何商标 ──
    if not detections:
        result.no_trademark = True
        result.keep_segments = [TimeSegment(start=0.0, end=total_duration)]
        logger.info("未检测到商标，整个视频将保留")
        return result

    # ── 步骤1: 将检测帧转换为时间点集合 ──
    trademark_timestamps = sorted(set(d.timestamp for d in detections))
    logger.debug(f"检测到 {len(trademark_timestamps)} 个商标时间点")

    # ── 步骤2: 合并连续时间点为商标片段 ──
    raw_segments = _merge_timestamps_to_segments(
        trademark_timestamps,
        fps=detection_result.fps,
        merge_gap=config.merge_gap_sec,
    )
    logger.debug(f"合并为 {len(raw_segments)} 个原始商标片段")

    # ── 步骤3: 添加前后缓冲区间 ──
    buffered_segments = _add_buffer(
        raw_segments,
        buffer_before=config.buffer_before_sec,
        buffer_after=config.buffer_after_sec,
        total_duration=total_duration,
    )
    logger.debug(f"添加缓冲后 {len(buffered_segments)} 个商标片段")

    # ── 步骤4: 合并重叠片段 ──
    merged_segments = _merge_overlapping_segments(buffered_segments)
    logger.debug(f"合并重叠后 {len(merged_segments)} 个商标片段")

    # ── 步骤5: 过滤极短片段 ──
    filtered_segments = [
        seg for seg in merged_segments
        if seg.duration >= config.min_segment_sec
    ]
    logger.debug(f"过滤极短片段后 {len(filtered_segments)} 个商标片段")

    # ── 情况2: 整个视频都是商标 ──
    if len(filtered_segments) == 1 and \
       filtered_segments[0].start <= 0.01 and \
       filtered_segments[0].end >= total_duration - 0.01:
        result.all_trademark = True
        result.trademark_segments = filtered_segments
        result.keep_segments = []
        logger.warning("整个视频都被商标覆盖，输出将为空")
        return result

    # ── 步骤6: 计算补集 = 需要保留的无商标片段 ──
    keep_segments = _compute_complement(filtered_segments, total_duration)

    result.trademark_segments = filtered_segments
    result.keep_segments = keep_segments

    logger.info(
        f"片段分析完成: "
        f"商标片段 {len(filtered_segments)} 个 "
        f"({result.total_trademark_duration:.1f}s / {total_duration:.1f}s, "
        f"占比 {result.trademark_ratio:.1%}) | "
        f"保留片段 {len(keep_segments)} 个 "
        f"({result.total_keep_duration:.1f}s)"
    )

    for i, seg in enumerate(filtered_segments):
        logger.info(f"  商标片段 {i+1}: {seg}")
    for i, seg in enumerate(keep_segments):
        logger.info(f"  保留片段 {i+1}: {seg}")

    return result


def _merge_timestamps_to_segments(
    timestamps: List[float],
    fps: float,
    merge_gap: float,
) -> List[TimeSegment]:
    """将时间点合并为连续片段

    如果相邻两个时间点的间隔小于 merge_gap，则归为同一片段。
    否则，间隔大于一帧时间但小于 merge_gap 的也合并（处理漏检帧）。

    Args:
        timestamps: 排序后的时间戳列表
        fps: 视频帧率
        merge_gap: 合并间隔阈值（秒）

    Returns:
        合并后的时间段列表
    """
    if not timestamps:
        return []

    frame_interval = 1.0 / fps if fps > 0 else 0.033  # 默认30fps
    segments = []
    seg_start = timestamps[0]
    seg_end = timestamps[0]

    for ts in timestamps[1:]:
        # 如果间隔小于合并阈值，扩展当前片段
        if ts - seg_end <= merge_gap:
            seg_end = ts
        else:
            # 保存当前片段，开始新片段
            # 片段结束时间 = 最后一个检测帧时间 + 一帧时长
            segments.append(TimeSegment(start=seg_start, end=seg_end + frame_interval))
            seg_start = ts
            seg_end = ts

    # 保存最后一个片段
    segments.append(TimeSegment(start=seg_start, end=seg_end + frame_interval))

    return segments


def _add_buffer(
    segments: List[TimeSegment],
    buffer_before: float,
    buffer_after: float,
    total_duration: float,
) -> List[TimeSegment]:
    """为每个商标片段添加前后缓冲区间

    Args:
        segments: 原始商标片段
        buffer_before: 前缓冲时长（秒）
        buffer_after: 后缓冲时长（秒）
        total_duration: 视频总时长

    Returns:
        添加缓冲后的片段列表
    """
    buffered = []
    for seg in segments:
        start = max(0.0, seg.start - buffer_before)
        end = min(total_duration, seg.end + buffer_after)
        buffered.append(TimeSegment(start=start, end=end))
    return buffered


def _merge_overlapping_segments(
    segments: List[TimeSegment],
) -> List[TimeSegment]:
    """合并重叠或相邻的片段

    Args:
        segments: 已排序的时间段列表

    Returns:
        合并后的时间段列表
    """
    if not segments:
        return []

    # 按起始时间排序
    sorted_segments = sorted(segments, key=lambda s: s.start)
    merged = [sorted_segments[0]]

    for seg in sorted_segments[1:]:
        last = merged[-1]
        if seg.start <= last.end:
            # 重叠或相邻，合并
            merged[-1] = TimeSegment(
                start=last.start,
                end=max(last.end, seg.end),
            )
        else:
            merged.append(seg)

    return merged


def _compute_complement(
    trademark_segments: List[TimeSegment],
    total_duration: float,
) -> List[TimeSegment]:
    """计算商标片段的补集 = 需要保留的无商标片段

    例如：
    原始视频: 0──────────────────────────────60s
    商标片段: [4.7-5.8s, 19.9-22.1s, 44.7-46.5s]
    保留片段: [0-4.7s, 5.8-19.9s, 22.1-44.7s, 46.5-60s]

    Args:
        trademark_segments: 商标片段列表（已排序、已合并）
        total_duration: 视频总时长

    Returns:
        保留片段列表
    """
    if not trademark_segments:
        return [TimeSegment(start=0.0, end=total_duration)]

    keep_segments = []
    current_start = 0.0

    for tm_seg in trademark_segments:
        if current_start < tm_seg.start:
            # 在商标片段之前有一段保留区间
            keep_segments.append(
                TimeSegment(start=current_start, end=tm_seg.start)
            )
        current_start = tm_seg.end

    # 最后一个商标片段之后的部分
    if current_start < total_duration:
        keep_segments.append(
            TimeSegment(start=current_start, end=total_duration)
        )

    return keep_segments