param([int]$Port = 8765, [string]$Planner = 'parking_hu.reference_planner:Planner', [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$parkingPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $parkingPython)) {
    $localConfig = Join-Path $PSScriptRoot '.local-python.txt'
    if (Test-Path -LiteralPath $localConfig) { $parkingPython = (Get-Content -LiteralPath $localConfig -Raw).Trim() }
}
if (-not (Test-Path -LiteralPath $parkingPython)) {
    Write-Host '请先安装 Python 3.12，再运行 setup.ps1 创建环境。' -ForegroundColor Yellow
    exit 1
}
$parkingArgs = @('-X', 'utf8', '-m', 'parking_hu', '--port', "$Port", '--planner', $Planner)
if ($NoBrowser) { $parkingArgs += '--no-browser' }
& $parkingPython @parkingArgs
exit $LASTEXITCODE
