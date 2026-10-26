# Getting started with Dexter on Windows

This guide takes you from a Windows laptop with nothing installed to Dexter's
home screen. It takes about 15 minutes. You do not need to know any Python.

When you are set up, follow [WALKTHROUGH.md](WALKTHROUGH.md) to run your first
project from planning to final approval.

**What Dexter is (and is not), in one paragraph.** Dexter is a small program
that runs on your laptop and keeps track of the work you share between
ChatGPT and Claude Code. It prepares the messages you send to each assistant,
keeps their replies, and asks for your decision at each checkpoint. It never
contacts ChatGPT, Claude Code or GitHub by itself, never spends money, and never
merges or deploys anything. Your approvals in Dexter are **SIMULATED**: they are
checked with a passphrase on your laptop, not by GitHub.

Contents: [What you need](#what-you-need) ·
[1. Install Python](#step-1-install-python) ·
[2. Download Dexter](#step-2-download-dexter) ·
[3. Start Dexter](#step-3-start-dexter-for-the-first-time) ·
[4. Make a shortcut](#step-4-optional-make-a-desktop-shortcut) ·
[Where your data lives](#where-dexter-keeps-your-data) ·
[Keeping AI tools away from your approvals](#keeping-ai-tools-away-from-your-approvals) ·
[The dashboard page](#the-read-only-dashboard-page) ·
[Updating Dexter](#updating-dexter) ·
[Troubleshooting](#troubleshooting)

## What you need

- A Windows 10 or Windows 11 laptop. You do not need administrator rights.
- An internet connection, only to download Python and Dexter.
- Accounts you already use for ChatGPT (chatgpt.com) and Claude Code
  (claude.ai/code). Dexter does not need their passwords and never connects
  to them.

## Step 1: Install Python

Dexter is written in Python, a free programming language. It needs Python 3.9 or
newer and nothing else.

1. Go to <https://www.python.org/downloads/> and click the big yellow
   **Download Python** button.
2. Open the file you downloaded.
3. **On the first screen, tick "Add python.exe to PATH"** at the bottom. This
   lets Dexter's launcher find Python.
4. Click **Install Now** and wait for "Setup was successful". Then click **Close**.

To check it worked: press the Windows key, type `cmd`, press Enter, and in the
black window type `py --version` and press Enter. You should see something like
`Python 3.13.1`. Close the window.

> If typing `python` opens the Microsoft Store, ignore the Store and use the
> python.org installer above. The Store version also works, but the python.org
> one is what this guide was written for.

## Step 2: Download Dexter

Pick **one** of these two ways.

**A. Download a ZIP (simplest).**
1. Go to <https://github.com/ronnieshorthose-cpu/dexter-sandbox>.
2. Click the green **Code** button, then **Download ZIP**.
3. Open your Downloads folder, right-click the ZIP file, choose **Properties**,
   tick **Unblock** if you see it, and click **OK**. This stops Windows asking
   about every file inside.
4. Right-click the ZIP file again, choose **Extract All...**, and extract it to
   your **Documents** folder. You now have a folder such as
   `Documents\dexter-sandbox-main`. You can rename it to `dexter-sandbox`.

**B. GitHub Desktop (easier to update later).** Install GitHub Desktop from
<https://desktop.github.com>, sign in, choose **File > Clone repository**, pick
`ronnieshorthose-cpu/dexter-sandbox`, and clone it into `Documents\GitHub`.

Either way, the folder contains a file named **`dexter.bat`**. That is the
only file you need to open.

## Step 3: Start Dexter for the first time

1. Open the `dexter-sandbox` folder and **double-click `dexter.bat`**.
   - If Windows shows "Windows protected your PC", check that the file is
     `dexter.bat` from your `dexter-sandbox` folder, click **More info**, then
     **Run anyway**. The launcher only starts Dexter with your Python; it
     downloads and installs nothing.
2. A black window opens and Dexter says **FIRST-TIME SETUP**. It shows where it
   will keep its records, for example `C:\Users\you\.dexter\coordinator.db`.
   That folder is outside the repository, so nothing you do in Dexter is ever
   committed to GitHub.
3. **Choose your owner passphrase**: at least 12 characters. Four random words
   work well, for example `copper lantern orbit tulip`. Type it, press Enter,
   then type it again. Nothing appears on screen while you type; that is normal.
   - You will type it for every owner decision: approving a task, recording an
     AI reply, accepting finished work.
   - **Never give it to ChatGPT, Claude Code or any other AI tool**, and do not
     save it in a file. Whoever knows it is the owner as far as Dexter can tell.
   - It cannot be recovered if you forget it. A password manager is a good
     place to keep it.
4. You see the home screen:

```
==============================================================================
 DEXTER  -  local project coordinator (Phase 3B)
 Database: C:\Users\you\.dexter\coordinator.db

 Owner approvals in Dexter are SIMULATED (checked with a local passphrase
 only). Nothing is sent to ChatGPT or Claude Code, and nothing on GitHub is
 approved, merged or deployed.
==============================================================================
 No projects yet. Choose [P] Projects to create your first one.
==============================================================================

What would you like to do?
  [N] Do the next step
  [P] Projects
  [T] Tasks
  [H] Handoffs (copy to an AI / paste a reply)
  [R] Reports and history
  [D] Open the read-only dashboard in your browser
  [C] Check records and back up
  [?] Help
  [Q] Quit
Choose:
```

Type a letter and press Enter. **N** (Do the next step) is almost always the
right choice: Dexter asks which task, explains where it is, and puts the
recommended step first. **B** goes back. **Q** quits; everything is saved as
you go.

## Step 4 (optional): Make a desktop shortcut

Right-click `dexter.bat`, choose **Show more options > Send to > Desktop (create
shortcut)**. Double-click the shortcut to start Dexter from now on. Keep the
real `dexter.bat` inside the `dexter-sandbox` folder: a copy on its own cannot
find the rest of Dexter.

## Where Dexter keeps your data

Everything lives in the hidden-looking `.dexter` folder in your user folder,
**outside** the repository. Dexter refuses to create any of these inside a git
folder, so they cannot be committed to GitHub by accident.

| What | Where |
|---|---|
| Database (projects, tasks, approvals, history) | `C:\Users\you\.dexter\coordinator.db` |
| Messages prepared for the assistants | `C:\Users\you\.dexter\exchange\outbox\HO-0001.txt` ... |
| Replies you paste in (deleted once recorded) | `C:\Users\you\.dexter\exchange\inbox\` |
| Saved progress reports | `C:\Users\you\.dexter\exchange\reports\` |
| The read-only dashboard page | `C:\Users\you\.dexter\exchange\dashboard.html` |
| Backups made from the menu | `C:\Users\you\.dexter\backups\` |

Your Windows user folder is private to your Windows account by default. Do not
move `.dexter` into OneDrive, Dropbox, a shared folder or the repository.

**Backups.** Choose **[C] Check records and back up**, then **Back up the
database now**. Copy backups to a USB stick you keep unplugged, not to a folder
an AI tool can read.

**The history anchor.** **[C] > Check the records** prints a line such as
`History anchor: entry 16, ee8da464268e393d`. Write it down on paper or in your
phone now and then. If a later check shows an *older* anchor, someone replaced
your database with an older copy.

## Keeping AI tools away from your approvals

Phase 3A's rule still applies, and on Windows it means:

- **Use Claude Code on the web** (<https://claude.ai/code>). It runs in the
  cloud and cannot see your laptop, your `.dexter` folder or your Dexter window.
- **Do not run an AI coding tool on your laptop under the same Windows account
  as Dexter** (for example a Claude Code command-line or desktop agent with
  access to your files). If you want one, create a separate Windows account for
  it (**Settings > Accounts > Other users**) and keep Dexter in yours.
- **Never paste your passphrase into any chat**, and never type it anywhere
  except Dexter's `Owner passphrase:` prompt.
- **Do not let an AI tool control the Dexter window.** Dexter refuses piped
  input for owner actions, but a program that can type into your window could
  type into the prompt.

## The read-only dashboard page

Choose **[D]** on the home screen. Dexter writes `dashboard.html` into your
private `.dexter\exchange` folder and opens it in your browser. It shows what is
waiting for you, what is waiting for each AI, reviews, active and blocked tasks,
and a table of every project's tasks.

- **It is a file, not a website.** There is no server, so nothing on the network
  can reach Dexter through it.
- **It has no buttons**, scripts or forms, and a security policy that stops it
  loading anything. Approvals happen only in the Dexter window.
- **It does not update by itself.** Press **[D]** again to refresh it.

## Updating Dexter

Your data is not in the `dexter-sandbox` folder, so replacing that folder is
safe.
- **ZIP:** download the new ZIP and extract it over the old folder (or into a
  new one).
- **GitHub Desktop:** click **Fetch origin**, then **Pull**.

Before updating, make a backup from the **[C]** menu.

## Troubleshooting

| What you see | What to do |
|---|---|
| "Python 3.9 or newer was not found on this computer" | Install Python as in Step 1, ticking **Add python.exe to PATH**. If Python is installed, run the installer again, choose **Modify**, and tick **Add Python to environment variables**. |
| The Microsoft Store opens | Close it and install Python from python.org (Step 1). |
| "Dexter could not find its program files next to this launcher" | You started a copy of `dexter.bat` that is not in the `dexter-sandbox` folder. Use the one inside the folder, or a shortcut to it. |
| "This Python installation is missing a security feature Dexter needs" | Install Python from python.org; some trimmed-down Python builds leave it out. |
| "owner actions need the passphrase typed into an interactive terminal" | Start Dexter by double-clicking `dexter.bat` (or from Command Prompt or Windows Terminal), not from Git Bash or another program. |
| "the owner passphrase is incorrect" | Type it again carefully. After two wrong tries Dexter makes you wait a few seconds, then longer, up to 15 minutes. To cancel an owner step, press Enter without typing anything; that never counts as a wrong try. |
| "Could not copy: ..." | The clipboard was not available. Choose **Open it in Notepad**, press Ctrl+A, then Ctrl+C. |
| Notepad does not open | Dexter prints the file's location instead. Open it in any text editor. |
| "is not UTF-8 text" after pasting a reply | In Notepad choose **File > Save As**, set **Encoding** to **UTF-8**, and save. |
| "the database is busy" | Another Dexter window is open. Close it and try again. |
| "Terminate batch job (Y/N)?" after Ctrl+C | Type `Y` and press Enter. Ctrl+C only cancels; each change is saved completely or not at all. |
| The window closes before you can read it | The launcher always waits for a key press, so this means Windows did not start it. Press the Windows key, type `cmd`, then type `cd Documents\dexter-sandbox` and `dexter.bat` to see the message. |
| "refusing to create a database inside a git working tree" | You pointed Dexter at a folder inside a repository. Use the default `.dexter` folder. |
| Forgotten passphrase | It cannot be recovered. Rename `C:\Users\you\.dexter` to `.dexter-old` (keep it for reference) and start Dexter again to set up a new one. |
| Anything else | Choose **[C] > Check the records**. If it reports problems, restore the newest backup (see "Recovering from errors" in [COORDINATOR.md](COORDINATOR.md)). |

**Using the command line instead.** Everything in the menu is also available as
a command. In Command Prompt, from the `dexter-sandbox` folder:
`py -m dexter_coordinator start` opens the menu,
`py -m dexter_coordinator dashboard` prints the home screen, and
`py -m dexter_coordinator --help` lists every command. See
[COORDINATOR.md](COORDINATOR.md).
