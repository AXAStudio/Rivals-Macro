# Is this safe?

Short answer: don't take my word for it. Here is how to check, in rising order
of effort. Every claim below is something you can verify yourself.

## 1. Don't run the .exe at all — run the source

This is the strongest option, because it requires trusting nothing:

```
git clone https://github.com/AXAStudio/Rivals-Macro
cd Rivals-Macro
pip install -r requirements.txt
python main.py
```

Three dependencies, all mainstream: `mss` (screen capture), `opencv-python`
(image matching), `numpy`. No installer, no service, nothing added to startup.

## 2. Checks you can run on the source in under a minute

```bash
# Does it have any way to talk to the internet?
grep -rE "^\s*(import|from)\s+(socket|ssl|http|urllib|requests|ftplib|smtplib|websocket)" rivals/ main.py

# Does it launch other programs or shell out?
grep -rE "subprocess|os\.system|os\.popen|ShellExecute" rivals/ main.py

# Does it hide code behind eval/exec/pickle/base64?
grep -rE "\beval\(|\bexec\(|__import__|pickle|marshal|base64" rivals/ main.py

# Everything it writes to disk
grep -rE "write_text|open\(.*[\"']w|mkdir" rivals/ main.py
```

At the tagged release, all three of the first commands return **nothing**, and
the last returns exactly one file: `config.json`, holding your settings.

The whole thing is about **3,400 lines** across 12 files. That is small enough
to actually read, which is not true of most things people download.

## 3. What it does at the OS level

Three capabilities, and no others:

| What | How | Why |
| --- | --- | --- |
| Reads the screen | `mss` screenshot of one monitor | to see which game screen is up |
| Compares images | OpenCV template matching | to recognise buttons and weapon cards |
| Sends clicks and keys | Win32 `SendInput` | to click those buttons |

It does not read or write Roblox's memory, inject into its process, hook any
API, or modify any game file. It looks at pixels and moves the mouse — the same
things you do.

## 4. VirusTotal

Scan it and post the link. **Expect some detections, and don't panic**: a
PyInstaller-packed Python app that captures the screen and synthesises input
matches the behavioural profile of a remote-access tool, so heuristic engines
flag it. This is a well-known false positive with PyInstaller, not evidence of
anything. That is also exactly why the source and the build script are public —
so you don't have to settle the argument with a scanner.

## 5. Verify your download matches the release

Compare the hash of what you downloaded against the one in the release notes:

```powershell
Get-FileHash .\RivalsMacro.exe -Algorithm SHA256
```

If it differs, you did not get the file that was published.

## 6. Build the .exe yourself

```
python tools/build.py
```

It builds from a clean throwaway venv and finishes by running
`RivalsMacro.exe --selftest` against the packaged binary. Note that PyInstaller
output is **not** byte-for-byte reproducible — your build will be functionally
identical but will not have the same hash as the published one. Compare
behaviour, not hashes.

## What it cannot do

It has no network code, so it cannot exfiltrate anything, download anything, or
receive commands. It stores no credentials and never asks for a login. It is
not a Roblox exploit or executor and does not interact with Roblox beyond
sending ordinary mouse and keyboard input to whatever window is focused.

## Fair warnings

- The build is **unsigned**, so SmartScreen will warn on first run. Code signing
  costs money; this is free.
- It sends real input to whatever is focused. If you alt-tab, it re-focuses
  Roblox rather than clicking into your other windows — but F9 stops it dead,
  from anywhere, at any time.
- Automating a game may violate the game's or Roblox's terms. That risk is
  yours to weigh.
