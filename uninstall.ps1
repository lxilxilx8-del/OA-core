param([ValidateSet('Ask','Keep','Delete','Cancel')][string]$DataChoice='Ask')
$ErrorActionPreference = 'Stop'

function Get-XiaoAInstallRoot {
    return [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'XiaoACore')).TrimEnd('\')
}

function Get-XiaoADesktop {
    return [Environment]::GetFolderPath('Desktop')
}

function Assert-XiaoAInstallRoot([string]$Root) {
    $expected = Get-XiaoAInstallRoot
    if ($Root -ne $expected -or (Split-Path -Leaf $Root) -ne 'XiaoACore') { throw '卸载目录校验失败。' }
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $false }
    $folder = Get-Item -LiteralPath $Root -Force
    if ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '安装目录是链接，已停止卸载。' }
    $marker = Join-Path $Root '.xiaoa-install.json'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { throw '没有找到小A安装标记，未删除任何文件。请先用新版安装包更新。' }
    $identity = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($identity.product -ne 'XiaoA Core' -or $identity.schema -ne 1 -or
        $identity.installRoot -ne $Root -or $identity.dataDir -ne (Join-Path $Root 'data')) {
        throw '小A安装标记不匹配，未删除任何文件。'
    }
    return $true
}

function Remove-XiaoAOwnedEntry([string]$Path,[string]$Root) {
    $absolute = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($absolute -ne $Root -and -not $absolute.StartsWith($Root + '\',[StringComparison]::OrdinalIgnoreCase)) {
        throw '拒绝删除小A目录以外的文件。'
    }
    if (-not (Test-Path -LiteralPath $absolute)) { return }
    $item = Get-Item -LiteralPath $absolute -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        # Delete the link itself without following a junction into other folders.
        if ($item.PSIsContainer) { [IO.Directory]::Delete($absolute,$false) }
        else { [IO.File]::Delete($absolute) }
        return
    }
    if ($item.PSIsContainer) {
        foreach ($child in @(Get-ChildItem -LiteralPath $absolute -Force)) {
            Remove-XiaoAOwnedEntry -Path $child.FullName -Root $Root
        }
    }
    Remove-Item -LiteralPath $absolute -Force
}

