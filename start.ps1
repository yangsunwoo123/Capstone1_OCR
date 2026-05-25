# OCR 서버 시작 스크립트
# 사용법: .\start.ps1

# .env 파일에서 환경변수 로드 (있을 경우)
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]+)=(.+)$") {
            $key   = $matches[1].Trim()
            $value = $matches[2].Trim()
            [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
            Write-Host "  ENV: $key 설정됨"
        }
    }
}

$env:PYTHONUTF8 = "1"

Write-Host ""
Write-Host "==================================="
Write-Host "  화성시 OCR 서버 시작"
Write-Host "==================================="
Write-Host "  URL: http://127.0.0.1:8000"
Write-Host "  종료: Ctrl+C"
Write-Host "==================================="
Write-Host ""

python main.py seed-forms 2>$null
python main.py serve --host 0.0.0.0 --port 8000
