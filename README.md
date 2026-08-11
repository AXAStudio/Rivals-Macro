# Rivals — Glass Grind Macro

Lobby to combat, on a loop:

```
Play  ->  scroll to Free For All  ->  "Join this game!"
      ->  primary -> secondary -> melee -> utility
      ->  jump spam + camera sweep + fire
      ->  leaderboard: stop dead, stay still through the map vote
      ->  weapon select reopens, repeat
```

## Run it

```
pip install -r requirements.txt
python main.py
```

`F8` starts/stops, `F9` is a panic stop. Both work while Roblox has focus.

## "How do I know this isn't a virus?"

Fair question for any downloaded binary. **[VERIFY.md](VERIFY.md)** lists checks
you can run yourself rather than assurances you have to take on faith — the
headline being that you can skip the .exe entirely and run the source, which is
~3,400 lines across 12 files with three mainstream dependencies.

## Building an .exe to hand out

```
python tools/build.py            # dist/RivalsMacro.exe, one file, ~67 MB
python tools/build.py --onedir   # a folder instead; starts faster
python tools/build.py --console  # keep a console window for debugging
```

The result needs nothing on the target machine — no Python, no OpenCV. The
templates are bundled inside; settings are written to `config.json` beside the
.exe, falling back to `%LOCALAPPDATA%\RivalsMacro` if that folder is read-only.
A `README.txt` for whoever you give it to is written alongside.

It builds from a throwaway venv in `.build-venv` rather than your system
Python. That is not fussiness: PyInstaller refuses to run at all next to the
obsolete `typing` backport that was installed here, and a clean environment
also keeps unrelated packages out of the bundle. The venv is created once.

The build finishes by running `RivalsMacro.exe --selftest`, which loads every
sentinel template, the weapon catalogue and the clock glyphs out of the bundle
and exits non-zero if anything is missing. A windowed .exe that cannot find its
templates shows nothing at all, so the build is not considered done until the
packaged binary has proved it can read its own data.

Measured on this machine: **3.0 s** from launch to a visible window, 67 MB.
Unsigned, so SmartScreen will warn the first time.

## Presets

The loadout row has one-click presets. **Glass Grind** — grenade launcher plus
Random for the other three — is the default and what the project is named for.
**All Random** and **Anything Offered** are the other two built-ins.

Set the dropdowns to anything you like and **Save as…** keeps it as your own
preset alongside them; the chip highlights whichever preset matches the current
dropdowns, and clears to "custom loadout" the moment you deviate. User presets
live in `config.json` and the built-ins cannot be overwritten or deleted.

## Layout

| File | Role |
| --- | --- |
| `main.py` | entry point |
| `rivals/ui.py` | control panel: presets, loadout, tuning, live log |
| `rivals/theme.py` | dark theme for the control panel |
| `rivals/timeline.py` | the activity graph |
| `rivals/stats.py` | grinding-time bookkeeping behind it |
| `rivals/macro.py` | the scene-driven loop |
| `rivals/detect.py` | which screen am I looking at |
| `rivals/picker.py` | loadout grid geometry, cell state, active slot |
| `rivals/clock.py` | reads the match timer, without OCR |
| `rivals/vision.py` | capture + template matching |
| `rivals/inputs.py` | Win32 `SendInput` — scancode keys, relative mouse |
| `rivals/window.py` | finds and focuses the Roblox window |
| `templates/manifest.json` | per-template reference height (see below) |
| `tools/verify.py` | replays every `tests/*.png` through the real decision code |
| `tools/dryrun.py` | whole loop against those captures, input stubbed |
| `tools/scan.py` | "what do you see?" on the live screen or a saved PNG |
| `tools/recut.py` | re-cut a weapon card template from your own capture |

## The activity graph

The panel tracks how much of your time is actually being spent *in a match*,
which for a grind is the only number that matters.

- **Green band** — grinding: the combat loop running. Lobby, mode list, loadout
  picking, leaderboards and dialogs are all deliberately excluded, so the band
  is time on the field rather than time with the app open.
- **Spikes** — the things that break it up. Amber for a round ending, red for a
  disconnect or a forced relaunch, blue for the weapon select opening.
- **Tiles** — grinding time, session length, the percentage of the session
  actually in game, rounds finished and interruptions.
