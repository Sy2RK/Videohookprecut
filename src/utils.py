"""工具函数模块 - V2"""

import base64
import io
import logging
import os
import subprocess
import json
import re
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger("videoprecut")

# ── FFmpeg/FFprobe 路径解析 ──

_ffmpeg_path: Optional[str] = None
_ffprobe_path: Optional[str] = None


def _resolve_ffmpeg() -> str:
    """解析 ffmpeg 可执行文件路径

    优先使用系统 PATH 中的 ffmpeg，若不存在则回退到 imageio-ffmpeg 包。
    """
    global _ffmpeg_path
    if _ffmpeg_path is not None:
        return _ffmpeg_path

    # 1. 尝试系统 PATH
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, timeout=5
        )
        if result.returncode == 0:
            _ffmpeg_path = "ffmpeg"
            return _ffmpeg_path
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 2. 尝试 imageio-ffmpeg
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.isfile(ffmpeg_exe):
            _ffmpeg_path = ffmpeg_exe
            # 将 imageio-ffmpeg 的 bin 目录加入 PATH，方便其他模块直接用 "ffmpeg"
            bin_dir = os.path.dirname(ffmpeg_exe)
            if bin_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            logger.info(f"使用 imageio-ffmpeg: {ffmpeg_exe}")
            return _ffmpeg_path
    except ImportError:
        pass

    _ffmpeg_path = ""
    return _ffmpeg_path


def _resolve_ffprobe() -> str:
    """解析 ffprobe 可执行文件路径

    优先使用系统 PATH 中的 ffprobe，若不存在返回空字符串。
    imageio-ffmpeg 不包含 ffprobe。
    """
    global _ffprobe_path
    if _ffprobe_path is not None:
        return _ffprobe_path

    try:
        result = subprocess.run(
            ["ffprobe", "-version"], capture_output=True, timeout=5
        )
        if result.returncode == 0:
            _ffprobe_path = "ffprobe"
            return _ffprobe_path
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    _ffprobe_path = ""
    return _ffprobe_path


def get_ffmpeg_path() -> str:
    """获取 ffmpeg 可执行文件路径，未找到则抛出异常"""
    path = _resolve_ffmpeg()
    if not path:
        raise FileNotFoundError(
            "未找到 ffmpeg，请安装 FFmpeg 或安装 imageio-ffmpeg 包"
        )
    return path


def get_ffprobe_path() -> Optional[str]:
    """获取 ffprobe 可执行文件路径，未找到返回 None"""
    path = _resolve_ffprobe()
    return path if path else None


def setup_ffmpeg_env() -> bool:
    """初始化 FFmpeg 环境，解析路径并确保可用

    Returns:
        True 如果 ffmpeg 可用
    """
    ffmpeg = _resolve_ffmpeg()
    ffprobe = _resolve_ffprobe()

    if ffmpeg:
        logger.debug(f"ffmpeg: {ffmpeg}")
    if ffprobe:
        logger.debug(f"ffprobe: {ffprobe}")
    elif ffmpeg:
        logger.info("ffprobe 不可用，将使用 ffmpeg -i 作为回退方案获取视频信息")

    return bool(ffmpeg)


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> logging.Logger:
    """配置日志系统"""
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # 文件处理器
    file_handler = logging.FileHandler(
        os.path.join(log_dir, "videoprecut.log"), encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger("videoprecut")
    root_logger.setLevel(level)
    # 避免重复添加处理器
    if not root_logger.handlers:
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)

    return root_logger


def get_video_info(video_path: str) -> Optional[Dict[str, Any]]:
    """获取视频元信息

    优先使用 ffprobe，若不可用则回退到 ffmpeg -i 解析。
    """
    ffprobe = get_ffprobe_path()
    if ffprobe:
        return _get_video_info_ffprobe(ffprobe, video_path)
    else:
        return _get_video_info_ffmpeg_fallback(video_path)


