param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
$dataRoot = 'D:\AppData\XianyuWishlist'
$privatePath = Join-Path $dataRoot 'private'
$pythonPath = Join-Path $dataRoot 'venv\Scripts\python.exe'
$url = 'http://127.0.0.1:8766'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '缺少运行环境，请按 README 安装依赖。' }
New-Item -ItemType Directory -Path $privatePath -Force | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $privatePath /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE) { throw '无法保护本地数据目录，已停止启动。' }
$health = $null
try { $health = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 2 } catch {}
if ($health -and $health.service -ne 'XianyuWishlist') { throw '8766 端口被其他服务占用。' }
if (-not $health) {
    $listener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw '8766 端口被其他服务占用。' }
    $env:WISHLIST_DATA = $privatePath
    $env:PYTHONUTF8 = '1'
    $serviceProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','uvicorn','wishlist.app:create_app','--factory','--host','127.0.0.1','--port','8766','--no-access-log') -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $privatePath 'server.log') -RedirectStandardError (Join-Path $privatePath 'server-error.log') -PassThru
    $serviceProcess.Id | Set-Content -LiteralPath (Join-Path $privatePath 'server.pid')
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 250
        try { $health = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 2; break } catch {}
    }
    if (-not $health -or $health.service -ne 'XianyuWishlist') { throw '启动失败，请查看本机 server-error.log。' }
}
Write-Host ("清单已运行：{0}  PID={1}" -f $url,$health.pid)
if (-not $NoBrowser) { Start-Process -FilePath 'chrome.exe' -ArgumentList $url }
