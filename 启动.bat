@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境，正在创建...
    python -m venv venv
    echo [提示] 正在安装依赖...
    venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    echo [完成] 依赖安装完毕
    pause
)

echo 正在启动外贸屏幕实时翻译助手 V1...
start "" "venv\Scripts\python.exe" main.py
