"""视频剪辑与拼接模块 - V2

支持两种模式：
1. 分段导出：将视频按时间段剪切为独立文件（Hook/Gameplay）
2. 商标剪除：剪掉含商标的片段，保留并拼接无商标的片段
"""

import logging
import os
import subprocess
import tempfile
import shutil
from typing import List, Optional

from .config import Config
from .segmenter import TimeSegment, SegmentResult
from .utils import format_timestamp, get_ffmpeg_path

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


def edit_video(
    input_path: str,
    segment_result: SegmentResult,
    output_path: str,
    config: Config,
) -> str:
    """根据片段分析结果剪辑视频（V1兼容：剪除商标，保留无商标片段）

    Args:
        input_path: 输入 mp4 视频路径
        segment_result: 片段分析结果（包含保留片段列表）
        output_path: 输出视频路径
        config: 全局配置

    Returns:
        输出文件路径
    """
    # 无商标，直接复制
    if segment_result.no_trademark:
        logger.info("无商标，直接复制到输出目录")
        return _copy_video(input_path, output_path)

    # 整个视频都是商标
    if segment_result.all_trademark:
        logger.warning("整个视频都是商标，跳过输出")
        return ""

    # 只有一个保留片段且覆盖整个视频
    if len(segment_result.keep_segments) == 1:
        seg = segment_result.keep_segments[0]
        if seg.start <= 0.01 and seg.end >= segment_result.total_duration - 0.01:
            logger.info("保留片段覆盖整个视频，直接复制")
            return _copy_video(input_path, output_path)

    # 需要剪辑拼接
    keep_segments = segment_result.keep_segments
    logger.info(
        f"开始剪辑: 保留 {len(keep_segments)} 个片段, "
        f"剪除 {len(segment_result.trademark_segments)} 个商标片段"
    )

    return _cut_and_concat(input_path, keep_segments, output_path, config)


def _copy_video(input_path: str, output_path: str) -> str:
    """直接复制视频到输出目录"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    shutil.copy2(input_path, output_path)
    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(f"视频已复制到: {output_path} ({file_size_mb:.1f}MB)")
    return output_path


def _cut_and_concat(
    input_path: str,
    keep_segments: List[TimeSegment],
    output_path: str,
    config: Config,
) -> str:
    """剪切保留片段并拼接为最终视频

    Args:
        input_path: 输入视频路径
        keep_segments: 需要保留的片段列表
        output_path: 输出视频路径
        config: 全局配置

    Returns:
        输出文件路径
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 创建临时目录存放片段
    temp_dir = tempfile.mkdtemp(prefix="videoprecut_")
    segment_files = []

    try:
        # 逐个剪切保留片段
        for i, seg in enumerate(keep_segments):
            seg_filename = f"segment_{i:04d}.mp4"
            seg_path = os.path.join(temp_dir, seg_filename)

            logger.info(
                f"  剪切片段 {i+1}/{len(keep_segments)}: "
                f"{format_timestamp(seg.start)} - {format_timestamp(seg.end)} "
                f"({seg.duration:.3f}s)"
            )

            result = cut_segment(input_path, seg, seg_path, config)

            if result and os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                segment_files.append(seg_path)
            else:
                logger.warning(f"  片段 {i+1} 剪切失败，跳过")

        if not segment_files:
            logger.error("所有片段剪切失败，无法生成输出")
            return ""

        # 拼接所有片段
        if len(segment_files) == 1:
            shutil.move(segment_files[0], output_path)
        else:
            _concat_segments(segment_files, output_path, config)

        # 验证输出
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"剪辑完成: {output_path} ({file_size_mb:.1f}MB)")
        else:
            logger.error("输出文件无效")
            return ""

        return output_path

    finally:
        _cleanup_temp(temp_dir, segment_files)


def _concat_segments(
    segment_files: List[str],
    output_path: str,
    config: Config,
) -> None:
    """使用 FFmpeg concat demuxer 拼接多个片段"""
    concat_file = os.path.join(os.path.dirname(segment_files[0]), "concat.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for seg_path in segment_files:
            f.write(f"file '{os.path.abspath(seg_path)}'\n")

    cmd = [
        get_ffmpeg_path(), "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        "-movflags", "+faststart",
        "-loglevel", "error",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.ffmpeg_timeout)
        if result.returncode != 0:
            logger.error(f"FFmpeg 拼接失败: {result.stderr[-300:]}")
            _concat_segments_reencode(segment_files, output_path, config)
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg 拼接超时")
        raise
    finally:
        if os.path.exists(concat_file):
            os.remove(concat_file)


def _concat_segments_reencode(
    segment_files: List[str],
    output_path: str,
    config: Config,
) -> None:
    """使用重编码方式拼接片段（回退方案）"""
    logger.info("  回退到重编码拼接模式")

    inputs = []
    filter_parts = []
    n = len(segment_files)

    for i, seg_path in enumerate(segment_files):
        inputs.extend(["-i", seg_path])
        filter_parts.append(f"[{i}:v][{i}:a]")

    filter_str = "".join(filter_parts) + f"concat=n={n}:v=1:a=1[outv][outa]"

    cmd = [
        get_ffmpeg_path(), "-y",
        *inputs,
        "-filter_complex", filter_str,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", config.video_codec,
        "-c:a", config.audio_codec,
        "-crf", str(config.crf),
        "-preset", config.preset,
        "-movflags", "+faststart",
        "-loglevel", "error",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.ffmpeg_timeout * 2)
    if result.returncode != 0:
        logger.error(f"重编码拼接也失败: {result.stderr[-300:]}")
        raise RuntimeError("FFmpeg 拼接失败")


def _cleanup_temp(temp_dir: str, segment_files: List[str]) -> None:
    """清理临时文件"""
    try:
        for f in segment_files:
            if os.path.exists(f):
                os.remove(f)
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as e:
        logger.debug(f"清理临时文件时出错: {e}")
