@echo off
:: 设置编码为 UTF-8，防止中文路径或输出乱码
chcp 65001 >nul
 

echo 正在运行脚本...
python main.py

echo.
echo 脚本运行结束。
pause


 
