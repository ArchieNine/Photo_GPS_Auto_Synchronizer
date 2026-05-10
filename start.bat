@echo off
:: 設定終端機為 UTF-8 編碼
chcp 65001 >nul

echo ========================================
echo    大規模照片 GPS 自動補齊系統
echo ========================================
echo.

:: 1. 檢查系統是否有安裝 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [錯誤] 找不到 Python！
    echo 請前往 https://www.python.org/ 下載並安裝 Python [建議 3.9 以上版本]。
    echo 安裝時請務必勾選 Add Python to PATH 
    echo.
    pause
    exit /b
)

:: 2. 檢查虛擬環境是否存在，若沒有則自動建立
if not exist "venv\Scripts\activate.bat" (
    echo 偵測到首次執行，正在為您建立獨立的 Python 虛擬環境 [venv]...
    python -m venv venv
    echo 虛擬環境建立完成！
)

:: 3. 啟動虛擬環境
call venv\Scripts\activate.bat

:: 4. 安裝/更新依賴套件
echo 正在檢查並安裝必要套件 [這可能需要一點時間]...
python -m pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt

:: 5. 執行主程式
echo.
echo 啟動系統...
python main.py

echo.
pause