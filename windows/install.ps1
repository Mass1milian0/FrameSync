param(
    [Parameter(Mandatory = $false)]
    [ValidatePattern('^[A-Za-z0-9._@:-]+$')]
    [string] $Target = 'steamos@frame'
)

$ErrorActionPreference = 'Stop'
if ($Target.StartsWith('-')) {
    throw 'The SSH target must not begin with a dash.'
}
$repo = Split-Path -Parent $PSScriptRoot
$pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if (-not $pythonLauncher) {
    throw 'Install Python 3.11+ for Windows from python.org, then rerun this script.'
}
$pyLauncherPath = $pythonLauncher.Source
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$scp = (Get-Command scp.exe -ErrorAction Stop).Source
$python = (& $pyLauncherPath -3 -c 'import sys; print(sys.executable)').Trim()
& $pyLauncherPath -3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'
if ($LASTEXITCODE -ne 0) {
    throw 'FrameSync requires Python 3.11 or newer.'
}
if (-not (Test-Path $python)) {
    throw 'Could not locate the Python 3 interpreter selected by the py launcher.'
}
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) {
    $pythonw = $python
}

$installDir = Join-Path $env:LOCALAPPDATA 'FrameSync'
$startupDir = [Environment]::GetFolderPath('Startup')
$startupLink = Join-Path $startupDir 'FrameSync.lnk'
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item -Force (Join-Path $repo 'framesync.py') $installDir
if (Test-Path (Join-Path $installDir 'framesync')) {
    Remove-Item -Recurse -Force (Join-Path $installDir 'framesync')
}
Copy-Item -Recurse -Force (Join-Path $repo 'framesync') $installDir

$secretBytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($secretBytes)
$rng.Dispose()
$secretFile = Join-Path $installDir 'token'
[IO.File]::WriteAllText($secretFile, [Convert]::ToBase64String($secretBytes))
$acl = Get-Acl $secretFile
$acl.SetAccessRuleProtection($true, $false)
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$rule = New-Object -TypeName System.Security.AccessControl.FileSystemAccessRule -ArgumentList @(
    $currentUser,
    [System.Security.AccessControl.FileSystemRights]::Read,
    [System.Security.AccessControl.AccessControlType]::Allow
)
$acl.SetAccessRule($rule)
Set-Acl -Path $secretFile -AclObject $acl

& $ssh $Target 'umask 077 && mkdir -p ~/.cache/framesync-setup && chmod 700 ~/.cache/framesync-setup'
if ($LASTEXITCODE -ne 0) { throw 'Could not create the FrameSync setup directory over SSH.' }
& $scp -r (Join-Path $repo 'framesync') (Join-Path $repo 'framesync.py') `
    (Join-Path $repo 'frame/install.sh') (Join-Path $repo 'frame/uninstall.sh') `
    "$Target`:.cache/framesync-setup/"
if ($LASTEXITCODE -ne 0) { throw 'Could not copy FrameSync files to the Steam Frame.' }
& $scp $secretFile "$Target`:.cache/framesync-setup/token"
if ($LASTEXITCODE -ne 0) { throw 'Could not transfer the FrameSync authentication token.' }
& $ssh $Target 'chmod 700 ~/.cache/framesync-setup/install.sh && bash ~/.cache/framesync-setup/install.sh 39101 39102'
if ($LASTEXITCODE -ne 0) { throw 'Steam Frame installation failed; see the SSH output above.' }

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($startupLink)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$installDir\framesync.py`" host --target `"$Target`" --secret-file `"$secretFile`""
$shortcut.WorkingDirectory = $installDir
$shortcut.Description = 'Automatic Steam Frame clipboard synchronization'
$shortcut.Save()

$arguments = @(
    "`"$installDir\framesync.py`"",
    'host',
    '--target',
    $Target,
    '--secret-file',
    "`"$secretFile`""
)
Start-Process -FilePath $pythonw -ArgumentList $arguments -WorkingDirectory $installDir

Write-Host 'FrameSync installed. It starts at Windows logon and KDE desktop login on the Frame.'
Write-Host "Windows logs: $(Join-Path $env:LOCALAPPDATA 'FrameSync\framesync.log')"
