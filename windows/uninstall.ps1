$ErrorActionPreference = 'Stop'

$installDir = Join-Path $env:LOCALAPPDATA 'FrameSync'
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'FrameSync.lnk'
$scriptPath = Join-Path $installDir 'framesync.py'
Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('python.exe', 'pythonw.exe') -and
    $_.CommandLine -and
    $_.CommandLine.Contains($scriptPath)
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains('127.0.0.1:39102:127.0.0.1:39101')
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Remove-Item -Force -ErrorAction SilentlyContinue $startupLink
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $installDir
Write-Host 'Windows FrameSync startup and files removed.'
Write-Host 'To remove the Frame component, run ~/.local/share/framesync/uninstall.sh over SSH before deleting that directory.'
