param([switch]$VerifyOnly)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$appRoot = Join-Path $env:LOCALAPPDATA 'XiaoACore'
$manifestPath = Join-Path $sourceRoot 'manifest.json'

function Assert-Payload {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.version -ne '1.1.0' -or @($manifest.files).Count -lt 10) { throw '安装清单无效。' }
    foreach ($entry in $manifest.files) {
        $path = [IO.Path]::GetFullPath((Join-Path $sourceRoot $entry.path))
        if (-not $path.StartsWith($sourceRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
            throw '安装清单包含目录外路径。'
        }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw ('缺少安装文件：' + $entry.path) }
        if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw ('安装文件校验失败：' + $entry.path)
        }
    }
    return $manifest
}

function Find-Python {
    $candidates = @()
    foreach ($name in @('python.exe','python3.exe')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { $candidates += $cmd.Source }
    }
    $candidates += @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python*\python.exe') -ErrorAction SilentlyContinue |
                    Sort-Object FullName -Descending | Select-Object -ExpandProperty FullName)
    $candidates += @(Get-ChildItem -Path 'C:\Program Files\Python*\python.exe' -ErrorAction SilentlyContinue |
                    Select-Object -ExpandProperty FullName)
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        $version = & $candidate -c "import sys,struct;print('OK' if (3,11)<=sys.version_info[:2]<=(3,13) and struct.calcsize('P')==8 else 'NO')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -eq 'OK') { return $candidate }
    }
    return $null
}

