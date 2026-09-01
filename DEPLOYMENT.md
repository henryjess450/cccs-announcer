# Installing the CCCS Announcer

For the person setting this up. You do not need to know Python. Most of it is
one double-click.

**What you are installing:** one program, on one Windows 10 or 11 computer that
is wired into the PA amplifier. Staff open a web page on their own computers,
type an announcement, and this machine speaks it out of the speakers.

**About 20 minutes**, most of it waiting for downloads.

---

## Contents

- [Before you start](#before-you-start)
- [Step 1 — Plug the audio in](#step-1--plug-the-audio-in)
- [Step 2 — Download and run ANNOUNCER.bat](#step-2--download-and-run-announcerbat)
- [Step 3 — Sign in and claim the admin account](#step-3--sign-in-and-claim-the-admin-account)
- [Step 4 — Make it switch on and sign in by itself](#step-4--make-it-switch-on-and-sign-in-by-itself)
- [Step 5 — Pull the plug and check](#step-5--pull-the-plug-and-check)
- [Giving staff the address](#giving-staff-the-address)
- [Adding staff accounts](#adding-staff-accounts)
- [Backups](#backups)
- [When it stops working](#when-it-stops-working)
- [Upgrading](#upgrading)
- [Doing it by hand](#doing-it-by-hand)

---

## Before you start

- **A Windows 10 or 11 computer** that can stay switched on permanently, in the
  same room as the PA amplifier. It does not need to be fast.
- **A wired network connection.** Wi-Fi drops out; announcements should not.
- **An audio cable** from the computer to the amplifier — usually 3.5 mm jack
  to two RCA plugs.
- **Internet access on that computer, once**, for setup. After that the
  announcer works entirely on the school network with no internet at all. (No
  announcement text ever leaves the building — the voice runs locally.)
- **A dedicated Windows account** on that machine, called something like `pa`.
  Make it a **standard user, not an administrator**. Sign in as that account
  before you start.
- Somewhere **physically secure** to put the machine. Anyone with a keyboard in
  front of it can talk to the whole school.

---

## Step 1 — Plug the audio in

1. Cable from the computer's **headphone / line-out** socket to a **spare
   line-level input** on the amplifier.
2. If the amplifier has a **paging** or **priority** input, use that — it
   usually ducks background music automatically.
3. **Not** a microphone input. Line level into a mic input is distorted and far
   too loud.
4. Leave the amplifier's volume low for now.

> If the computer's headphone socket hums, use a cheap **USB audio interface**
> instead. It usually sounds better and isolates the ground.

---

## Step 2 — Download and run ANNOUNCER.bat

**Download one file: `ANNOUNCER.bat`.**

1. On the PA machine, open
   <https://github.com/henryjess450/cccs-announcer> and sign in to GitHub.
   (The repository is private, so you have to be signed in to see it.)
2. Click **`ANNOUNCER.bat`** in the file list, then the **download** button
   (the ⤓ icon, top right of the file view).
3. **Double-click the downloaded file.**

That is it. It downloads the rest of the announcer into `C:\announcer`,
installs everything, and starts up. It asks you to sign in to GitHub once along
the way, and Windows will ask for permission once or twice — say yes.

> If a standard (non-administrator) account cannot create `C:\announcer`, it
> uses a folder in your own profile instead and tells you which. Either is
> fine; just use the path it prints from then on.

Once it has downloaded, **run `ANNOUNCER.bat` from that folder** in future, not
the one in Downloads. You can delete the downloaded copy.

The first run:

1. installs Python if it is missing
2. sets the announcer up
3. downloads the speech engine and voice (about 85 MB)
4. creates the database and the chimes
5. makes the announcer start whenever this account signs in
6. opens the firewall so staff computers can reach it
7. links the folder to the code repository, so it can pull fixes by itself

Then it starts. The black window stays open — that window *is* the announcer,
and closing it stops announcements. It prints **the address staff will use**:

```
        http://192.168.1.42:8080
```

**Write that down.** It is what goes on the sticky note in the office.

Every time after this, double-clicking `ANNOUNCER.bat` **pulls the latest code
and starts the announcer** — the setup part is skipped. It is safe to run
whenever you like.

Pulling updates can never stop the announcer starting. With no internet, or if
the GitHub sign-in has expired, it prints one line and carries on with the code
already there. That matters: a 3 AM reboot with the network down still has to
end with announcements working at 8 AM.

The window also shows **who can sign in as an administrator** and both
addresses — the local one staff use, and the school's internet address, which
is **not** the one to use.

### If the download does not work

Get the whole thing as a ZIP instead:

1. Open <https://github.com/henryjess450/cccs-announcer> (signed in).
2. Green **Code** button → **Download ZIP**.
3. Unzip it to `C:\announcer`.
4. Double-click `C:\announcer\ANNOUNCER.bat`.

---

## Step 3 — Sign in and claim the admin account

The first time the announcer starts it makes an administrator account for you
and shows you the password in that black window:

```
  ==============================================================
   FIRST-TIME SETUP

   An administrator account has been created for you.

      Username:  admin
      Password:  bxfx-orkk-gtqo-uyke-45
  ==============================================================
```

It is also saved in `C:\announcer\data\FIRST-LOGIN.txt`, in case the window has
scrolled.

1. On this computer, open a browser and go to <http://localhost:8080>.
2. Sign in as **`admin`** with that password.
3. You are asked to **set up your account** — your full name, the username you
   want, and a password only you know. Fill it in and save.
   - The **full name** is what the school sees against every announcement you
     make. Use a real one: `Henry Jess`, not `Office`.
   - `admin` and its password stop working the moment you do this, and
     `FIRST-LOGIN.txt` deletes itself.
4. Click **Check the speakers**. A chime should come out of the PA. This is
   only a chime, not an announcement, so it is safe to press during the school
   day.
5. Type a test announcement and send it.

**Nothing came out?** → [When it stops working](#when-it-stops-working).

### Set the levels while you are here

- Windows volume to about **70%**. Leave headroom; 100% often clips.
- Turn the **amplifier** up until an announcement is clearly audible in the
  furthest hallway, then back off slightly.
- In Windows Sound settings, open the device's properties and turn **off** all
  "enhancements" and "spatial sound" — they mangle speech — and turn **off**
  "Allow applications to take exclusive control", so nothing can steal the
  speakers from the announcer.
- Chime too startling? Lower `PA_CHIME_GAIN` in `C:\announcer\.env` (try
  `0.30`) and restart.
- Voice too fast for an echoey hallway? Raise `PIPER_LENGTH_SCALE` to `1.15`.
  Higher is slower.

---

## Step 4 — Make it switch on and sign in by itself

The goal: a power cut at 3 AM does not become a problem at 8 AM.

Three things have to be true. **Setup already did the first two.** The third is
a firmware setting, and no program is allowed to change it.

| | What | Done by |
|---|---|---|
| 1 | A task starts the announcer when the account signs in | Setup |
| 2 | Windows signs in to that account by itself | Setup |
| 3 | The computer switches on when power comes back | **You, in the BIOS** |

To see where you stand, double-click **`check-autostart.bat`**. It reports all
three and offers to fix 1 and 2 if anything is missing.

### The BIOS setting — you have to do this one

1. Restart the computer and press the BIOS key as it starts — usually **DEL**
   or **F2** (the screen normally says).
2. Find **"Restore on AC Power Loss"**, **"After Power Failure"**, or **"AC
   Back"**. Usually under Power, ACPI, or Advanced.
3. Set it to **Power On**.
4. Save and exit.

Without this the machine stays off after a power cut until somebody notices.

### About the automatic sign-in

Setup uses **Autologon**, a Microsoft tool, which stores the password as an
encrypted LSA secret rather than as plain text in the registry. You type the
password into that Microsoft tool; nothing here saves or sends it.

> **Why the announcer needs a signed-in desktop at all:** a Windows *service*
> runs in what Windows calls Session 0, which has **no access to the sound
> card**. Installed as a service it would start perfectly and never make a
> sound.

If setup could not do it, do it by hand: press the Windows key, type
**`netplwiz`**, press Enter, and untick "Users must enter a user name and
password to use this computer".

> **No tick box?** Recent Windows hides it. Press Windows+R, type `regedit`,
> and at
> `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device`
> set **`DevicePasswordLessBuildVersion`** to `0`. Then try `netplwiz` again.

### Stop it going to sleep

- Settings → System → Power → **Sleep: Never**. (Screen off is fine.)
- Control Panel → Power Options → Change advanced settings:
  - **USB selective suspend → Disabled** — this one matters if you used a USB
    audio interface. Windows will otherwise power it down overnight and the
    speakers go dead until someone unplugs and replugs it.
  - **Turn off hard disk after → Never**.

### Then lock the screen

Automatic sign-in leaves a signed-in Windows session running, so **anyone who
can reach the keyboard is inside that account**. Handle it:

- **Locking the screen does not stop the announcer or the audio** — the session
  keeps running and announcements keep playing. So there is no reason to leave
  it unlocked. Press **Windows+L**, or set a screen saver with "On resume,
  display logon screen".
- Keep the account a **standard user**, not an administrator.
- Do not sign into email or cloud storage with it, and do not map network
  drives to it.
- Keep the machine in a locked cupboard or the equipment closet.

---

## Step 5 — Pull the plug and check

Do not skip this. It is the whole point of step 4.

1. **Pull the power cable out.** Wait ten seconds. Plug it back in.
2. The computer should switch itself on and sign itself in. Do not touch it.
3. Wait two minutes.
4. From **another** computer, open the address from step 2.
5. Sign in. It should say **Idle**. Press **Check the speakers**.

If you hear the chime, the system will be there on Monday morning.

---

## Giving staff the address

The address is the machine's address **on the school network** — something like
`http://192.168.1.42:8080`. To see it again at any time, double-click
**`show-address.bat`** on the PA machine.

Two things worth doing:

1. **Stop the address changing.** Give the machine a DHCP reservation on its
   MAC address, or a static IP. If the address changes, every sticky note in
   the school is wrong.
2. **Give it a name** in DNS if you can — `announce.yourschool.local` is much
   easier for staff than a string of numbers.

> **This is a local address, and that is the one you want.** It is not the
> school's public internet address, and the announcer should not be reachable
> from the internet at all — anything out there that can reach it can try to
> talk to the whole school. Keep it on the **staff network**, not the student
> VLAN and not guest Wi-Fi. Staff passwords travel unencrypted over plain
> `http` on the LAN, which is an acceptable trade on a wired staff network and
> not one anywhere else.
>
> `show-address.bat --public` will show the school's internet address if you
> ever need it for something else. It is not needed here.

---

## Adding staff accounts

Sign in as an administrator and click **Admin** in the top right.

1. Type a username and full name, choose **Staff** or **Administrator**, click
   **Add account**.
2. A password appears in a green box. **Give it to that person now** — it
   cannot be shown again.
3. The first time they sign in they choose their own password. Until they do,
   they cannot make announcements.

| Button | What it does |
|---|---|
| **Reset password** | New password, and signs them out everywhere immediately |
| **Turn off** | Stops the account signing in, ends its sessions at once. Use when someone leaves |
| **Unlock** | Clears a lockout after too many wrong passwords |
| **Make admin / Make staff** | Changes what they can do |

The same page shows the **announcement log** — every announcement, who sent it,
what was actually spoken, what happened to it — and a **sign-in trail**.

### Clearing the list

There are two different "clears", and they do different things:

| Where | What it does |
|---|---|
| **Clear** on the announcement page, next to *Recently sent* | Hides that list **on that computer only**. Nothing is deleted. Handy after testing. Other people's screens are unaffected, and the log keeps everything. |
| **Clear** on the Admin page, under the announcement log | **Permanently deletes** announcement records. Administrators only. Anything still waiting or playing is kept. |

Clearing the log from the Admin page is itself recorded in the sign-in trail,
with your name and how many records went. The log is the main thing that makes
every announcement attributable, so emptying it always leaves a mark.

Worth knowing:

- **Staff can stop their own announcements. Administrators can stop anyone's.**
- **Administrators are not rate-limited.** Staff are: 5 announcements per 10
  minutes, which is about stopping a stuck key rather than stopping misuse.
- **The last administrator cannot be turned off or demoted.** Locking everyone
  out of administration is not a mistake you can undo from the browser.
- **Sessions expire after 30 minutes idle**, so a staff computer left logged in
  is not an open microphone.
- **Show new staff the Preview button.** It plays through their own computer's
  speakers and never touches the PA. It is the difference between a
  mispronounced surname being heard by one person and by four hundred.

---

## Backups

Everything that matters is in **one folder**: `C:\announcer\data`. It holds the
database (every account and every announcement ever made), the chimes, and the
logs. Include it in your normal file backup — once a night is plenty.

To copy it by hand while the announcer is running, take the whole `data`
folder, including all three database files if present:

```
data\announcer.sqlite3
data\announcer.sqlite3-wal
data\announcer.sqlite3-shm
```

To restore: stop the announcer, replace the folder, start it again.

---

## When it stops working

**Check these in order.** The first three cover almost everything.

### First: is it running?

On the PA machine, look for the black announcer window. If it is not there:

- Double-click `ANNOUNCER.bat`, or open Task Scheduler, find
  **CCCS Announcer**, right-click → **Run**.
- If the machine is sitting at a sign-in screen, the automatic sign-in
  (step 4B) has come undone. Redo it.

### Second: what does the health page say?

From any computer, open:

```
http://<address>:8080/health
```

You will get a block of text. Look for:

| What it says | What it means | What to do |
|---|---|---|
| `"status": "ok"` | Everything is fine | The problem is elsewhere — check the amplifier |
| `"audio": {"ok": false ...}` | It cannot reach the speakers | See below |
| `"tts": {"ok": false ...}` | It cannot produce speech | See below |
| `"database": {"writable": false}` | The disk is full or read-only | Free up disk space |
| Page will not load at all | The announcer is not running | Go back to "is it running?" |

The `detail` field next to whichever one failed says exactly what is wrong, in
technical terms. That is the line to read, and the line to quote if you need
help.

### It cannot reach the speakers

- Is the audio cable still plugged in at both ends?
- Is the amplifier switched on?
- Did the USB audio interface get unplugged, or move to a different port?
- Run `.venv\Scripts\python.exe scripts\list_audio_devices.py` again. If the
  device name has changed, update `PA_AUDIO_DEVICE` in `.env` and restart.
- Did Windows Update change the default playback device? It does that.

**Announcements are not lost while this is broken.** They are held in the queue
and play as soon as the speakers come back. Staff see a red banner saying so.

### "MSVCP140.dll was not found", or the voice never works

Piper is built with Microsoft's compiler and needs the **Microsoft Visual C++
Runtime**, which Windows 10 does not always have. Without it `piper.exe` cannot
start at all.

**Easiest fix:** double-click `ANNOUNCER.bat`. It notices the runtime is
missing and offers to install it. (It gives up waiting after 15 seconds and
carries on, so it never blocks an unattended restart.)

**Or by hand**, in PowerShell. These two work from any folder:

```
iwr https://aka.ms/vs/17/release/vc_redist.x64.exe -OutFile "$env:TEMP\vc.exe"
Start-Process -Wait "$env:TEMP\vc.exe" -ArgumentList '/install','/quiet','/norestart'
```

Then restart the announcer.

`/health` reports this before anyone tries to announce: the `tts` section says
"missing a Windows component".

### It cannot produce speech

- Check `C:\announcer\voices` still contains **both** the `.onnx` file and the
  `.onnx.json` file.
- Check `C:\announcer\piper\piper.exe` is still there, with its supporting
  files next to it.
- If you moved Piper or the voice out of `C:\announcer\piper` and
  `C:\announcer\voices`, put them back — the announcer finds them there
  by itself.
- If antivirus quarantined `piper.exe`, restore it and add an exclusion for
  `C:\announcer`.

### "The announcer is already running on this computer"

Exactly what it says: a second copy tried to start. Two copies would talk over
each other on the speakers, so the second one refuses. This is correct
behaviour, not a fault. Use the copy that is already running. If you are
certain nothing is running, restart the machine.

### It hums or buzzes instead of speaking

The announcer is running on its **test voice**, which is a tone rather than
speech. Look at the announcement page: there will be a yellow **Test mode**
banner across the top saying so.

Open `C:\announcer\.env` and check:

```
PA_TTS_ENGINE=piper
```

If it says `mock`, change it to `piper`, then restart the announcer.

### The chime is right but there is silence where the words should be

The chime comes from a file on disk and the speech comes from Piper, so a
working chime with no speech means Piper specifically has failed. Check
`/health` — the `tts` section will say why — and see "It cannot produce speech" above.

### Someone wants a different chime

There is deliberately no chime chooser: every announcement uses the same one,
so staff have one less decision to make in a hurry. To change it for the whole
school, set `PA_DEFAULT_CHIME` in `.env` to one of `two_tone_bell`,
`attention`, `soft_alert`, or `urgent`, and restart. Play each one first with
**Check the speakers** after changing it.

### Somebody cannot sign in

| What they see | What it means | What to do |
|---|---|---|
| "That username or password is not right." | Wrong username **or** wrong password — the message is deliberately vague so it cannot be used to find out which accounts exist | Check the username on the admin page, then **Reset password** |
| "This account is locked…" | Too many wrong passwords | Admin page → **Unlock**. It also clears itself after 5 minutes |
| "This account has been turned off." | Somebody deactivated it | Admin page → **Turn on** |
| "Please sign in again." appearing repeatedly | Their session expired while the page was open | Normal after 30 minutes idle. Sign in again |
| The sign-in page will not accept anything at all | `PA_SESSION_COOKIE_SECURE=true` while serving plain http | Set it to `false` in `.env` and restart |

### It says first-time setup is not finished

`/health` reports `degraded` with `"setup_pending": true` whenever the starting
`admin` account is still on the password the announcer issued. Sign in as
`admin` and complete the setup screen. Until somebody does, that account cannot
make announcements or manage anything — but it is still an account with a
password sitting on the network, so do not leave it.

### I lost the first-time password

If nobody has signed in yet, look in `C:\announcer\data\FIRST-LOGIN.txt`.
If that file is gone, the account has already been set up — use the username
somebody chose. If nothing works, use the command line below.

### It did not come back on after a restart or a power cut

Double-click **`check-autostart.bat`** on the PA machine. It tells you which of
the three requirements is not met and offers to fix the two it can:

```
   [ok]   1. A task starts the announcer when pa signs in.
   [--]   2. Windows stops at the sign-in screen, so nothing starts.
   [??]   3. The BIOS must switch the computer on when power comes back.
```

A `[--]` on 1 or 2 is fixable from that screen. A machine that stays switched
**off** entirely after a power cut is number 3, and that is a BIOS setting —
see step 4.

### Nobody at all can sign in

Use the PA machine's keyboard:

```
.venv\Scripts\python.exe scripts\manage_users.py list
.venv\Scripts\python.exe scripts\manage_users.py reset <username>
```

If `list` shows no administrator, make one:

```
.venv\Scripts\python.exe scripts\manage_users.py add --admin
```

`http://<address>:8080/health` also reports this: `accounts.ok` goes to
`false` and the whole health check goes to `degraded` when there is no active
administrator.

### Someone says "it says I have sent too many"

That is the rate limit: 5 announcements per 10 minutes for staff. The message
tells them how long to wait and to ask the office if it is urgent.
Administrators are never limited. To change it, set `PA_RATE_LIMIT_COUNT` and
`PA_RATE_LIMIT_WINDOW_SECONDS` in `.env` and restart.

### Someone wants to check a name before announcing it

That is what **Preview in my browser** is for. It plays through their own
computer's speakers and never touches the PA. Worth showing every new staff
member — it is the difference between a mispronounced surname being heard by
one person and by four hundred.

### "The argument 'scripts\...' to the -File parameter does not exist"

You are in the wrong folder. Anything starting `scripts\` is relative to the
announcer folder, so change to it first:

```
cd C:\announcer
```

If the announcer went into your own profile instead (which happens when the
account cannot write to the root of `C:`), it is:

```
cd %USERPROFILE%\announcer
```

The window the announcer runs in prints its own folder at the top, and
`ANNOUNCER.bat` sets the folder for you — which is why almost everything is
better done by double-clicking that instead.

### What address do I give staff?

Double-click **`show-address.bat`** on the PA machine. It prints the address
without needing the announcer to be running.

If it says the computer is not on a network, the network cable is out.

### Updates are not being pulled

The window says so when it starts — "could not check", "git is not installed",
or "not linked to the code repository". None of these stop the announcer
working; they only mean it is running the code already on the machine.

To fix it, run setup again on the PA machine. **Change to the announcer folder
first** — the command below uses a path relative to it:

```
cd C:\announcer
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

It will offer to install Git and sign in to GitHub.

### Staff say it worked yesterday and not today

Have one of them press **Ctrl + F5** on the announcement page. If an upgrade
went in, that forces their browser to fetch the new page.

### Reading the log

```
C:\announcer\data\logs\announcer.log
```

One line per event, newest at the bottom. It rotates automatically and keeps
the last ten files, so it will not fill the disk. Every failure is in there
with the technical reason.

---

---

## Upgrading

**Normally there is nothing to do.** `ANNOUNCER.bat` pulls the latest code
every time it starts, so restarting the announcer updates it.

To force it now: close the announcer window and double-click `ANNOUNCER.bat`.

Your settings and data are never touched by an update — `.env`, the database,
the logs, the chimes, Piper and the voice are all outside what the update
replaces.

If updates are turned off (no Git, or the folder was copied rather than
linked), upgrade by hand:

1. Stop the announcer.
2. **Back up `C:\announcer\data`.**
3. Replace the program files, **keeping** your `.env` and `data` folder.
4. Double-click `ANNOUNCER.bat` — it skips everything already installed.

---

## Doing it by hand

`ANNOUNCER.bat` does all of this for you. It is written down in case it fails, or
the machine has no internet.

**Python** — <https://www.python.org/downloads/windows/>, 3.11 or newer. Tick
**"Add python.exe to PATH"** on the first screen.

**The announcer**, from a Command Prompt in `C:\announcer`:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\seed.py
```

**The speech engine** — download `piper_windows_amd64.zip` from
<https://github.com/rhasspy/piper/releases> and unzip it so you have
`C:\announcer\piper\piper.exe`. Keep the files next to it; Piper needs them.

**The voice** — from
<https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US>, download
**both** files for `en_US-lessac-medium` (the `.onnx` and the `.onnx.json`) into
`C:\announcer\voices`. They must sit beside each other.

> No internet on the PA machine? Download these on another computer and bring
> them over on a USB stick. Nothing phones home afterwards.

The announcer finds `piper\piper.exe` and the voice in `voices\` by itself, so
there is nothing to configure.

**Start at sign-in** (from the announcer folder):

```
cd C:\announcer
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
```

**Firewall**, in an administrator PowerShell:

```
New-NetFirewallRule -DisplayName "CCCS Announcer" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Domain,Private
```

**The first administrator**, if you would rather not use the browser:

```
.venv\Scripts\python.exe scripts\manage_users.py add --admin
```

---

## Quick reference

| | |
|---|---|
| Program folder | `C:\announcer` |
| Settings (optional) | `C:\announcer\.env` — all settings: `.env.example.full` |
| Everything to back up | `C:\announcer\data` |
| Log | `C:\announcer\data\logs\announcer.log` |
| First-time password | `C:\announcer\data\FIRST-LOGIN.txt` (deletes itself after setup) |
| Set up AND start (the only file you need) | `ANNOUNCER.bat` |
| See the staff address | `show-address.bat` |
| Check it restarts on its own | `check-autostart.bat` |
| Health check | `<address>/health` |
| Admin page | `<address>/admin` |
| Startup task name | `CCCS Announcer` (Task Scheduler) |
| List accounts | `.venv\Scripts\python.exe scripts\manage_users.py list` |
| Reset a password | `.venv\Scripts\python.exe scripts\manage_users.py reset <username>` |
| List audio devices | `.venv\Scripts\python.exe scripts\list_audio_devices.py` |
