# Laya Computer Use - tutorial

Lokální AI ovládání Windows 11. Zadáš cíl textem nebo hlasem, program ho provede:
otevře aplikaci, klikne, napíše text. Rozhodnutí dělá engine, který si vybereš.

## Instalace

```powershell
cd C:\Users\witek\Documents\browseruse
uv sync
uv add pywinauto                      # jen pokud chybí
```

Stáhni checkpoint Layi (~800 MB) do `models\laya-typed-decisions`.
Pak zkopíruj `.env.example` na `.env` a vyplň klíče:

```powershell
Copy-Item .env.example .env
```

## CLI

```powershell
laya-cu doctor                        # kontrola: pywinauto, checkpoint, VRAM
laya-cu apps                          # 240 spustitelných programů
laya-cu launch notepad                # otevře program
laya-cu windows                       # seznam otevřených oken
laya-cu run "Type hello into the document" --app notepad --go
```

`run` je bez `--go` jen suchý běh (nic nevykoná). Cíl zadávej anglicky.
Destruktivní cíle (delete, send, pay, install, close) zastaví běh, dokud nepřidáš `--allow-sensitive`.

## Volba enginu (to nejdůležitější)

Nastav v `.env` nebo v shellu přes `JEV_DECISION`:

| Engine | Kdy použít | Klíč |
|---|---|---|
| `laya` (výchozí) | lokální, zdarma, rychlý start | ne |
| **`deepseek`** | **skutečné úkoly - umí vybrat akci** | `TEXT_MODEL_API_KEY` |
| `typesafe` | cloud API | `TYPESAFE_API_KEY` |

```powershell
$env:JEV_DECISION="deepseek"
laya-cu run "Type hello into the document" --app notepad --go
```

**Ověřeno:** `deepseek` vybral `TYPE_TEXT`, vygeneroval text, napsal ho do Notepadu
(`document value: 'hello'`). Laya na stejném úkolu vrátila `BLOCKED`.

## Hlas

```powershell
laya-voice                            # poslechová smyčka
laya-voice --selftest                 # test TTS/STT bez mikrofonu
```

Řekneš cíl, program ho přepíše a provede. "Stop listening" ukončí poslech.
Menší modely: TTS `TinyTTS` (~3,4 MB) místo piper (~63 MB), STT `tiny.en` (~75 MiB) místo `base.en` (~142 MiB).

## Overlay

Zapnutý ve výchozím stavu. Ukazuje box kolem cílového elementu, kroužek v místě kliku
a panel se krokem, rychlostí a logem Layi. Sám zmizí po 4 s.

```powershell
$env:JEV_CU_HUD=0                     # vypnout
```

## Struktura

```
laya_cu/
  jev_cu.py        # smyčka: okno -> elementy -> rozhodnutí -> akce
  laya_cu.py       # CLI (laya-cu)
  jev_cu_hud.py    # desktopový overlay
  laya_voice.py    # hlas (laya-voice)
```

## Poctivé limity

- **Laya sama nevybere akci** na hustých oknech (měřeno: skóre 0.26-0.41 = šum, argmax špatně).
  Je to klasifikátor obsahu, ne selektor akcí. Proto selhává bezpečně - vrátí `BLOCKED`, neklikne naslepo.
- **Na skutečné úkoly použij `deepseek`** (nebo `typesafe`). To je ověřená cesta.
- Aplikace s CEF/canvas renderingem (Steam, hry) nejsou ve stromu UI vidět, takže je program neovládá.
