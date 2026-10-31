param([string]$Python = 'python', [switch]$Cuda)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& $Python -c "import sys; assert sys.version_info[:2] == (3,12), 'Python 3.12 required'"
if ($LASTEXITCODE -ne 0) { throw '请通过 -Python 参数指定 Python 3.12。' }
& $Python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败。' }
$parkingPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $parkingPython -m pip install --upgrade pip
$torchIndex = if ($Cuda) { 'https://download.pytorch.org/whl/cu124' } else { 'https://download.pytorch.org/whl/cpu' }
& $parkingPython -m pip install torch==2.5.1 torchvision==0.20.1 --index-url $torchIndex
if ($LASTEXITCODE -ne 0) { throw 'Torch 安装失败。' }
& $parkingPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '项目依赖安装失败。' }
Write-Host '安装完成。运行 start.ps1 或双击启动项目.cmd。'
