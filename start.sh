#!/bin/bash

echo "========================================"
echo "  🌍 大規模照片 GPS 自動補齊系統"
echo "========================================"
echo ""

# 1. 檢查系統是否有安裝 Python3
if ! command -v python3 &> /dev/null; then
    echo "❌ 錯誤：找不到 Python3！請先安裝 Python3 (建議 3.9+)。"
    exit 1
fi

# 2. 檢查虛擬環境是否存在，若沒有則自動建立
if [ ! -d "venv" ]; then
    echo "⏳ 偵測到首次執行，正在為您建立獨立的 Python 虛擬環境 (venv)..."
    python3 -m venv venv
    echo "✅ 虛擬環境建立完成！"
fi

# 3. 啟動虛擬環境
source venv/bin/activate

# 4. 安裝/更新依賴套件
echo "📦 正在檢查並安裝必要套件 (這可能需要一點時間)..."
pip install --upgrade pip >/dev/null 2>&1
pip install -r requirements.txt

# 5. 執行主程式
echo ""
echo "🚀 啟動系統..."
python3 main.py