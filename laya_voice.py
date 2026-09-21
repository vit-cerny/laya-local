"""Voice control for Laya: say a goal, Laya runs it on your computer or in a browser,
and the outcome is spoken back. Fully offline - no API key, no cloud.

Speech out: piper-tts (local neural TTS, models/piper/en_US-lessac-medium.onnx),
played with winsound. Falls back to Windows SAPI (scripts/laya-speak.ps1) if piper
is missing or fails, so the tool never goes silent.

Speech in: faster-whisper (base.en, CPU int8) over sounddevice microphone capture
(4 s windows at 16 kHz mono float32). Falls back to the SAPI dictation listener
(scripts/laya_voice_listener.ps1) if faster-whisper or sounddevice is unavailable.

Routing: if the phrase mentions "browser", "web", "internet" or "site", the goal goes to
browser use (real Chrome via jev_search); otherwise to computer use (desktop via
jev_cu, execution ON with the safety gate). Say "stop listening" to quit.

Usage:
    uv run --env-file .env python laya_voice.py             # listen on the microphone
    uv run --env-file .env python laya_voice.py --selftest  # no mic: synth a phrase, recognize it
    uv run --env-file .env python laya_voice.py --debug     # print raw transcripts + RMS
"""

import argparse
import re
import subprocess
import sys
import tempfile
import winsound
from pathlib import Path

from jev_config import ROOT, load_env

load_env()

SPEAK = ROOT / "scripts" / "laya-speak.ps1"
LISTENER = ROOT / "scripts" / "laya_voice_listener.ps1"
PIPER = Path(sys.executable).parent / "piper.exe"
PIPER_MODEL = ROOT / "models" / "piper" / "en_US-lessac-medium.onnx"
BROWSER_HINT = re.compile(r"\b(browser|web|internet|site)\b", re.IGNORECASE)
STOP_HINT = re.compile(r"\b(stop listening|exit|quit)\b", re.IGNORECASE)
NOISE_TOKENS = {"[blank_audio]", "you", "thank you", "thanks for watching", "subtitles by"}
DEBUG = False
_WHISPER = None


def _piper_synth(text, wav):
    """Synthesize text to a WAV with piper (raises on any failure)."""
    if not PIPER.exists() or not PIPER_MODEL.exists():
        raise FileNotFoundError(f"piper binary or model missing: {PIPER}, {PIPER_MODEL}")
    subprocess.run(
        [str(PIPER), "-m", str(PIPER_MODEL), "-f", str(wav)],
        input=text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        check=True,
    )
    if not wav.exists() or wav.stat().st_size == 0:
        raise RuntimeError("piper produced no audio")


def speak(text, outfile=None):
    """Speak text with piper (local TTS); fall back to SAPI if piper is unavailable."""
    wav = Path(outfile) if outfile else Path(tempfile.gettempdir()) / "laya_speak.wav"
    try:
        _piper_synth(text, wav)
    except Exception as exc:
        print(f"  piper failed ({type(exc).__name__}: {exc}); using SAPI fallback", flush=True)
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SPEAK), "-Text", text]
        if outfile:
            args += ["-OutFile", outfile]
        subprocess.run(args, check=False)
        return True
    if not outfile:
        winsound.PlaySound(str(wav), winsound.SND_FILENAME)
        wav.unlink(missing_ok=True)
    return True


def _get_whisper():
    """Lazy singleton for faster-whisper (first load ~30s)."""
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel

        print("loading faster-whisper base.en (first load ~30s)...", flush=True)
        _WHISPER = WhisperModel("base.en", device="cpu", compute_type="int8")
    return _WHISPER


def _accept(text, rms):
    """Gate transcripts: skip near-silent audio, filler, and very short results."""
    if rms < 0.01:
        return False
    t = text.strip().lower()
    return len(t) >= 3 and t not in NOISE_TOKENS