try {
    Write-Host '小A 内核版 1.1.0 — 预览版安装向导' -ForegroundColor Cyan
    $manifest = Assert-Payload
    Write-Host '安装文件校验通过。'
    if ($VerifyOnly) { Write-Host 'VERIFY_OK'; exit 0 }
    Write-Host '账号风险：Google 当前不支持在消费订阅下把 agy 包装进第三方应用，此接入可能导致账号暂停或终止；个人自用、低频使用也不能保证安全。请先阅读 README.md。' -ForegroundColor Yellow
    if (-not [Environment]::Is64BitOperatingSystem) { throw '此安装包需要 Windows 10/11 64位。' }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw '本包尚未验证 ARM64，请使用 x64 Windows。' }

    Write-Host '[1/5] 准备 Python 环境…'
    $pythonExe = Find-Python
    if (-not $pythonExe) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) {
            throw '没有找到 Python 或 Windows 应用安装程序。请先从 python.org 安装 64位 Python 3.13，或从微软商店安装“应用安装程序”，再双击本安装入口。'
        }
        & $winget.Source install --id Python.Python.3.13 --exact --source winget --scope user --silent --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw 'Python 自动安装没有成功，请检查网络后重试。' }
        $pythonExe = Find-Python
        if (-not $pythonExe) { throw 'Python 已安装但未找到，请关闭安装窗口后重新双击安装入口。' }
    }

    # Only code from the verified manifest is copied; recipient data survives reinstall.
    $manage = Join-Path $appRoot 'manage.ps1'
    if (Test-Path -LiteralPath $manage) { & $manage -Action Stop }
    New-Item -ItemType Directory -Path $appRoot -Force | Out-Null
    foreach ($entry in $manifest.files) {
        $dest = [IO.Path]::GetFullPath((Join-Path $appRoot $entry.path))
        if (-not $dest.StartsWith($appRoot + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw '安装目标无效。' }
        New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
        $origin = [IO.Path]::GetFullPath((Join-Path $sourceRoot $entry.path))
        if ($origin -ne $dest) { Copy-Item -LiteralPath $origin -Destination $dest -Force }
    }
    $installedManifest = Join-Path $appRoot 'manifest.json'
    if ($manifestPath -ne $installedManifest) {
        Copy-Item -LiteralPath $manifestPath -Destination $installedManifest -Force
    }
    # This per-user identity is checked before the uninstaller removes anything.
    [ordered]@{product='XiaoA Core';schema=1;installRoot=$appRoot;dataDir=(Join-Path $appRoot 'data')} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $appRoot '.xiaoa-install.json') -Encoding UTF8

    Write-Host '[2/5] 安装小A独立依赖…'
    $runtime = Join-Path $appRoot 'runtime'
    $runtimePython = Join-Path $runtime 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $runtimePython)) {
        & $pythonExe -m venv $runtime
        if ($LASTEXITCODE -ne 0) { throw '创建独立运行环境失败。' }
    }
    & $runtimePython -m pip install --disable-pip-version-check -r (Join-Path $appRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw '依赖下载失败，请检查能否访问 PyPI，然后重新运行安装入口。' }

    Write-Host '[3/5] 准备 Antigravity CLI…'
    $agyExe = Join-Path $env:LOCALAPPDATA 'agy\bin\agy.exe'
    $env:AGY_CLI_DISABLE_AUTO_UPDATE = 'true'
    if (-not (Test-Path -LiteralPath $agyExe)) {
        $setupCache = Join-Path $appRoot 'setup-cache'
        New-Item -ItemType Directory -Path $setupCache -Force | Out-Null
        $officialInstaller = Join-Path $setupCache 'agy-official-install.ps1'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://antigravity.google/cli/install.ps1' -OutFile $officialInstaller
        & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $officialInstaller
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $agyExe)) { throw 'AGY 安装失败，请检查能否访问 Google Antigravity。' }
    }
    $env:XIAOA_DATA_DIR = Join-Path $appRoot 'data'
    $env:XIAOA_AGY_EXE = $agyExe
    New-Item -ItemType Directory -Path $env:XIAOA_DATA_DIR -Force | Out-Null
    Push-Location $env:XIAOA_DATA_DIR
    try {
        $signedIn = $false
        try {
            $probeRaw = & $agyExe -p '/usage' --output-format json 2>$null
            if ($LASTEXITCODE -eq 0) {
                $probe = ($probeRaw -join "`n") | ConvertFrom-Json
                $signedIn = $probe.status -eq 'SUCCESS' -and $probe.command.name -eq 'usage'
            }
        } catch {}
        if (-not $signedIn) {
            Write-Host '请在接下来的 AGY 界面中登录你自己的 Google 账号。登录后按 Ctrl+D 退出界面，安装会继续。' -ForegroundColor Yellow
            & $agyExe
            $probeRaw = & $agyExe -p '/usage' --output-format json 2>$null
            if ($LASTEXITCODE -ne 0) { throw '尚未完成 AGY 登录，请重试安装。' }
            $probe = ($probeRaw -join "`n") | ConvertFrom-Json
            if ($probe.status -ne 'SUCCESS' -or $probe.command.name -ne 'usage') { throw 'AGY 登录状态未通过检查。' }
        }
    } finally { Pop-Location }

    Write-Host '[4/5] 微信扫码绑定…'
    & $runtimePython (Join-Path $appRoot 'app\clawbot_service.py') --check
    if ($LASTEXITCODE -ne 0) { throw '小A运行环境检查失败。' }
    & $runtimePython (Join-Path $appRoot 'app\clawbot_service.py') --login
    if ($LASTEXITCODE -ne 0) { throw '微信扫码未完成，请重新运行安装入口。' }

    Write-Host '[5/5] 创建快捷方式并启动…'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) '小A（内核版）.lnk'))
    $shortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $shortcut.Arguments = '"' + (Join-Path $appRoot 'start.vbs') + '"'
    $shortcut.WorkingDirectory = $appRoot
    $shortcut.Description = '启动自己的小A微信助手'
    $shortcut.Save()
    $uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\XiaoACore'
    New-Item -Path $uninstallKey -Force | Out-Null
    $uninstallCommand = '"' + (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') +
                        '" -NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $appRoot 'uninstall.ps1') + '"'
    foreach ($entry in @{DisplayName='小A内核版';DisplayVersion='1.1.0';InstallLocation=$appRoot;UninstallString=$uninstallCommand}.GetEnumerator()) {
        New-ItemProperty -LiteralPath $uninstallKey -Name $entry.Key -Value $entry.Value -PropertyType String -Force | Out-Null
    }
    New-ItemProperty -LiteralPath $uninstallKey -Name 'NoModify' -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name 'NoRepair' -Value 1 -PropertyType DWord -Force | Out-Null
    & (Join-Path $appRoot 'manage.ps1') -Action Start
    Write-Host '安装完成。以后双击桌面“小A（内核版）”即可启动。主动聊天默认关闭。' -ForegroundColor Green
    exit 0
} catch {
    Write-Host ('安装未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
