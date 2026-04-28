"""多进程并行处理模块 - V2

管理多进程并行处理视频，每个工作进程独立处理一个完整视频。
V2工作流：AI结构分析 → Hook/Gameplay分段 → 剪切导出 → Hook描述 → JSON元数据
"""

import logging
import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from functools import partial
from typing import List, Optional

from .config import Config

logger = logging.getLogger("videoprecut.parallel")


@dataclass
class ProcessResult:
    """单个视频处理结果"""

    video_path: str = ""  # 视频路径
    success: bool = False  # 是否成功
    discarded: bool = False  # 是否被丢弃（无Hook）
    discard_reason: str = ""  # 丢弃原因
    hook_path: str = ""  # Hook视频输出路径
    gameplay_path: str = ""  # Gameplay视频输出路径
    metadata_path: str = ""  # JSON元数据路径
    hook_description: str = ""  # Hook一句话描述
    hook_emotion: str = ""  # Hook传达的情感
    hook_transition: str = ""  # Hook如何引入Gameplay
    error: str = ""  # 错误信息
    duration_sec: float = 0.0  # 处理耗时（秒）


def worker(
    video_path: str,
    config_dict: dict,
    result_queue: mp.Queue,
    progress_counter: mp.Value,
    gpu_id: int,
) -> None:
    """单个工作进程：处理一个完整视频（V2流程）

    复用 main.process_video() 核心逻辑，避免代码重复。
    每个进程独立加载模型，独立执行完整的处理流程。

    Args:
        video_path: 视频文件路径
        config_dict: 配置字典（序列化传递）
        result_queue: 结果收集队列
        progress_counter: 进度计数器（共享内存）
        gpu_id: 分配的 GPU ID
    """
    # 在子进程中重新配置日志
    from .utils import setup_logging
    setup_logging()

    config = Config.from_dict(config_dict)

    start_time = time.time()
    result = ProcessResult(video_path=video_path)

    try:
        # 延迟导入
        from .ingestion import VideoInfo
        from .main import process_video
        from .utils import get_video_info

        # 获取视频信息
        video_info = get_video_info(video_path)
        if video_info is None:
            raise RuntimeError(f"无法获取视频信息: {video_path}")

        filename = os.path.basename(video_path)
        stem = os.path.splitext(filename)[0]
        ext = os.path.splitext(filename)[1].lower()

        video = VideoInfo(
            filepath=video_path,
            filename=filename,
            stem=stem,
            extension=ext,
            is_mp4=(ext == ".mp4"),
            duration=video_info["duration"],
            fps=video_info["fps"],
            width=video_info["width"],
            height=video_info["height"],
            has_video=video_info["has_video"],
            has_audio=video_info["has_audio"],
            video_codec=video_info["video_codec"],
            audio_codec=video_info["audio_codec"],
        )

        # 调用共享的处理逻辑
        proc_result = process_video(video, config)

        # 将 dict 结果转换为 ProcessResult
        result.success = proc_result.get("success", False)
        result.discarded = proc_result.get("discarded", False)
        result.discard_reason = proc_result.get("discard_reason", "")
        result.hook_path = proc_result.get("hook_path", "")
        result.gameplay_path = proc_result.get("gameplay_path", "")
        result.metadata_path = proc_result.get("metadata_path", "")
        result.hook_description = proc_result.get("hook_description", "")
        result.hook_emotion = proc_result.get("hook_emotion", "")
        result.hook_transition = proc_result.get("hook_transition", "")
        result.error = proc_result.get("error", "")

    except Exception as e:
        result.error = str(e)
        result.success = False
        logger.error(f"处理失败: {video_path} - {e}", exc_info=True)

    finally:
        result.duration_sec = time.time() - start_time
        result_queue.put(result)

        with progress_counter.get_lock():
            progress_counter.value += 1
            current = progress_counter.value
        logger.info(f"进度: {current} 个视频已完成")


