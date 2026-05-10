@echo off
chcp 65001 >nul
echo ========================================
echo    Setting default language to: [English (en)]
echo ========================================

:: 使用 .NET 類別確保絕對正確的 UTF-8 讀寫（無 BOM）
powershell -Command "$path = 'main.py'; $content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8); $content = $content -replace '--lang\", default=\"(en|zh)\"', '--lang\", default=\"en\"'; [System.IO.File]::WriteAllText($path, $content, (New-Object System.Text.UTF8Encoding($false)))"

echo.
echo ✅ Done! Default language is now set to English.
echo.
pause