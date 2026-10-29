param([ValidateSet('Start','Stop','Data')][string]$Action='Start')
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'XiaoACore'))
$service = Join-Path $appRoot 'app\clawbot_service.py'
$data = Join-Path $appRoot 'data'
$python = Join-Path $appRoot 'runtime\Scripts\python.exe'
$env:XIAOA_DATA_DIR = $data
$env:XIAOA_AGY_EXE = Join-Path $env:LOCALAPPDATA 'agy\bin\agy.exe'
$env:AGY_CLI_DISABLE_AUTO_UPDATE = 'true'
function Get-OwnBot {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and
        $_.CommandLine.IndexOf('"' + $service + '"',[StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}
if ($Action -eq 'Data') { Start-Process explorer.exe -ArgumentList ('"' + $data + '"'); exit 0 }
$bots = @(Get-OwnBot)
if ($Action -eq 'Stop') {
    foreach ($bot in $bots) {
        # Stop only this installed entry point; never other Python/AGY sessions.
        Stop-Process -Id $bot.ProcessId -ErrorAction SilentlyContinue
    }
    exit 0
}
if ($bots.Count -gt 0) { exit 0 }
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath (Join-Path $data 'clawbot_account.json'))) {
    throw '请先运行“一键安装小A.cmd”完成安装和扫码。'
}
New-Item -ItemType Directory -Path $data -Force | Out-Null
$process = Start-Process -FilePath $python -ArgumentList ('"' + $service + '"') -WorkingDirectory $data -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
if ($process.HasExited) { throw '小A启动失败，请查看数据目录 logs 文件夹里的 clawbot_service.log。' }