- **Lifetime** — grinding time carried across sessions in `config.json`.

The window starts at 5 minutes, grows with the session, and scrolls once it
passes an hour. It redraws once a second; the macro thread writes through a
lock and the UI reads an immutable snapshot.

## How it decides

It is **scene driven**, not a fixed sequence. Every poll it names the screen
from one sentinel template and acts on that. A respawn, a slow load, or landing
somewhere out of order all resolve themselves instead of wedging.

| Scene | Sentinel | Action |
| --- | --- | --- |
| Lobby | `play_button` | click Play, then wheel down hunting the FFA banner |
| Mode list | `ffa` | click the banner |
| Spectating | `join_button` | click the green "Join this game!" arrow |
| Loadout picker | `random_card` | read the active slot, pick that slot's card |
| Roblox game page | `home_play` | click the blue Play button and wait for the load |
| In a match | `health_bar` | alive: grind. dead: press Space |
| Match over | `leave_button` | **nothing** |
| Between rounds | `tohub_button` | **nothing** |
| Map vote | `mapvote_banner` | **nothing** — the other players pick |
| Disconnected | `reconnect` | click **Reconnect** |
| Connection failed | `connfail_title` | click **Cancel**, which lands on the game page |
| Anything else | — | if a match is running: jump, sweep, fire |

Across all ten captures the worst true positive is **0.908** and the loudest
false positive is **0.796**, so the 0.85 threshold has room on both sides.

**Templates must contain UI pixels only.** The first `play_button` carried 3 px
of the lobby behind it — bright sand in `tests/1.png` — and scored 0.746 in a
dark lobby, which read as "not the lobby" and stalled the macro. Both green
buttons are now cropped inside their own edges: Play scores 1.000 in both
lobby captures, and Join no longer carries any of the map behind it.

### Window focus

A click into an unfocused window is swallowed activating it. Pressing **Start**
in the control panel moves focus to the panel, so the first click the macro
made only handed focus back and did nothing.

The macro now takes the game window through the Win32 API before clicking —
`SetForegroundWindow` behind an `AttachThreadInput` attach, since Windows
refuses a bare call from a process that does not already own the foreground.
Measured at 19 ms to pull focus off another window. It finds the window by
class (`WINDOWSCLIENT`), falling back to a title match, and re-finds it if the
handle dies — leaving a game closes that window and opens another.

Focus is re-checked before every click and every 0.5 s during combat, so the
macro can never spray clicks into another app if you alt-tab. Turn it off with
the checkbox if you want the old behaviour.

### The loadout picker

The Random card is the anchor. It sits in the top-left cell on every picker
screen and matches 0.908–0.940, so the whole 5×2 grid is laid out from it and
**every click is a cell centre** — a bad template match can never fling the
cursor somewhere random. Each cell is then classified:

- **locked** — the red 🚫 cards. Redness 109–113 against ≤19 for anything else.
- **empty** — unused slots. Grey std 0.8–2.5 against ≥45 for a real card.
- **available** — everything else.

Which slot is showing is read off the highlighted box above the grid, so the
macro never assumes the stage order held. After each pick it waits for the
indicator or the grid contents to actually change before clicking again.

Picking a named weapon takes two passes: a flat 0.78 threshold inside the grid,
then a per-cell pass that accepts the best cell only when it clears 0.68 *and*
beats the runner-up by 0.15. The second pass rescues cards a neighbouring 3D
model overlaps without letting near-ties through. **Not every weapon is offered
every round** — when yours is absent it takes the Random card.

### Knowing it is actually in a match

The macro used to *infer* this: the weapon select closed, so it must be
playing. It now reads the HUD.

- **`health_bar`** on screen means a match HUD is up.
- **`respawn`** on top of that means you are dead. Without it, alive.

The combat loop only runs while alive; when dead it presses **Space** and
nothing else. (Jump spam was pressing Space anyway, which is why respawning
appeared to work — now it is deliberate.) Death is picked up by the half-second
probe, so the swinging stops promptly rather than at the next full scene read.

**Spectating draws a full match HUD**, health bar and all, so `join_button` is
checked before `health_bar` — otherwise watching a game reads as playing one
and the macro grinds at it. `random_card` goes first too, so that dying mid
weapon-select can never let grinding win over picking.

