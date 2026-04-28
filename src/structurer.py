"""视频结构分析模块

整合多模态AI分析结果，确定最终的视频分段：
Hook（开头纯图像）+ Gameplay（玩法展示）+ 商标/Try Now（结尾）

核心逻辑：
1. 多模态AI分析视频整体结构，获取 hook_end 和 trademark_start
2. 如果 AI 判断无 Hook，标记为丢弃
3. 商标最小时长验证（防止AI幻觉）
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .analyzer import VideoStructure

logger = logging.getLogger("videoprecut.structurer")


@dataclass
class TimeSegment:
    """时间段"""

    start: float  # 起始时间（秒）
    end: float  # 结束时间（秒）

    @property
    def duration(self) -> float:
        """时长"""
        return max(0.0, self.end - self.start)


@dataclass
class VideoStructureResult:
    """视频结构分析最终结果"""

    has_hook: bool  # 是否存在Hook
    has_trademark: bool = False  # 是否存在商标/结束画面
    should_discard: bool = False  # 是否应丢弃

    # 三段式时间分段
    hook_segment: Optional[TimeSegment] = None  # Hook时间段
    gameplay_segment: Optional[TimeSegment] = None  # Gameplay时间段
    trademark_segment: Optional[TimeSegment] = None  # 商标时间段（可能为空）

    # Hook描述
    hook_description: str = ""  # Hook一句话描述

    # 元信息
    ai_confidence: float = 0.0  # AI分析置信度

    @property
    def hook_duration(self) -> float:
        """Hook时长"""
        return self.hook_segment.duration if self.hook_segment else 0.0

    @property
    def gameplay_duration(self) -> float:
        """Gameplay时长"""
        return self.gameplay_segment.duration if self.gameplay_segment else 0.0

    @property
    def trademark_duration(self) -> float:
        """商标时长"""
        return self.trademark_segment.duration if self.trademark_segment else 0.0


def analyze_video_structure(
    ai_structure: VideoStructure,
    video_duration: float,
    config: Config,
) -> VideoStructureResult:
    """整合AI分析结果，确定最终视频分段

    Args:
        ai_structure: 多模态AI分析的视频结构
        video_duration: 视频总时长
        config: 全局配置

    Returns:
        VideoStructureResult 最终视频结构
    """
    result = VideoStructureResult(
        has_hook=ai_structure.has_hook,
        should_discard=False,
        ai_confidence=ai_structure.confidence,
    )

    # ── 情况1: 无Hook → 丢弃 ──
    if not ai_structure.has_hook:
        if config.discard_no_hook:
            result.should_discard = True
            logger.info("无Hook，素材将被丢弃")
        else:
            # 不丢弃，整个视频作为gameplay
            result.gameplay_segment = TimeSegment(start=0.0, end=video_duration)
            logger.info("无Hook但配置为不丢弃，整个视频作为gameplay")
        return result

    # ── 情况2: 有Hook → 三段式分段 ──
    hook_end = ai_structure.hook_end_seconds
    trademark_start = ai_structure.trademark_start_seconds
    has_trademark = ai_structure.has_trademark

    # Hook安全缓冲：从AI判断的hook_end提前，确保Hook中不含任何Gameplay内容
    hook_end_raw = hook_end
    hook_end = max(0.0, hook_end - config.hook_safety_buffer)
    if hook_end < hook_end_raw:
        logger.info(
            f"Hook安全缓冲: {hook_end_raw:.1f}s → {hook_end:.1f}s "
            f"(提前{config.hook_safety_buffer:.1f}s，确保Hook不含Gameplay)"
        )

    # Hook最小时长检查：缓冲后如果Hook太短，视为无Hook
    if hook_end < config.hook_min_duration:
        logger.info(
            f"Hook时长过短({hook_end:.1f}s < {config.hook_min_duration:.1f}s)，视为无Hook"
        )
        if config.discard_no_hook:
            result.should_discard = True
            logger.info("无有效Hook，素材将被丢弃")
        else:
            result.has_hook = False
            result.gameplay_segment = TimeSegment(start=0.0, end=video_duration)
            logger.info("无有效Hook但配置为不丢弃，整个视频作为gameplay")
        return result

    # ── 商标最小时长验证（防止AI幻觉） ──
    trademark_duration = video_duration - trademark_start
    if has_trademark and trademark_duration < config.trademark_min_duration:
        logger.info(
            f"商标时长过短({trademark_duration:.1f}s < {config.trademark_min_duration:.1f}s)，"
            f"视为AI误判，不算商标"
        )
        has_trademark = False
        trademark_start = video_duration

    # ── 无商标检查 → 丢弃 ──
    if not has_trademark:
        if config.discard_no_trademark:
            result.should_discard = True
            logger.info("无商标/结束画面，素材将被丢弃")
        else:
            result.has_trademark = False
            result.gameplay_segment = TimeSegment(start=hook_end, end=video_duration)
            logger.info("无商标但配置为不丢弃，Hook后全部作为gameplay")
        return result

    result.has_trademark = True

    # 确保时间戳合理性
    hook_end = max(0.0, min(hook_end, video_duration))
    trademark_start = max(hook_end, min(trademark_start, video_duration))

    # 构建三段式分段
    result.hook_segment = TimeSegment(start=0.0, end=hook_end)

    # Gameplay: hook_end → trademark_start
    if trademark_start > hook_end:
        result.gameplay_segment = TimeSegment(start=hook_end, end=trademark_start)
    else:
        # 没有商标部分，gameplay延伸到视频末尾
        result.gameplay_segment = TimeSegment(start=hook_end, end=video_duration)

    # 商标: trademark_start → end（仅当存在时）
    if trademark_start < video_duration - config.trademark_min_duration:
        result.trademark_segment = TimeSegment(
            start=trademark_start, end=video_duration
        )

    logger.info(
        f"视频结构: Hook[0-{hook_end:.1f}s] + "
        f"Gameplay[{hook_end:.1f}-{trademark_start:.1f}s] + "
        f"商标[{trademark_start:.1f}-{video_duration:.1f}s]"
    )

    return result
