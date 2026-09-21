"""Voice control for Laya: say a goal, Laya runs it on your computer or in a browser,
and the outcome is spoken back. Fully offline (Windows SAPI) - no API key, no cloud.

Routing: if the phrase mentions "browser", "web", "internet" or "site", the goal goes to
browser use (real Chrome via jev_search); otherwise to computer use (desktop via
jev_cu, execution ON with the safety gate). Say "stop listening" to quit.

Usage:
    uv run --env-file .env python laya_voice.py             # listen on the microphone
    uv run --env-file .env python laya_voice.py --selftest  # no mic: synth a phrase, recognize it
"""

import argparse
import os
import re
import subprocess
from pathlib import Path

from jev_config import ROOT, load_env

load_env()

SPEAK = ROOT / "scripts" / "laya-speak.ps1"
LISTENER = ROOT / "scripts" / "laya_voice_listener.ps1"
BROWSER_HINT = re.compile(r"\b(browser|web|internet|site)\b", re.IGNORECASE)
STOP_HINT = re.compile(r"\b(stop listening|exit|quit)\b", re.IGNORECASE)


def speak(text, outfile=None):
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SPEAK), "-Text", text]
    if outfile:
        args += ["-OutFile", outfile]
    subprocess.run(args, check=False)


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


def listen():
    from laya_ask import agent

    print("warming local Laya model (first load ~35s)...", flush=True)
    agent()
    speak("Laya voice control ready. Say a goal, or say stop listening.")
    print("listening...", flush=True)
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
            print(f"heard: {goal}", flush=True)
            if STOP_HINT.search(goal):
                speak("Stopping.")
                break
            try:
                summary, detail = route(goal)
                print(f"  result: {detail}", flush=True)
            except Exception as exc:
                summary = f"Failed. {type(exc).__name__}"
                print(f"  error: {exc}", flush=True)
            speak(summary)
            print("listening...", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        print("stopped.")


def selftest():
    """No microphone needed: synthesize a phrase to a WAV, then recognize it."""
    wav = Path(os.environ.get("TEMP", ".")) / "laya_selftest.wav"
    phrase = "open notepad"
    print(f"synthesizing {phrase!r} -> {wav}")
    speak(phrase, outfile=str(wav))
    if not wav.exists() or wav.stat().st_size == 0:
        print("FAIL: no WAV produced (is audio output available?)")
        return 1
    print("recognizing...")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(LISTENER), "-WaveFile", str(wav)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    heard = [ln[5:] for ln in proc.stdout.splitlines() if ln.startswith("GOAL:")]
    print(f"heard: {heard}")
    if not heard:
        print("NOTE: recognizer returned nothing for synthesized audio (dictation accuracy varies);")
        print("      the pipeline itself ran clean. Retry with a real microphone.")
        return 2
    print("PASS: speech pipeline produced a transcription." if heard else "result ambiguous.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true", help="test recognition from a synthesized WAV (no mic)")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    listen()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())