def route(goal):
    """Run one recognized goal; returns (spoken_summary, detail_dict)."""
    if BROWSER_HINT.search(goal):
        from jev_search import run_search

        print(f"  [browser use] {goal}")
        result = run_search(goal)
        detail = {k: result.get(k) for k in ("status", "final_url", "title")}
        title = result.get("title") or "unknown"
        return f"Done. The page title is {title}. Status {result.get('status')}.", detail
    from jev_cu import run_goal

    print(f"  [computer use] {goal}")
    lines = []
    result = run_goal(goal, window=None, execute=True, max_steps=6, log=lines.append)
    detail = {"status": result.get("status"), "reason": result.get("reason")}
    if result.get("status") == "done":
        return "Done, the goal is satisfied.", detail
    return f"Stopped. Status {result.get('status')}. {result.get('reason') or ''}".strip(), detail


def _handle_goal(text):
    """Route one accepted transcript; returns False when the user asked to stop."""
    print(f"heard: {text}", flush=True)
    if STOP_HINT.search(text):
        speak("Stopping.")
        return False
    try:
        summary, detail = route(text)
        print(f"  result: {detail}", flush=True)
    except Exception as exc:
        summary = f"Failed. {type(exc).__name__}"
        print(f"  error: {exc}", flush=True)
    speak(summary)
    print("listening...", flush=True)
    return True


def _listen_local():
    """Microphone loop: sounddevice capture + faster-whisper transcription."""
    import numpy as np
    import sounddevice as sd
    from faster_whisper import WhisperModel  # noqa: F401  (import check for fallback)

    model = _get_whisper()
    sd.query_devices(kind="input")  # raises if no input device -> SAPI fallback
    rate, window = 16000, 4.0
    print("listening (faster-whisper)...", flush=True)
    while True:
        try:
            audio = sd.rec(int(rate * window), samplerate=rate, channels=1, dtype="float32")
            sd.wait()
            rms = float(np.sqrt(np.mean(audio**2))) if audio.size else 0.0
            segments, _ = model.transcribe(audio, language="en")
            text = " ".join(s.text.strip() for s in segments).strip()
            if DEBUG:
                print(f"  [debug] rms={rms:.4f} raw={text!r}", flush=True)
            if not _accept(text, rms):
                continue
            if not _handle_goal(text):
                break
        except Exception as exc:
            print(f"  capture/transcribe error: {exc}", flush=True)


def _listen_sapi():
    """Fallback microphone loop: the existing SAPI dictation listener subprocess."""
    print("listening (SAPI fallback)...", flush=True)
    proc = subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LISTENER)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("GOAL:"):
                continue
            goal = line[5:].strip()
            if not goal:
                continue
            if not _handle_goal(goal):
                break
    finally:
        proc.terminate()


def listen():
    from laya_ask import agent

    print("warming local Laya model (first load ~35s)...", flush=True)
    agent()
    speak("Laya voice control ready. Say a goal, or say stop listening.")
    try:
        try:
            _listen_local()
        except Exception as exc:
            print(f"local STT unavailable ({type(exc).__name__}: {exc}); falling back to SAPI listener", flush=True)
            _listen_sapi()
    except KeyboardInterrupt:
        pass
    finally:
        print("stopped.")


def selftest():
    """No microphone needed: piper-synthesize a phrase to a WAV, then transcribe it with faster-whisper."""
    wav = Path(tempfile.gettempdir()) / "laya_selftest.wav"
    phrase = "open notepad"
    print(f"synthesizing {phrase!r} -> {wav}")
    speak(phrase, outfile=str(wav))
    if not wav.exists() or wav.stat().st_size == 0:
        print("FAIL: no WAV produced (piper failed and the SAPI fallback produced nothing)")
        return 1
    print("recognizing...")
    try:
        model = _get_whisper()
        segments, _ = model.transcribe(str(wav), language="en")
        transcript = " ".join(s.text.strip() for s in segments).strip()
    except Exception as exc:
        print(f"FAIL: faster-whisper transcription error: {type(exc).__name__}: {exc}")
        return 1
    print(f"transcript: {transcript!r}")
    norm = transcript.lower().replace(".", "").replace(",", "").strip()
    if norm == phrase:
        print("PASS: recognized the synthesized phrase.")
        return 0
    print("FAIL: transcript did not match the phrase.")
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="test the piper + faster-whisper pipeline from a synthesized WAV (no mic)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="print raw transcripts and RMS from the microphone loop",
    )
    args = parser.parse_args()
    global DEBUG
    DEBUG = args.debug
    if args.selftest:
        return selftest()
    listen()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())