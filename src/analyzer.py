"""多模态AI分析器模块

封装多模态AI模型调用，提供：
1. 视频结构分析（检测Hook/Gameplay/商标三段式结构）
2. Hook视觉元素描述（一句话总结）

支持可插拔的AI提供商：DashScope(Qwen) / OpenAI GPT-4o / Anthropic Claude Vision / 本地模型

视频输入模式：
- DashScope(Qwen): 原生视频输入，直接传视频文件，帧级精度
- 其他提供商: 回退到帧采样+图片输入模式
"""

import base64
import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .config import Config
from .utils import image_to_base64

logger = logging.getLogger("videoprecut.analyzer")


# DashScope 视频直传大小限制（100MB）
MAX_VIDEO_SIZE_MB = 100


def video_to_base64(video_path: str, max_size_mb: float = MAX_VIDEO_SIZE_MB) -> str:
    """将视频文件编码为 base64 字符串

    Args:
        video_path: 视频文件路径
        max_size_mb: 最大允许文件大小（MB），超过则抛出 ValueError

    Returns:
        base64 编码字符串

    Raises:
        ValueError: 文件大小超过限制
    """
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if file_size_mb > max_size_mb:
        raise ValueError(
            f"视频文件过大: {file_size_mb:.1f}MB > {max_size_mb:.0f}MB限制，"
            f"请压缩视频或使用帧采样模式"
        )
    with open(video_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ── 数据结构 ──

@dataclass
class VideoStructure:
    """视频结构分析结果"""
    has_hook: bool = False  # 是否存在Hook
    hook_end_seconds: float = 0.0  # Hook结束时间（秒）
    has_trademark: bool = False  # 是否存在商标/结束画面
    trademark_start_seconds: float = 0.0  # 商标开始时间（秒）
    confidence: float = 0.0  # 分析置信度
    raw_response: str = ""  # AI原始返回文本


@dataclass
class HookDescription:
    """Hook元素描述结果"""
    description: str = ""  # 一句话描述
    emotion: str = ""  # Hook传达的情感
    transition: str = ""  # Hook如何引入Gameplay
    raw_response: str = ""  # AI原始返回文本


# ── 重试工具 ──

def _retry_api_call(func, max_retries: int = 3, base_delay: float = 2.0):
    """带指数退避的API调用重试

    Args:
        func: 无参可调用对象
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒），每次重试翻倍

    Returns:
        函数返回值

    Raises:
        最后一次重试的异常
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"API调用失败(第{attempt + 1}次)，{delay:.0f}s后重试: {e}"
                )
                time.sleep(delay)
            else:
                logger.error(f"API调用失败(已重试{max_retries}次): {e}")
    raise last_error


# ── AI提供商抽象 ──

class AIProvider(ABC):
    """AI提供商抽象基类"""

    @abstractmethod
    def analyze_images(
        self,
        images: List,  # List[PIL.Image.Image]
        prompt: str,
    ) -> str:
        """分析图片并返回文本响应"""
        ...

    def analyze_video(
        self,
        video_path: str,
        prompt: str,
    ) -> str:
        """分析视频并返回文本响应

        默认实现：不支持视频直传，抛出 NotImplementedError。
        支持 video_url 输入的提供商（如 DashScope/Qwen）应重写此方法。

        Args:
            video_path: 视频文件路径
            prompt: 分析提示词

        Returns:
            AI 文本响应

        Raises:
            NotImplementedError: 提供商不支持视频直传
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 不支持视频直传，请使用帧采样模式"
        )

    @property
    def supports_video(self) -> bool:
        """是否支持视频直传"""
        return False


class OpenAIProvider(AIProvider):
    """OpenAI GPT-4o 提供商"""

    def __init__(self, config: Config):
        self.api_key = config.ai_api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = config.ai_model
        self.base_url = config.ai_base_url or None
        self.max_tokens = config.ai_max_tokens
        self.temperature = config.ai_temperature
        # 延迟初始化client
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError("请安装 openai: pip install openai")
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def analyze_images(self, images: List, prompt: str) -> str:
        """调用 OpenAI Vision API"""

        # 构建消息内容
        content = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = image_to_base64(img, format="JPEG", quality=85)
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                    "detail": "low",  # 使用低分辨率以节省token
                },
            })

        def _call():
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            return response.choices[0].message.content

        return _retry_api_call(_call)


