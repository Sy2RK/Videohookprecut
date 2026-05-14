#!/bin/bash
# Videoprecut 每日定时运行脚本
# 由 launchd 在每天北京时间 12:00 调用

# 项目目录
PROJECT_DIR="/Users/sheny2/Workspace/Videoprecut"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python"
LOG_DIR="${PROJECT_DIR}/logs"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

# 日志文件（按日期命名）
LOG_FILE="${LOG_DIR}/daily_$(date +%Y%m%d).log"

echo "========================================" >> "$LOG_FILE"
echo "Videoprecut 每日定时任务启动: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

# 加载 .env 环境变量
set -a
source "${PROJECT_DIR}/.env" 2>/dev/null
set +a

# ── 步骤1: 从飞书多维表格下载新视频 ──
cd "$PROJECT_DIR"
echo "" >> "$LOG_FILE"
echo "── 步骤1: 从飞书下载新视频 ──" >> "$LOG_FILE"
"$VENV_PYTHON" -m src.bitable_import >> "$LOG_FILE" 2>&1
IMPORT_EXIT=$?
echo "飞书导入退出码: $IMPORT_EXIT" >> "$LOG_FILE"

# ── 步骤2: 处理视频并上传云盘 ──
echo "" >> "$LOG_FILE"
echo "── 步骤2: 处理视频并上传云盘 ──" >> "$LOG_FILE"
"$VENV_PYTHON" -m src.main --gdrive >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
echo "" >> "$LOG_FILE"
echo "任务结束: $(date '+%Y-%m-%d %H:%M:%S'), 退出码: $EXIT_CODE" >> "$LOG_FILE"

exit $EXIT_CODE
