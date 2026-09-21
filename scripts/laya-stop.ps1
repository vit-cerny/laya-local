# laya-stop.ps1 - stop the Laya GUIs and the voice listener.
# Leaves Chrome (9222) and the model checkpoint untouched.
$names = @('laya_dashboard', 'laya_console', 'laya_voice')
$stopped = 0
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
    $cmd = $_.CommandLine
    if ($cmd -and ($names | Where-Object { $cmd -like "*$_*" })) {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "stopped pid $($_.ProcessId)"
        $stopped++
    }
}
if ($stopped -eq 0) { Write-Host "nothing to stop (no Laya GUI or voice process running)" }