class AnthropicProvider(AIProvider):
    """Anthropic Claude Vision 提供商"""

    def __init__(self, config: Config):
        self.api_key = config.ai_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = config.ai_model
        self.max_tokens = config.ai_max_tokens
        self.temperature = config.ai_temperature
        self._client = None

    @property
    def client(self):
        """延迟初始化 Anthropic 客户端"""
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError("请安装 anthropic: pip install anthropic")
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def analyze_images(self, images: List, prompt: str) -> str:
        """调用 Anthropic Vision API"""
        # 构建消息内容
        content = []
        for img in images:
            b64 = image_to_base64(img, format="JPEG", quality=85)
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": b64,
                },
            })
        content.append({"type": "text", "text": prompt})

        def _call():
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[{"role": "user", "content": content}],
            )
            return response.content[0].text

        return _retry_api_call(_call)


class DashScopeProvider(AIProvider):
    """阿里 DashScope 提供商（Qwen 系列模型）

    使用 DashScope 的 OpenAI 兼容接口调用 Qwen-VL 等多模态模型。
    支持 Qwen 原生视频输入，直接传视频文件获得帧级精度分析。
    文档: https://help.aliyun.com/zh/model-studio/developer-reference/use-qwen-by-calling-api
    """

    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, config: Config):
        self.api_key = config.ai_api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.model = config.ai_model or "qwen-plus-latest"
        self.max_tokens = config.ai_max_tokens
        self.temperature = config.ai_temperature
        self._client = None

    @property
    def client(self):
        """延迟初始化 DashScope OpenAI 兼容客户端"""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError("请安装 openai: pip install openai")
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.DASHSCOPE_BASE_URL,
            )
        return self._client

    @property
    def supports_video(self) -> bool:
        """Qwen 系列模型支持原生视频输入"""
        return True

    def analyze_video(
        self,
        video_path: str,
        prompt: str,
    ) -> str:
        """调用 DashScope 视频分析接口（Qwen 原生视频输入）

        将视频文件 base64 编码后通过 video_url 类型传入，
        Qwen 模型会自动提取视频帧进行分析，精度远高于截图采样。

        Args:
            video_path: 视频文件路径
            prompt: 分析提示词

        Returns:
            AI 文本响应
        """
        # 视频文件 base64 编码
        logger.info(f"编码视频文件: {os.path.basename(video_path)}")
        video_b64 = video_to_base64(video_path)
        video_size_mb = len(video_b64) * 3 // 4 / (1024 * 1024)  # 近似原始大小
        logger.info(f"视频编码完成: {video_size_mb:.1f}MB")

        # 根据文件扩展名确定 MIME 类型
        ext = os.path.splitext(video_path)[1].lower()
        mime_map = {
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".avi": "video/x-msvideo",
            ".mov": "video/quicktime",
            ".mkv": "video/x-matroska",
        }
        mime_type = mime_map.get(ext, "video/mp4")

        # 构建消息内容：文本 + 视频
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "video_url",
                "video_url": {
                    "url": f"data:{mime_type};base64,{video_b64}",
                },
            },
        ]

        def _call():
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            return response.choices[0].message.content

        return _retry_api_call(_call)

    def analyze_images(self, images: List, prompt: str) -> str:
        """调用 DashScope OpenAI 兼容接口（图片模式，回退方案）"""
        # 构建消息内容
        content = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = image_to_base64(img, format="JPEG", quality=85)
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                },
            })

        def _call():
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            return response.choices[0].message.content

        return _retry_api_call(_call)


class LocalProvider(AIProvider):
    """本地模型提供商（Qwen-VL / LLaVA 等）

    使用 OpenAI 兼容接口，需自行部署模型服务。
    """

    def __init__(self, config: Config):
        self.base_url = config.ai_base_url or "http://localhost:8000/v1"
        self.model = config.ai_model
        self.api_key = config.ai_api_key or "dummy"
        self.max_tokens = config.ai_max_tokens
        self.temperature = config.ai_temperature
        self._client = None

    @property
    def client(self):
        """延迟初始化本地模型 OpenAI 兼容客户端"""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise ImportError("请安装 openai: pip install openai")
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def analyze_images(self, images: List, prompt: str) -> str:
        """调用本地模型 OpenAI 兼容接口"""
        content = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = image_to_base64(img, format="JPEG", quality=85)
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                },
            })

        def _call():
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            return response.choices[0].message.content

        return _retry_api_call(_call)