def _get_video_info_ffprobe(
    ffprobe_path: str, video_path: str
) -> Optional[Dict[str, Any]]:
    """使用 ffprobe 获取视频元信息"""
    try:
        cmd = [
            ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.warning(f"ffprobe 执行失败: {video_path}")
            return _get_video_info_ffmpeg_fallback(video_path)

        info = json.loads(result.stdout)

        # 提取关键信息
        format_info = info.get("format", {})
        streams = info.get("streams", [])

        video_stream = None
        audio_stream = None
        for s in streams:
            if s.get("codec_type") == "video" and video_stream is None:
                video_stream = s
            elif s.get("codec_type") == "audio" and audio_stream is None:
                audio_stream = s

        duration = float(format_info.get("duration", 0))
        fps = 30.0  # 默认值
        width = 0
        height = 0

        if video_stream:
            # 解析帧率
            r_frame_rate = video_stream.get("r_frame_rate", "30/1")
            if "/" in r_frame_rate:
                num, den = r_frame_rate.split("/")
                fps = float(num) / float(den) if float(den) > 0 else 30.0
            width = int(video_stream.get("width", 0))
            height = int(video_stream.get("height", 0))

        return {
            "duration": duration,
            "fps": fps,
            "width": width,
            "height": height,
            "format": format_info.get("format_name", ""),
            "has_video": video_stream is not None,
            "has_audio": audio_stream is not None,
            "video_codec": video_stream.get("codec_name", "") if video_stream else "",
            "audio_codec": audio_stream.get("codec_name", "") if audio_stream else "",
        }
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
        logger.error(f"获取视频信息失败 {video_path}: {e}")
        return _get_video_info_ffmpeg_fallback(video_path)


def _get_video_info_ffmpeg_fallback(video_path: str) -> Optional[Dict[str, Any]]:
    """使用 ffmpeg -i 作为回退方案获取视频元信息（当 ffprobe 不可用时）"""
    try:
        ffmpeg_path = get_ffmpeg_path()
        cmd = [ffmpeg_path, "-i", video_path]
        # ffmpeg -i 不加输出文件会报错，但 stderr 会包含视频信息
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        stderr = result.stderr

        info: Dict[str, Any] = {
            "duration": 0.0,
            "fps": 30.0,
            "width": 0,
            "height": 0,
            "format": "",
            "has_video": False,
            "has_audio": False,
            "video_codec": "",
            "audio_codec": "",
        }

        # 解析时长: Duration: 00:01:23.45
        dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", stderr)
        if dur_match:
            h, m, s = dur_match.groups()
            info["duration"] = int(h) * 3600 + int(m) * 60 + float(s)

        # 解析视频流: Stream #0:0[0x1](und): Video: h264 ..., 426x640 [SAR ...]
        # 注意: 避免匹配 (avc1 / 0x31637661) 中的 0xNNNN 格式
        video_match = re.search(
            r"Stream\s+#\d+:\d+(?:\[0x[0-9a-fA-F]+\])?(?:\(\w+\))?:\s*Video:\s*(\w+).*?,\s*(\d+)x(\d+)",
            stderr,
        )
        if video_match:
            info["has_video"] = True
            info["video_codec"] = video_match.group(1)
            info["width"] = int(video_match.group(2))
            info["height"] = int(video_match.group(3))

            # 尝试解析帧率
            fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)
            if fps_match:
                info["fps"] = float(fps_match.group(1))
            else:
                # 尝试解析 tbr/tbn
                tbr_match = re.search(r"(\d+(?:\.\d+)?)\s*tbr", stderr)
                if tbr_match:
                    info["fps"] = float(tbr_match.group(1))

        # 解析音频流: Stream #0:1[0x2](eng): Audio: aac ...
        audio_match = re.search(
            r"Stream\s+#\d+:\d+(?:\[0x[0-9a-fA-F]+\])?(?:\(\w+\))?:\s*Audio:\s*(\w+)", stderr
        )
        if audio_match:
            info["has_audio"] = True
            info["audio_codec"] = audio_match.group(1)

        return info

    except Exception as e:
        logger.error(f"ffmpeg 回退获取视频信息失败 {video_path}: {e}")
        return None


def format_timestamp(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS.mmm 时间戳"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def ensure_dir(path: str) -> None:
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


def safe_filename(filename: str) -> str:
    """生成安全的文件名"""
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)


def check_ffmpeg() -> bool:
    """检查 ffmpeg 是否可用（ffprobe 可选）"""
    return setup_ffmpeg_env()


def sample_keyframes(
    video_path: str,
    num_frames: int = 8,
    start_time: float = 0.0,
    end_time: Optional[float] = None,
) -> List[Tuple[float, "PIL.Image.Image"]]:
    """从视频中均匀采样关键帧

    Args:
        video_path: 视频文件路径
        num_frames: 采样帧数
        start_time: 起始时间（秒）
        end_time: 结束时间（秒），None表示到视频末尾

    Returns:
        [(时间戳, PIL图像), ...] 列表
    """
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_duration = total_frames / fps if fps > 0 else 0.0

    if end_time is None:
        end_time = video_duration

    actual_duration = end_time - start_time
    if actual_duration <= 0:
        cap.release()
        return []

    # 计算采样时间点
    if num_frames == 1:
        sample_times = [start_time + actual_duration / 2]
    else:
        interval = actual_duration / (num_frames - 1)
        sample_times = [start_time + i * interval for i in range(num_frames)]

    frames = []
    for t in sample_times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if ret:
            # BGR -> RGB
            frame_rgb = frame[:, :, ::-1]
            # 转为 PIL Image
            from PIL import Image
            pil_image = Image.fromarray(frame_rgb)
            frames.append((t, pil_image))

    cap.release()
    return frames


def extract_segment_frames(
    video_path: str,
    start: float,
    end: float,
    num_frames: int = 4,
) -> List[Tuple[float, "PIL.Image.Image"]]:
    """提取视频指定时间段的帧

    Args:
        video_path: 视频文件路径
        start: 起始时间（秒）
        end: 结束时间（秒）
        num_frames: 采样帧数

    Returns:
        [(时间戳, PIL图像), ...] 列表
    """
    return sample_keyframes(video_path, num_frames, start_time=start, end_time=end)


def image_to_base64(image: "PIL.Image.Image", format: str = "JPEG", quality: int = 85) -> str:
    """将 PIL Image 转换为 base64 编码字符串

    Args:
        image: PIL 图像对象
        format: 图像格式 (JPEG/PNG)
        quality: JPEG 质量

    Returns:
        base64 编码字符串
    """
    buffer = io.BytesIO()
    image.save(buffer, format=format, quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def save_metadata(
    output_path: str,
    data: dict,
) -> None:
    """保存 JSON 元数据文件

    Args:
        output_path: 输出文件路径
        data: 元数据字典
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.debug(f"元数据已保存: {output_path}")
