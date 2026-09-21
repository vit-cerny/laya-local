# laya-speak.ps1 - offline text-to-speech (Windows SAPI). Used by laya_voice.py.
#   .\scripts\laya-speak.ps1 -Text "ready"
#   .\scripts\laya-speak.ps1 -Text "ready" -OutFile "$env:TEMP\sample.wav"   (selftest)
param(
    [string]$Text,
    [string]$OutFile = ""
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($OutFile) {
    $synth.SetOutputToWaveFile($OutFile)
} else {
    $synth.SetOutputToDefaultAudioDevice()
}
$synth.Speak($Text)
$synth.Dispose()