def run_parallel(
    video_paths: List[str],
    config: Config,
) -> List[ProcessResult]:
    """并行处理多个视频

    Args:
        video_paths: 视频文件路径列表
        config: 全局配置

    Returns:
        处理结果列表
    """
    n_videos = len(video_paths)
    n_workers = min(config.max_workers, n_videos)

    logger.info(
        f"启动并行处理: {n_videos} 个视频, {n_workers} 个工作进程"
    )

    # GPU 分配：轮询分配
    gpu_assignments = [
        config.gpu_ids[i % len(config.gpu_ids)]
        for i in range(n_videos)
    ]

    # 创建共享对象
    result_queue = mp.Queue()
    progress_counter = mp.Value("i", 0)

    # 构造工作函数
    worker_func = partial(
        worker,
        config_dict=config.to_dict(),
        result_queue=result_queue,
        progress_counter=progress_counter,
    )

    # 使用进程池
    try:
        with mp.Pool(processes=n_workers) as pool:
            pool.starmap(
                worker_func,
                [(vp, gpu_assignments[i]) for i, vp in enumerate(video_paths)],
            )
            pool.close()
            pool.join()
    except Exception as e:
        logger.error(f"进程池异常: {e}")
        raise

    # 收集所有结果
    results = []
    while not result_queue.empty():
        results.append(result_queue.get_nowait())

    return results


def summarize_results(results: List[ProcessResult]) -> dict:
    """汇总处理结果

    Args:
        results: 处理结果列表

    Returns:
        汇总统计字典
    """
    total = len(results)
    success = sum(1 for r in results if r.success)
    failed = total - success
    discarded = sum(1 for r in results if r.discarded)
    discarded_no_hook = sum(1 for r in results if r.discarded and r.discard_reason == "no_hook")
    discarded_no_trademark = sum(1 for r in results if r.discarded and r.discard_reason == "no_trademark")
    total_time = sum(r.duration_sec for r in results)

    processed = sum(1 for r in results if r.success and not r.discarded)
    has_hook_desc = sum(1 for r in results if r.hook_description)
    has_hook_emotion = sum(1 for r in results if r.hook_emotion)
    has_hook_transition = sum(1 for r in results if r.hook_transition)

    summary = {
        "total_videos": total,
        "success": success,
        "failed": failed,
        "discarded": discarded,
        "discarded_no_hook": discarded_no_hook,
        "discarded_no_trademark": discarded_no_trademark,
        "processed": processed,
        "has_hook_description": has_hook_desc,
        "has_hook_emotion": has_hook_emotion,
        "has_hook_transition": has_hook_transition,
        "total_processing_time": total_time,
        "avg_time_per_video": total_time / max(total, 1),
    }

    logger.info("=" * 60)
    logger.info("处理结果汇总")
    logger.info("=" * 60)
    logger.info(f"  总视频数: {total}")
    logger.info(f"  成功处理: {success}")
    logger.info(f"  丢弃(无Hook): {discarded_no_hook}")
    logger.info(f"  丢弃(无商标): {discarded_no_trademark}")
    logger.info(f"  成功产出: {processed}")
    logger.info(f"  有Hook描述: {has_hook_desc}")
    logger.info(f"  有Hook情感: {has_hook_emotion}")
    logger.info(f"  有Hook过渡: {has_hook_transition}")
    logger.info(f"  失败: {failed}")
    logger.info(f"  总处理时间: {total_time:.1f}s")
    logger.info(f"  平均每视频: {total_time / max(total, 1):.1f}s")

    if failed > 0:
        logger.info("")
        logger.info("失败视频:")
        for r in results:
            if not r.success:
                logger.info(f"  {r.video_path}: {r.error}")

    if discarded > 0:
        logger.info("")
        logger.info("丢弃视频:")
        for r in results:
            if r.discarded:
                logger.info(f"  {r.video_path}")

    logger.info("=" * 60)

    return summary