"""每日定时调度模块

在指定时间（北京时间）自动执行视频处理工作流。
支持配置每日运行时间，到时间后自动触发完整流程：
  1. 从飞书多维表格下载视频
  2. 处理视频（Hook/Gameplay 切割）
  3. 上传到 Google Drive

用法:
    # 通过 .env 配置
    SCHEDULE_ENABLED=True
    SCHEDULE_TIME=12:00

    # 或命令行
    python -m src.main --schedule --schedule-time 12:00
"""

import logging
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

logger = logging.getLogger("videoprecut.scheduler")

# 北京时间 UTC+8
BEIJING_TZ_OFFSET = timedelta(hours=8)


def _now_beijing() -> datetime:
    """获取当前北京时间"""
    return datetime.utcnow() + BEIJING_TZ_OFFSET


def _parse_time(time_str: str) -> tuple:
    """解析时间字符串为 (hour, minute)

    Args:
        time_str: 时间字符串，格式 "HH:MM"（24小时制）

    Returns:
        (hour, minute) 元组

    Raises:
        ValueError: 格式错误
    """
    try:
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"时间格式错误: {time_str}，应为 HH:MM")
        hour = int(parts[0])
        minute = int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"时间范围错误: {time_str}，小时 0-23，分钟 0-59")
        return hour, minute
    except (ValueError, IndexError) as e:
        raise ValueError(f"时间格式错误: {time_str}，应为 HH:MM") from e


def _seconds_until_next_run(hour: int, minute: int) -> float:
    """计算距离下次运行时间的秒数

    Args:
        hour: 目标小时（北京时间）
        minute: 目标分钟（北京时间）

    Returns:
        距离下次运行的秒数
    """
    now = _now_beijing()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # 如果今天的目标时间已过，则安排到明天
    if target <= now:
        target += timedelta(days=1)

    delta = target - now
    return delta.total_seconds()


def run_daily(
    task: Callable,
    schedule_time: str = "12:00",
    on_complete: Optional[Callable] = None,
) -> None:
    """每日定时执行任务

    阻塞式运行，在每天指定时间（北京时间）执行 task。
    支持 Ctrl+C 优雅退出。

    Args:
        task: 要执行的任务函数（无参数）
        schedule_time: 每日运行时间（北京时间，格式 "HH:MM"）
        on_complete: 每次任务完成后的回调（可选）
    """
    hour, minute = _parse_time(schedule_time)
    logger.info(f"定时调度已启动，每日北京时间 {hour:02d}:{minute:02d} 执行")

    # 优雅退出处理
    running = True

    def _signal_handler(sig, frame):
        nonlocal running
        logger.info("收到退出信号，正在停止调度器...")
        running = False

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    while running:
        # 计算下次运行时间
        wait_seconds = _seconds_until_next_run(hour, minute)
        next_run = _now_beijing() + timedelta(seconds=wait_seconds)

        logger.info(
            f"下次运行: {next_run.strftime('%Y-%m-%d %H:%M:%S')} 北京时间 "
            f"（{wait_seconds / 3600:.1f} 小时后）"
        )

        # 等待，每60秒检查一次退出信号
        while wait_seconds > 0 and running:
            sleep_time = min(60, wait_seconds)
            time.sleep(sleep_time)
            wait_seconds -= sleep_time

        if not running:
            break

        # 执行任务
        now_str = _now_beijing().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("=" * 60)
        logger.info(f"定时任务开始执行 - {now_str} 北京时间")
        logger.info("=" * 60)

        try:
            task()
            logger.info("定时任务执行完成")
        except Exception as e:
            logger.error(f"定时任务执行失败: {e}", exc_info=True)

        if on_complete:
            try:
                on_complete()
            except Exception as e:
                logger.error(f"回调执行失败: {e}", exc_info=True)

    logger.info("调度器已停止")
