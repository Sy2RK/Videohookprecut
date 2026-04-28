"""视频剪辑模块 - V2

将视频按时间段剪切为独立文件（Hook/Gameplay）
"""

import logging
import os
import subprocess
from typing import Optional

from .config import Config
from .structurer import TimeSegment
from .utils import get_ffmpeg_path

logger = logging.getLogger("videoprecut.editor")


def cut_segment(
    input_path: str,
    segment: TimeSegment,
    output_path: str,
    config: Config,
) -> str:
    """从视频中剪切一个时间段并导出为独立文件

    Args:
        input_path: 输入 mp4 视频路径
        segment: 要剪切的时间段
        output_path: 输出视频路径
        config: 全局配置

    Returns:
        输出文件路径，失败返回空字符串
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    duration = segment.duration
    if duration <= 0:
        logger.warning(f"片段时长为0，跳过: {segment}")
        return ""

    logger.info(
        f"  剪切片段: {format_timestamp(segment.start)} - "
        f"{format_timestamp(segment.end)} ({duration:.3f}s)"
    )

    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-ss", f"{segment.start:.6f}",       # 先 seek（快速定位）
        "-i", input_path,
        "-t", f"{duration:.6f}",              # 持续时长
        "-c:v", config.video_codec,           # 重编码确保精度
        "-c:a", config.audio_codec,
        "-crf", str(config.crf),
        "-preset", config.preset,
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        "-loglevel", "error",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.ffmpeg_timeout)
        if result.returncode != 0:
            logger.error(f"FFmpeg 剪切失败: {result.stderr[-300:]}")
            # 回退到流复制模式
            return _cut_segment_copy(input_path, segment, output_path, config)

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"  剪切完成: {output_path} ({file_size_mb:.1f}MB)")
            return output_path
        else:
            logger.error("输出文件无效")
            return ""

    except subprocess.TimeoutExpired:
        logger.error("FFmpeg 剪切超时")
        return ""


def _cut_segment_copy(
    input_path: str,
    segment: TimeSegment,
    output_path: str,
    config: Config,
) -> str:
    """使用流复制模式剪切片段（回退方案）

    Args:
        input_path: 输入视频路径
        segment: 时间段
        output_path: 输出片段路径
        config: 全局配置

    Returns:
        输出文件路径
    """
    duration = segment.duration

    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-ss", f"{segment.start:.6f}",
        "-i", input_path,
        "-t", f"{duration:.6f}",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-loglevel", "error",
        output_path,
    ]

    logger.info("  回退到流复制模式")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=max(config.ffmpeg_timeout // 2, 60))
    if result.returncode != 0:
        logger.error(f"流复制模式也失败: {result.stderr[-300:]}")
        return ""

    return output_path if os.path.exists(output_path) else ""