function Stop-XiaoAOwnedProcessTree([string]$Root) {
    $service = Join-Path $Root 'app\clawbot_service.py'
    $executables = @((Join-Path $Root 'runtime\Scripts\python.exe'),(Join-Path $Root 'runtime\Scripts\pythonw.exe'))
    $all = @(Get-CimInstance Win32_Process)
    $owned = @($all | Where-Object {
        $executables -contains $_.ExecutablePath -and $_.CommandLine -and
        $_.CommandLine.IndexOf('"' + $service + '"',[StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    $queue = New-Object System.Collections.ArrayList
    foreach ($bot in $owned) { [void]$queue.Add($bot) }
    for ($i=0; $i -lt $queue.Count; $i++) {
        foreach ($child in @($all | Where-Object ParentProcessId -eq $queue[$i].ProcessId)) {
            if (-not @($queue | Where-Object ProcessId -eq $child.ProcessId).Count) { [void]$queue.Add($child) }
        }
    }
    for ($i=$queue.Count-1; $i -ge 0; $i--) {
        $snapshot = $queue[$i]
        $current = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $snapshot.ProcessId) -ErrorAction SilentlyContinue
        if ($current -and $current.CreationDate -eq $snapshot.CreationDate -and $current.ExecutablePath -eq $snapshot.ExecutablePath) {
            Stop-Process -Id $snapshot.ProcessId -ErrorAction Stop
            Wait-Process -Id $snapshot.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }
    }
}

function Remove-XiaoAShortcut([string]$Root) {
    $desktop = Get-XiaoADesktop
    $shortcutPath = Join-Path $desktop '小A（内核版）.lnk'
    if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $expected = '"' + (Join-Path $Root 'start.vbs') + '"'
        if ($shortcut.Arguments -eq $expected -and
            [IO.Path]::GetFileName($shortcut.TargetPath) -ieq 'wscript.exe') {
            Remove-Item -LiteralPath $shortcutPath -Force
        }
    }
}

function Remove-XiaoARegistration([string]$Root) {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\XiaoACore'
    if (Test-Path -LiteralPath $key) {
        $registration = Get-ItemProperty -LiteralPath $key
        if ($registration.InstallLocation -eq $Root -and $registration.DisplayName -eq '小A内核版') {
            Remove-Item -LiteralPath $key -Force
        }
    }
}

function Show-XiaoADataChoice {
    $shell = New-Object -ComObject WScript.Shell
    $message = "将卸载小A程序和快捷方式。是否保留本地数据（聊天、记忆、资料和微信绑定）？`n`n是：保留数据，便于以后重装。`n否：一并删除数据，无法恢复。`n取消：不卸载。`n`n只处理内核版，不影响其他小A、Python 或 AGY。"
    switch ($shell.Popup($message,0,'卸载小A内核版',35)) {
        6 { return 'Keep' }
        7 { return 'Delete' }
        default { return 'Cancel' }
    }
}

function Invoke-XiaoAUninstall([string]$Choice='Ask') {
    if ($Choice -notin @('Ask','Keep','Delete','Cancel')) { throw '卸载选项无效。' }
    if ($Choice -eq 'Cancel') { return [pscustomobject]@{status='cancelled';data_retained=$true} }
    $root = Get-XiaoAInstallRoot
    if (-not (Assert-XiaoAInstallRoot -Root $root)) { return [pscustomobject]@{status='not_installed';data_retained=$false} }
    if ($Choice -eq 'Ask') { $Choice = Show-XiaoADataChoice }
    if ($Choice -eq 'Cancel') { return [pscustomobject]@{status='cancelled';data_retained=$true} }
    Stop-XiaoAOwnedProcessTree -Root $root
    # Release the working directory before removing the installed scripts themselves.
    Set-Location -LiteralPath ([IO.Path]::GetTempPath())
    foreach ($item in @(Get-ChildItem -LiteralPath $root -Force)) {
        if ($Choice -eq 'Keep' -and $item.Name -eq 'data') { continue }
        if ($item.Name -eq '.xiaoa-install.json') { continue }
        Remove-XiaoAOwnedEntry -Path $item.FullName -Root $root
    }
    Remove-XiaoAShortcut -Root $root
    Remove-XiaoARegistration -Root $root
    Remove-XiaoAOwnedEntry -Path (Join-Path $root '.xiaoa-install.json') -Root $root
    if ($Choice -eq 'Delete' -or -not @(Get-ChildItem -LiteralPath $root -Force).Count) {
        Remove-XiaoAOwnedEntry -Path $root -Root $root
    }
    return [pscustomobject]@{status='uninstalled';data_retained=($Choice -eq 'Keep')}
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $result = Invoke-XiaoAUninstall -Choice $DataChoice
        if ($DataChoice -eq 'Ask' -and $result.status -ne 'cancelled') {
            $message = if ($result.status -eq 'not_installed') { '没有找到已安装的小A内核版。' }
                       elseif ($result.data_retained) { '小A内核版已卸载。本地数据已保留，重装可继续使用。' }
                       else { '小A内核版及本地数据已删除。' }
            $shell = New-Object -ComObject WScript.Shell
            [void]$shell.Popup($message,0,'小A内核版',64)
        }
        $result | ConvertTo-Json -Compress
        exit 0
    } catch {
        if ($DataChoice -eq 'Ask') {
            $shell = New-Object -ComObject WScript.Shell
            [void]$shell.Popup(('卸载未完成：' + $_.Exception.Message),0,'小A内核版',48)
        }
        Write-Error $_.Exception.Message
        exit 1
    }
}
