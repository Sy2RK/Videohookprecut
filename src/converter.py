"""视频格式转换模块 - 将非mp4格式转换为mp4"""

import logging
import os
import subprocess
from typing import Optional

from .config import Config
from .ingestion import VideoInfo
from .utils import get_ffmpeg_path

logger = logging.getLogger("videoprecut.converter")


def convert_to_mp4(video: VideoInfo, config: Config) -> str:
    """将视频转换为 mp4 格式

    如果视频已经是 mp4，直接返回原路径。
    否则使用 FFmpeg 转换并返回转换后的文件路径。

    Args:
        video: 视频信息对象
        config: 全局配置

    Returns:
        mp4 文件路径
    """
    if video.is_mp4:
        logger.info(f"视频已是 mp4 格式，跳过转换: {video.filename}")
        return video.filepath

    # 构造输出路径
    temp_dir = os.path.join(config.temp_dir, "converted")
    os.makedirs(temp_dir, exist_ok=True)
    output_path = os.path.join(temp_dir, f"{video.stem}.mp4")

    # 如果已存在转换后的文件，直接使用
    if os.path.exists(output_path):
        logger.info(f"已存在转换文件，跳过: {output_path}")
        return output_path

    logger.info(f"开始转换: {video.filename} -> {video.stem}.mp4")

    cmd = [
        get_ffmpeg_path(),
        "-y",  # 覆盖输出文件
        "-i", video.filepath,
        "-c:v", config.video_codec,
        "-c:a", config.audio_codec,
        "-crf", str(config.crf),
        "-preset", config.preset,
        "-movflags", "+faststart",  # 优化网络播放
        "-progress", "pipe:1",  # 输出进度信息
        output_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10分钟超时
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg 转换失败: {result.stderr[-500:]}")
            raise RuntimeError(f"FFmpeg 转换失败: {video.filename}")

        # 验证输出文件
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"转换后文件无效: {output_path}")

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info(
            f"转换完成: {video.filename} -> {video.stem}.mp4 "
            f"({file_size_mb:.1f}MB)"
        )
        return output_path

    except subprocess.TimeoutExpired:
        logger.error(f"转换超时: {video.filename}")
        # 清理不完整的输出文件
        if os.path.exists(output_path):
            os.remove(output_path)
        raise
    except Exception as e:
        logger.error(f"转换失败: {video.filename} - {e}")
        if os.path.exists(output_path):
            os.remove(output_path)
        raise


def ensure_mp4(video: VideoInfo, config: Config) -> str:
    """确保视频为 mp4 格式，必要时进行转换

    Args:
        video: 视频信息对象
        config: 全局配置

    Returns:
        mp4 文件路径
    """
    return convert_to_mp4(video, config)


def cleanup_converted(config: Config) -> None:
    """清理格式转换产生的临时文件

    在所有视频处理完成后调用，删除 temp/converted/ 目录下的临时 mp4 文件。

    Args:
        config: 全局配置
    """
    converted_dir = os.path.join(config.temp_dir, "converted")
    if not os.path.exists(converted_dir):
        return

    cleaned = 0
    for filename in os.listdir(converted_dir):
        filepath = os.path.join(converted_dir, filename)
        try:
            if os.path.isfile(filepath):
                os.remove(filepath)
                cleaned += 1
        except Exception as e:
            logger.debug(f"清理临时文件失败: {filepath} - {e}")

    if cleaned > 0:
        logger.info(f"已清理 {cleaned} 个格式转换临时文件")