There used to be a shorter mid-match sentinel list, to save a few milliseconds
per poll. It cost two bugs: it omitted `home_play`, so a drop to the Roblox
game page mid-match was invisible, and it omitted `join_button`, which is
exactly the spectate bug above. There is now **one list**, ordered by frequency
subject to that one precedence rule, and a verify assertion pins the rule down.
Being put into spectate is also on the half-second probe, so the sweeping stops
promptly instead of at the next full read.

The health bar template is the whole widget including the `100`, which means it
matches at high health and degrades as the bar drains. That is survivable
because `in_match` is sticky: it is only cleared when a *different* screen is
positively identified, so a low-health frame does not knock the macro out of
its match.

### Asserting the loadout

Every 10 minutes in a match, the macro checks the primary you asked for is the
one actually in hand, by matching the equipped weapon's name plate at the
bottom right. If it is wrong it waits for the next death, presses **Space** to
respawn, then **M** to reopen the weapon select, and re-picks from there.

This one needed a different matching technique. The name plate is drawn over
live gameplay, so its background is whatever the map happens to be — the same
gold text reads as light-on-dark in one map and dark-on-light in another, and
plain intensity matching collapsed to 0.67 on frames where the weapon *was*
equipped. Matching on **Sobel gradients** instead ignores what shows through
and keys on the glyph shapes:

| | intensity | gradients |
| --- | --- | --- |
| equipped (three captures) | 0.64 – 1.00 | **0.84 – 1.00** |
| wrong weapon | 0.27 | **0.18** |

Gradients bring their own failure mode: a flat screen has near-zero gradient
everywhere, and one near-constant patch correlates perfectly with another, so
the weapon picker screens scored a spurious 1.000. `find_edges` rejects any
window whose edge energy is below 15% of the template's, which kills that
outright. A single miss also gets one retry a second later, since a killfeed
card can sit over the name plate.

Only the grenade launcher has a `hud_<weapon>.png` name plate so far. Any other
primary logs that the check is off and skips it — it never guesses.

### The end of the round

A round ends into a **leaderboard**, then a between-rounds lobby, then a map
vote, then the weapon select — and the game walks itself through all of it. The
macro must be silent for the whole sequence: a click on the vote screen votes
for a map, a click on the leaderboard can leave the server, and anything still
swinging when the weapon select reopens picks a weapon for you.

So all three post-match screens are recognised scenes whose action is *nothing*.
Seeing any of them also clears the in-match flag, so the unrecognised frames
between them stay silent too.

The leaderboard is spotted two ways: the normal scene read, and a cheap
bottom-right-corner probe for the Leave button that runs every 0.5 s during
combat. The probe is what actually stops the swinging in time.

**The clock is not the signal.** An earlier version watched the match timer and
stopped in the last 10 seconds, which does not work: an FFA round ends when
someone reaches the points target, so the clock rarely runs out. That hold is
still in place as a cheap extra, but the leaderboard is what the macro relies
on. The clock does still earn its keep as a liveness signal — see Recovery.

### The two Roblox dialogs

They share a layout — title, message, an outlined button on the left and a
filled one on the right — but need opposite answers:

| Dialog | Left | Right | Macro clicks |
| --- | --- | --- | --- |
| Disconnected | Leave | Reconnect | **Reconnect** |
| Connection Failed | Cancel | Retry | **Cancel** |

The Reconnect button is distinctive enough to be its own sentinel (1.000 on the
real dialog against 0.424 on the other). Cancel is not — it is the same
outlined button as Leave with a different word, and scores **0.893** against
it. So the connection-failed dialog is identified by its *title*, and Cancel is
only ever hunted inside the dialog that title already identified.

Both are also watched by the in-combat probe, in a centre-of-screen region, so
a drop is caught within half a second rather than at the next full scene read.

### Recovery

For a wedge with no dialog, liveness is measured off the clock strip: not the
digits, but a 48x12 fingerprint of those pixels. A single ones-digit tick moves
it by ~1.0 against 0.00 for an identical frame, and it does not care that only
0/1/2/5 have glyph templates — a clock reading 3:47 would otherwise come back
as `?:??` every poll and look frozen, which would have made the macro leave a
perfectly healthy match after 60 seconds.

