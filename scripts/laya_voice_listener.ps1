# laya_voice_listener.ps1 - offline microphone listener (Windows SAPI dictation).
# Prints one "GOAL:<recognized text>" line per phrase to stdout. Exits on
# "stop listening" (case-insensitive) or, in selftest mode (-WaveFile), after one phrase.
param(
    [string]$WaveFile = ""
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$rec = New-Object System.Speech.Recognition.SpeechRecognitionEngine
if ($WaveFile) {
    $rec.SetInputToWaveFile($WaveFile)
} else {
    $rec.SetInputToDefaultAudioDevice()
}
$rec.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
while ($true) {
    try {
        $result = $rec.Recognize()
    } catch {
        Start-Sleep -Milliseconds 300
        continue
    }
    if ($null -eq $result) { continue }
    $text = ($result.Text).Trim()
    if (-not $text) { continue }
    Write-Output ("GOAL:" + $text)
    if ($WaveFile) { break }
    if ($text -match "stop listening") { break }
}