"""Videoprecut 安装配置"""

from setuptools import setup, find_packages

setup(
    name="videoprecut",
    version="0.1.0",
    description="竞品商标视频剪辑工作流",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "ultralytics>=8.0.0",
        "opencv-python>=4.8.0",
        "ffmpeg-python>=0.2.0",
        "Pillow>=10.0.0",
        "tqdm>=4.65.0",
        "pyyaml>=6.0",
        "psutil>=5.9.0",
    ],
    entry_points={
        "console_scripts": [
            "videoprecut=src.main:main",
        ],
    },
)