If neither a known screen nor a clock-strip change happens for 60 s, the macro
sends **Esc, L, Enter** to leave, lands on the Roblox game page, clicks the blue
Play button, and starts over.

### Camera mode

- **relative** (default) — raw mouse deltas, which is what an FPS with a locked
  cursor reads. Clicks fire wherever the crosshair points.
- **absolute** — walks the cursor round the oval in screen coordinates and
  clicks random points in a centre box. Only useful with an unlocked cursor.

## Template scale

The templates were **not all cut from the same size capture**. Measured against
`tests/`, the weapon cards peak at scale 1.25–1.28 on a 1439-tall screen while
`ffa.png` peaks at 1.34. No single global scale serves both, so each template
carries the screen height its pixels are native to in
`templates/manifest.json`, and one global correction `k` — locked once, on the
first match — absorbs whatever your window size is. `k` is dropped and
re-swept if nothing is recognised for 30 s, so resizing mid-run recovers.

## Checking it

```
python tools/verify.py            # 130 assertions over tests/*.png
python tools/dryrun.py            # whole loop on real captures, no real input
python tools/scan.py              # what the matcher sees on your screen now
python tools/scan.py --image tests/4.png
```

`verify.py` runs 130 assertions: scene naming, click targets, grid geometry,
cell states, slot indicator, weapon selection, "any" never touching a locked
cell, stage advance, cold-starting from all twenty-one screens, clock reading,
alive/dead state, the loadout assert, and the grinding-time accounting behind
the activity graph.

No capture shows the last seconds of a round, so both harnesses **repaint the
clock pill** to synthesise 0:00 / 0:05 / 0:10 / 0:11 / 0:25 and check the hold
fires on exactly the first three. `dryrun.py` then plays a full round into a
repainted 0:05 frame and fails if any input goes out while it is showing, and
serves a frozen frame afterwards to confirm the Esc/L/Enter recovery and the
click on the game page.

### Known gaps

- **`weapon_primary_rpg` is not detectable** in `tests/4.png` (0.690, and its
  best cell wins by only 0.041). It declines and takes Random rather than
  guessing. Re-cut it from a round where it is not overlapped:
  `python tools/recut.py --image <shot>.png --cell <n> --name weapon_primary_rpg --apply`
- **`weapon_utility_medkit` and `weapon_secondary_slingshot` were re-cut** from
  `tests/7.png` and `tests/5.png`; the shipped ones scored 0.490 and 0.607
  because the card art differs. Originals are in `templates/_replaced/`. These
  are only verified against the captures they came from — that is circular, and
  the real proof is your next run.
- **`play.png` is unused.** It scores 0.632 at its best scale against
  `tests/1.png`; `play_button.png`, cut from that capture, scores 1.000.
- **`reconnect` carries a higher bar (0.90) and never sets the scale.** A plain
  dark rounded rectangle has little structure, and an uncalibrated wide sweep
  found 0.880 of it in the health bar. At the right scale a real dialog scores
  1.000 against 0.424 on the other dialog.
- **The hand-cropped `reconnect.png` was replaced.** It scored 0.730 against the
  real dialog — wrong aspect ratio — and was re-cut from `tests/disconnect.png`.
  The original is in `templates/_replaced/`.
- **The leaderboard capture is one map.** `leave_button` is cropped inside the
  button because the map behind it changes every round, but that button is
  slightly translucent, so a second capture on a different map is worth
  checking — the same way `tests/9.png` caught the Play button.

## Still open

- **Clock glyphs 3/4/6/7/8/9** have no templates, so the log often shows
  partial readings like `?:1?`. Nothing depends on it — liveness is pixel based
  and the end-of-round signal is the leaderboard.
- **Only the grenade launcher has a HUD name plate**, so the loadout assert is
  the one weapon it can check. Adding `hud_<weapon>.png`, cut from the bottom
  right of a capture with that weapon equipped, extends it.
- **The health bar template is a full-health crop.** It matches down to roughly
  75-80% health and then fades out; sticky `in_match` covers the gap, but a
  low-health capture would let it be cut more robustly.
- The **scroll-to-FFA loop** is still untested against a live list, since
  `tests/2.png` already shows the banner without scrolling.