# ── 提供商工厂 ──

def create_provider(config: Config) -> AIProvider:
    """根据配置创建AI提供商"""
    providers = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "dashscope": DashScopeProvider,
        "local": LocalProvider,
    }

    provider_name = config.ai_provider.lower()
    if provider_name not in providers:
        raise ValueError(
            f"不支持的AI提供商: {provider_name}，"
            f"可选: {list(providers.keys())}"
        )

    return providers[provider_name](config)


# ── 核心分析器 ──

class MultimodalAnalyzer:
    """多模态AI分析器

    提供视频结构分析和Hook元素描述功能。
    优先使用视频直传模式（Qwen原生视频输入），不支持时回退到帧采样模式。
    """

    def __init__(self, config: Config):
        self.config = config
        self._provider: Optional[AIProvider] = None

    @property
    def provider(self) -> AIProvider:
        """延迟初始化AI提供商"""
        if self._provider is None:
            self._provider = create_provider(self.config)
        return self._provider

    def analyze_video_structure_from_file(
        self,
        video_path: str,
        video_duration: float,
    ) -> VideoStructure:
        """分析视频结构（视频直传模式，优先使用）

        当AI提供商支持视频直传时（如 DashScope/Qwen），直接将视频文件
        传给模型，由模型自动提取帧进行分析，精度远高于截图采样。

        Args:
            video_path: 视频文件路径
            video_duration: 视频总时长（秒）

        Returns:
            VideoStructure 视频结构分析结果
        """
        if self.provider.supports_video:
            logger.info(f"使用视频直传模式分析: {os.path.basename(video_path)}")
            return self._analyze_structure_via_video(video_path, video_duration)
        else:
            logger.info("提供商不支持视频直传，请使用帧采样模式 analyze_video_structure()")
            return VideoStructure(
                has_hook=False,
                confidence=0.0,
                raw_response="提供商不支持视频直传，需使用帧采样模式",
            )

    def analyze_video_structure(
        self,
        frames: List[Tuple[float, "PIL.Image.Image"]],
        video_duration: float,
    ) -> VideoStructure:
        """分析视频结构（帧采样模式，回退方案）

        当AI提供商不支持视频直传时使用此方法，通过采样帧+图片输入分析。

        Args:
            frames: [(时间戳, PIL图像), ...] 采样帧列表
            video_duration: 视频总时长（秒）

        Returns:
            VideoStructure 视频结构分析结果
        """
        # 构建帧描述信息
        frame_info = "\n".join(
            f"第{i+1}帧 (时间: {t:.1f}秒)"
            for i, (t, _) in enumerate(frames)
        )

        prompt = f"""你是一个广告视频分析专家。请分析以下视频帧（按时间顺序排列），判断视频的结构。

视频总时长: {video_duration:.1f}秒
采样帧信息:
{frame_info}

请判断：
1. 视频开头是否存在"Hook"片段？
   - Hook是指纯图像、无游戏玩法展示、用于吸引点击的开头片段，通常几秒钟
   - 特征：静态画面、动画特效、文字提示、角色展示等，没有实际的游戏操作展示
   - **关键原则：Hook判定宁缺毋滥！** 只有当你完全确定开头片段不含任何游戏玩法内容时才判定为Hook。如果开头片段中已经出现了任何游戏画面或操作演示，则不应视为Hook
   - Hook结束时间点必须精确：宁可把Hook结束时间标早一点，也绝不能让Hook片段中包含任何游戏玩法画面

2. 如果存在Hook，Hook在哪个时间点结束？（即游戏玩法开始的时间点。注意：宁可标早，不可标晚）
3. 视频结尾是否存在商标/logo/"Try Now"/下载按钮等结束画面？
   - **关键原则：商标判定宁缺毋滥！** 只有当你明确看到品牌logo、"Try Now"、下载按钮、应用商店截图等明确的结束引导画面时才判定为有商标。视频最后几秒的游戏画面淡出或自然结束不算商标
   - 如果存在商标，从哪个时间点开始？

请严格按以下JSON格式返回，不要包含其他内容：
{{
    "has_hook": true或false,
    "hook_end_seconds": Hook结束的秒数（无Hook则为0。注意：宁可标早，不可标晚）,
    "has_trademark": true或false,
    "trademark_start_seconds": 商标画面开始的秒数（无商标则为视频总时长）,
    "confidence": 0.0到1.0的置信度
}}"""

        images = [img for _, img in frames]

        try:
            response_text = self.provider.analyze_images(images, prompt)
            logger.debug(f"AI结构分析原始返回: {response_text}")

            # 解析JSON响应
            structure = self._parse_structure_response(response_text, video_duration)
            return structure

        except Exception as e:
            logger.error(f"AI视频结构分析失败: {e}")
            return VideoStructure(
                has_hook=False,
                confidence=0.0,
                raw_response=f"Error: {e}",
            )

    def _analyze_structure_via_video(
        self,
        video_path: str,
        video_duration: float,
    ) -> VideoStructure:
        """通过视频直传分析视频结构（内部方法）

        Args:
            video_path: 视频文件路径
            video_duration: 视频总时长（秒）

        Returns:
            VideoStructure 视频结构分析结果
        """
        prompt = f"""你是一个广告视频分析专家。请分析这个广告视频的完整结构。

视频总时长: {video_duration:.1f}秒

请仔细观看视频的每一帧，判断：
1. 视频开头是否存在"Hook"片段？
   - Hook是指纯图像、无游戏玩法展示、用于吸引点击的开头片段，通常几秒钟
   - 特征：静态画面、动画特效、文字提示、角色展示等，没有实际的游戏操作展示
   - **关键原则：Hook判定宁缺毋滥！** 只有当你完全确定开头片段不含任何游戏玩法内容时才判定为Hook。如果开头片段中已经出现了任何游戏画面或操作演示，则不应视为Hook
   - Hook结束时间点必须精确：宁可把Hook结束时间标早一点，也绝不能让Hook片段中包含任何游戏玩法画面

2. 如果存在Hook，Hook在哪个精确时间点结束？（即游戏玩法开始的时间点，请精确到0.1秒。注意：这个时间点应该是最后一个纯Hook画面的结束时刻，而不是游戏画面刚开始出现的时刻）
3. 视频结尾是否存在商标/logo/"Try Now"/下载按钮等结束画面？
   - **关键原则：商标判定宁缺毋滥！** 只有当你明确看到品牌logo、"Try Now"、下载按钮、应用商店截图等明确的结束引导画面时才判定为有商标
   - 以下情况不算商标：游戏画面自然结束、画面淡出黑屏、最后几秒仍是游戏玩法、简单的文字叠加
   - 真正的商标/结束画面特征：明确的品牌标识、下载引导按钮、应用商店评分/截图、公司logo动画等
   - 如果不确定是否存在商标，请设has_trademark为false
   - 如果存在商标，从哪个精确时间点开始？（请精确到0.1秒）

请严格按以下JSON格式返回，不要包含其他内容：
{{
    "has_hook": true或false,
    "hook_end_seconds": Hook结束的秒数（无Hook则为0，精确到0.1秒。注意：宁可标早，不可标晚）,
    "has_trademark": true或false,
    "trademark_start_seconds": 商标画面开始的秒数（无商标则为视频总时长，精确到0.1秒）,
    "confidence": 0.0到1.0的置信度
}}"""

        try:
            response_text = self.provider.analyze_video(video_path, prompt)
            logger.debug(f"AI视频直传结构分析原始返回: {response_text}")

            # 解析JSON响应
            structure = self._parse_structure_response(response_text, video_duration)
            return structure

        except Exception as e:
            logger.error(f"AI视频直传结构分析失败: {e}")
            # 回退到帧采样模式
            logger.info("视频直传失败，尝试回退到帧采样模式")
            return VideoStructure(
                has_hook=False,
                confidence=0.0,
                raw_response=f"Error: {e}",
            )

    def describe_hook_from_file(
        self,
        video_path: str,
        hook_start: float = 0.0,
        hook_end: float = 0.0,
    ) -> HookDescription:
        """分析Hook视觉元素（视频直传模式）

        当AI提供商支持视频直传时，直接传视频文件分析Hook片段。

        Args:
            video_path: 视频文件路径
            hook_start: Hook起始时间（秒）
            hook_end: Hook结束时间（秒）

        Returns:
            HookDescription Hook描述结果
        """
        if not self.provider.supports_video:
            return HookDescription(description="提供商不支持视频直传")

        prompt = f"""请分析这个广告视频开头Hook片段（0秒到{hook_end:.1f}秒），从以下三个维度进行描述：

1. 视觉描述：用一句话简洁描述Hook片段的核心视觉内容和吸引点（角色、场景、特效、文字等）
2. 情感传达：Hook片段向观众传达了什么情感或情绪？（如好奇、惊喜、紧张、幽默、震撼等）
3. 过渡方式：Hook是如何引入后续游戏玩法部分的？（如画面直接切换、角色进入游戏、文字引导、特效过渡等）

请严格按以下JSON格式返回，不要包含其他内容：
{{
    "description": "一句话视觉描述",
    "emotion": "Hook传达的情感",
    "transition": "Hook如何引入Gameplay的过渡方式"
}}"""

        try:
            response_text = self.provider.analyze_video(video_path, prompt)
            logger.debug(f"AI Hook分析(视频直传)原始返回: {response_text}")

            hook_desc = self._parse_hook_description(response_text)
            logger.info(f"Hook描述(视频直传): {hook_desc.description}")
            logger.info(f"Hook情感(视频直传): {hook_desc.emotion}")
            logger.info(f"Hook过渡(视频直传): {hook_desc.transition}")
            return hook_desc

        except Exception as e:
            logger.error(f"AI Hook描述(视频直传)失败: {e}")
            return HookDescription(
                description=f"分析失败: {e}",
                raw_response=f"Error: {e}",
            )

    def describe_hook(
        self,
        hook_frames: List[Tuple[float, "PIL.Image.Image"]],
    ) -> HookDescription:
        """分析Hook视觉元素（帧采样模式，回退方案）

        Args:
            hook_frames: Hook时间段的采样帧 [(时间戳, PIL图像), ...]

        Returns:
            HookDescription Hook描述结果
        """
        if not hook_frames:
            return HookDescription(description="无Hook帧可分析")

        prompt = """请分析这个广告Hook片段，从以下三个维度进行描述：

1. 视觉描述：用一句话简洁描述Hook片段的核心视觉内容和吸引点（角色、场景、特效、文字等）
2. 情感传达：Hook片段向观众传达了什么情感或情绪？（如好奇、惊喜、紧张、幽默、震撼等）
3. 过渡方式：Hook是如何引入后续游戏玩法部分的？（如画面直接切换、角色进入游戏、文字引导、特效过渡等）

请严格按以下JSON格式返回，不要包含其他内容：
{
    "description": "一句话视觉描述",
    "emotion": "Hook传达的情感",
    "transition": "Hook如何引入Gameplay的过渡方式"
}"""

        images = [img for _, img in hook_frames]

        try:
            response_text = self.provider.analyze_images(images, prompt)
            logger.debug(f"AI Hook分析(帧采样)原始返回: {response_text}")

            hook_desc = self._parse_hook_description(response_text)
            logger.info(f"Hook描述: {hook_desc.description}")
            logger.info(f"Hook情感: {hook_desc.emotion}")
            logger.info(f"Hook过渡: {hook_desc.transition}")
            return hook_desc

        except Exception as e:
            logger.error(f"AI Hook描述失败: {e}")
            return HookDescription(
                description=f"分析失败: {e}",
                raw_response=f"Error: {e}",
            )

    def _parse_hook_description(self, response_text: str) -> HookDescription:
        """解析AI返回的Hook描述JSON

        Args:
            response_text: AI返回的文本

        Returns:
            HookDescription 解析后的Hook描述结果
        """
        raw = response_text.strip()

        # 策略1: 尝试直接解析整个响应为JSON
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return HookDescription(
                    description=data.get("description", "").strip(),
                    emotion=data.get("emotion", "").strip(),
                    transition=data.get("transition", "").strip(),
                    raw_response=raw,
                )
        except json.JSONDecodeError:
            pass

        # 策略2: 提取markdown代码块中的JSON
        code_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
        if code_match:
            try:
                data = json.loads(code_match.group(1).strip())
                if isinstance(data, dict):
                    return HookDescription(
                        description=data.get("description", "").strip(),
                        emotion=data.get("emotion", "").strip(),
                        transition=data.get("transition", "").strip(),
                        raw_response=raw,
                    )
            except json.JSONDecodeError:
                pass

        # 策略3: 使用平衡括号提取嵌套JSON（支持嵌套大括号）
        json_str = self._extract_balanced_json(raw)
        if json_str:
            try:
                data = json.loads(json_str)
                if isinstance(data, dict):
                    return HookDescription(
                        description=data.get("description", "").strip(),
                        emotion=data.get("emotion", "").strip(),
                        transition=data.get("transition", "").strip(),
                        raw_response=raw,
                    )
            except json.JSONDecodeError:
                pass

        # 所有策略均失败，将整个响应作为description
        logger.warning(f"Hook描述JSON解析失败，使用原始文本作为描述")
        description = raw.strip().strip('"').strip("'").strip()
        return HookDescription(
            description=description,
            raw_response=raw,
        )

    @staticmethod
    def _extract_balanced_json(text: str) -> Optional[str]:
        """从文本中提取平衡的JSON对象（支持嵌套大括号）

        Args:
            text: 可能包含JSON的文本

        Returns:
            提取到的JSON字符串，未找到返回None
        """
        start = text.find('{')
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            c = text[i]

            if escape:
                escape = False
                continue

            if c == '\\' and in_string:
                escape = True
                continue

            if c == '"' and not escape:
                in_string = not in_string
                continue

            if in_string:
                continue

            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return None

    def _parse_structure_response(
        self,
        response_text: str,
        video_duration: float,
    ) -> VideoStructure:
        """解析AI返回的视频结构JSON

        Args:
            response_text: AI返回的文本
            video_duration: 视频总时长

        Returns:
            VideoStructure
        """
        # 尝试从文本中提取JSON
        json_str = response_text

        # 处理markdown代码块包裹
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        json_str = json_str.strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning(f"AI返回非JSON格式: {response_text[:200]}")
            return VideoStructure(
                has_hook=False,
                confidence=0.0,
                raw_response=response_text,
            )

        has_hook = bool(data.get("has_hook", False))
        hook_end = float(data.get("hook_end_seconds", 0))
        has_trademark = bool(data.get("has_trademark", False))
        trademark_start = float(data.get("trademark_start_seconds", video_duration))
        confidence = float(data.get("confidence", 0.5))

        # 校验时间戳合理性
        if has_hook:
            hook_end = max(0.0, min(hook_end, video_duration))
            # Hook不应超过最大时长
            if hook_end > self.config.hook_max_duration:
                logger.warning(
                    f"Hook时长 {hook_end:.1f}s 超过最大限制 "
                    f"{self.config.hook_max_duration:.1f}s，可能判断有误"
                )
                confidence *= 0.7  # 降低置信度

        trademark_start = max(0.0, min(trademark_start, video_duration))

        # 校验逻辑：hook_end < trademark_start
        if has_hook and hook_end >= trademark_start:
            logger.warning(
                f"Hook结束时间({hook_end:.1f}s) >= "
                f"商标开始时间({trademark_start:.1f}s)，逻辑异常"
            )
            confidence *= 0.5

        # 如果AI明确说无商标，则将trademark_start设为视频总时长
        if not has_trademark:
            trademark_start = video_duration

        structure = VideoStructure(
            has_hook=has_hook,
            hook_end_seconds=hook_end,
            has_trademark=has_trademark,
            trademark_start_seconds=trademark_start,
            confidence=confidence,
            raw_response=response_text,
        )

        logger.info(
            f"视频结构分析: has_hook={has_hook}, "
            f"hook_end={hook_end:.1f}s, "
            f"has_trademark={has_trademark}, "
            f"trademark_start={trademark_start:.1f}s, "
            f"confidence={confidence:.2f}"
        )

        return structure