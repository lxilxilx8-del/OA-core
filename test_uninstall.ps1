param([string]$PackageRoot='')
$ErrorActionPreference='Stop'
if (-not $PackageRoot) {
    $PackageRoot=if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'uninstall.ps1')) { $PSScriptRoot }
                 else { Join-Path $PSScriptRoot 'XiaoA-Core-1.1.0' }
}
. (Join-Path $PackageRoot 'uninstall.ps1')
$testParent=[IO.Path]::GetFullPath($PSScriptRoot)
$testRoot=Join-Path $testParent ('qa-uninstall-'+[Guid]::NewGuid().ToString('N'))
if (-not $testRoot.StartsWith($testParent+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test directory' }
New-Item -ItemType Directory -Path $testRoot | Out-Null
$oldLocalAppData=$env:LOCALAPPDATA
$oldLocation=Get-Location
$script:FakeProcesses=@()
$script:Stopped=New-Object System.Collections.ArrayList
$script:RegistrationRemovals=New-Object System.Collections.ArrayList
$script:ReuseProcess=$false
$script:Checks=0

# No real process or registry mutation is possible in these tests.
function Get-CimInstance {
    [CmdletBinding()]param([string]$ClassName,[string]$Filter)
    if ($Filter -match 'ProcessId=(\d+)') {
        $current=@($script:FakeProcesses | Where-Object ProcessId -eq ([int]$Matches[1]))
        if ($script:ReuseProcess -and $current.Count -and $current[0].ProcessId -eq 41001) {
            $copy=$current[0].PSObject.Copy();$copy.CreationDate=$copy.CreationDate.AddSeconds(1);return $copy
        }
        return $current
    }
    return $script:FakeProcesses
}
function Stop-Process { [CmdletBinding()]param([int]$Id) [void]$script:Stopped.Add($Id) }
function Wait-Process { [CmdletBinding()]param([int]$Id,[int]$Timeout) }
function Remove-XiaoARegistration([string]$Root) { [void]$script:RegistrationRemovals.Add($Root) }
function Get-XiaoADesktop { return $script:FakeDesktop }
function Assert-True($Value,[string]$Message) { if (-not $Value) { throw $Message };$script:Checks++ }
function Assert-Throws([scriptblock]$Action,[string]$Message) {
    $threw=$false
    try { & $Action } catch { $threw=$true }
    Assert-True $threw $Message
}
function New-Fixture([string]$Name) {
    $env:LOCALAPPDATA=Join-Path $testRoot $Name
    $root=Get-XiaoAInstallRoot
    $script:FakeDesktop=Join-Path $env:LOCALAPPDATA 'Desktop'
    foreach ($folder in @($root,(Join-Path $root 'app'),(Join-Path $root 'runtime\Scripts'),(Join-Path $root 'data'),$script:FakeDesktop,(Join-Path $env:LOCALAPPDATA 'agy\bin'),(Join-Path $env:LOCALAPPDATA 'Programs\Python'))) {
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
    }
    [ordered]@{product='XiaoA Core';schema=1;installRoot=$root;dataDir=(Join-Path $root 'data')} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root '.xiaoa-install.json') -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $root 'app\clawbot_service.py') -Value '# fake code'
    Set-Content -LiteralPath (Join-Path $root 'runtime\Scripts\python.exe') -Value 'fake runtime'
    Set-Content -LiteralPath (Join-Path $root 'data\history.txt') -Value 'invented chat for testing'
    Set-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'agy\bin\agy.exe') -Value 'shared agy sentinel'
    Set-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Python\python.exe') -Value 'shared python sentinel'
    $script:FakeProcesses=@();$script:Stopped.Clear();$script:RegistrationRemovals.Clear()
    return $root
}
function New-TestShortcut([string]$Root,[switch]$OtherApp) {
    $shell=New-Object -ComObject WScript.Shell
    $path=Join-Path $script:FakeDesktop 'XiaoA Core.lnk'
    $shortcut=$shell.CreateShortcut($path)
    $shortcut.TargetPath=if ($OtherApp) { Join-Path $env:WINDIR 'System32\notepad.exe' } else { Join-Path $env:WINDIR 'System32\wscript.exe' }
    $shortcut.Arguments='"'+(Join-Path $Root 'start.vbs')+'"'
    $shortcut.Save()
    return $path
}
try {
    $root=New-Fixture 'keep'
    $before=(Get-FileHash -LiteralPath (Join-Path $root 'data\history.txt')).Hash
    $shortcut=New-TestShortcut $root
    $result=Invoke-XiaoAUninstall -Choice Keep
    Assert-True ($result.status -eq 'uninstalled' -and $result.data_retained) 'Keep mode result'
    Assert-True ((Get-FileHash -LiteralPath (Join-Path $root 'data\history.txt')).Hash -eq $before) 'History changed'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $root 'app'))) 'App remains'
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $root 'runtime'))) 'Runtime remains'
    Assert-True (-not (Test-Path -LiteralPath $shortcut)) 'Owned shortcut remains'
    Assert-True (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'agy\bin\agy.exe')) 'Shared AGY removed'
    Assert-True (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Python\python.exe')) 'Shared Python removed'

    $root=New-Fixture 'delete'
    $shortcut=New-TestShortcut $root -OtherApp
    $result=Invoke-XiaoAUninstall -Choice Delete
    Assert-True (-not $result.data_retained -and -not (Test-Path -LiteralPath $root)) 'Delete mode left app/data'
    Assert-True (Test-Path -LiteralPath $shortcut) 'Unrelated shortcut removed'
    Assert-True (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'agy\bin\agy.exe')) 'Shared AGY removed in delete mode'

    $root=New-Fixture 'cancel'
    $result=Invoke-XiaoAUninstall -Choice Cancel
    Assert-True ($result.status -eq 'cancelled' -and (Test-Path -LiteralPath (Join-Path $root 'app\clawbot_service.py'))) 'Cancel mutated app'
    Assert-True ($script:RegistrationRemovals.Count -eq 0 -and $script:Stopped.Count -eq 0) 'Cancel performed cleanup'

    $root=New-Fixture 'missing-marker'
    Remove-Item -LiteralPath (Join-Path $root '.xiaoa-install.json')
    Assert-Throws { Invoke-XiaoAUninstall -Choice Delete } 'Unmarked folder accepted'
    Assert-True (Test-Path -LiteralPath (Join-Path $root 'data\history.txt')) 'Unmarked data removed'

    $root=New-Fixture 'wrong-marker'
    [ordered]@{product='Other';schema=1;installRoot=$root;dataDir=(Join-Path $root 'data')} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root '.xiaoa-install.json') -Encoding UTF8
    Assert-Throws { Invoke-XiaoAUninstall -Choice Delete } 'Wrong identity accepted'
    Assert-True (Test-Path -LiteralPath (Join-Path $root 'data\history.txt')) 'Wrong-identity data removed'

    $root=New-Fixture 'boundaries'
    $outside=Join-Path $env:LOCALAPPDATA 'outside.txt'
    Set-Content -LiteralPath $outside -Value 'outside sentinel'
    Assert-Throws { Remove-XiaoAOwnedEntry -Path $outside -Root $root } 'Outside path accepted'
    Assert-True (Test-Path -LiteralPath $outside) 'Outside file removed'

    $root=New-Fixture 'child-junction'
    $outside=Join-Path $env:LOCALAPPDATA 'outside'
    New-Item -ItemType Directory -Path $outside | Out-Null
    Set-Content -LiteralPath (Join-Path $outside 'sentinel.txt') -Value 'external test sentinel'
    New-Item -ItemType Junction -Path (Join-Path $root 'linked') -Target $outside | Out-Null
    $null=Invoke-XiaoAUninstall -Choice Delete
    Assert-True (Test-Path -LiteralPath (Join-Path $outside 'sentinel.txt')) 'Junction target traversed'
    Assert-True (-not (Test-Path -LiteralPath $root)) 'Linked child prevented uninstall'

    $root=New-Fixture 'root-junction'
    $outside=Join-Path $env:LOCALAPPDATA 'outside-root'
    Move-Item -LiteralPath $root -Destination $outside
    New-Item -ItemType Junction -Path $root -Target $outside | Out-Null
    Assert-Throws { Invoke-XiaoAUninstall -Choice Delete } 'Root junction accepted'
    Assert-True (Test-Path -LiteralPath (Join-Path $outside 'data\history.txt')) 'Root junction target altered'
    [IO.Directory]::Delete($root,$false)

    $root=New-Fixture 'processes'
    $created=[datetime]'2026-01-01T12:00:00'
    $service=Join-Path $root 'app\clawbot_service.py'
    $runtime=Join-Path $root 'runtime\Scripts\python.exe'
    $script:FakeProcesses=@(
        [pscustomobject]@{ProcessId=41001;ParentProcessId=1;ExecutablePath=$runtime;CommandLine=('"'+$runtime+'" "'+$service+'"');CreationDate=$created},
        [pscustomobject]@{ProcessId=41002;ParentProcessId=41001;ExecutablePath='agy.exe';CommandLine='agy -p fake';CreationDate=$created},
        [pscustomobject]@{ProcessId=41003;ParentProcessId=1;ExecutablePath=$runtime;CommandLine='python other_app.py';CreationDate=$created},
        [pscustomobject]@{ProcessId=41004;ParentProcessId=1;ExecutablePath='agy.exe';CommandLine='agy other session';CreationDate=$created}
    )
    Stop-XiaoAOwnedProcessTree -Root $root
    Assert-True (($script:Stopped -join ',') -eq '41002,41001') 'Stopped unrelated processes'
    $script:Stopped.Clear();$script:ReuseProcess=$true
    Stop-XiaoAOwnedProcessTree -Root $root
    Assert-True (($script:Stopped -join ',') -eq '41002') 'Reused process identity was stopped'
    [pscustomobject]@{uninstall_checks_passed=$script:Checks;real_processes_stopped=0;real_registry_changes=0;only_fixture_directories_deleted=$true} | ConvertTo-Json -Compress
} finally {
    $env:LOCALAPPDATA=$oldLocalAppData
    Set-Location -LiteralPath $oldLocation.Path
    $resolved=[IO.Path]::GetFullPath($testRoot)
    if ($resolved.StartsWith($testParent+'\',[StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolved) -like 'qa-uninstall-*') {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
