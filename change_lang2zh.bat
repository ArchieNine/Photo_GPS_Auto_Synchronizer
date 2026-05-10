@echo off
chcp 65001 >nul
echo ========================================
echo    正在將預設語言修改為：[中文 (zh)]
echo ========================================

:: 使用 .NET 類別確保絕對正確的 UTF-8 讀寫（無 BOM）
powershell -Command "$path = 'main.py'; $content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8); $content = $content -replace '--lang\", default=\"(en|zh)\"', '--lang\", default=\"zh\"'; [System.IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding($false)))"

echo.
echo ✅ 修改完成！現在啟動程式將預設顯示中文。
echo.
pause