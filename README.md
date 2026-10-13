# AI Sponsorship Matchmaker

A social platform where people post their projects and others sponsor them. Think of a feed (Instagram-style) for
projects, Discord-style servers for teams and communities, direct messages, and a sponsorship flow:
**post → like / comment → offer to sponsor → owner accepts → chat opens**.

Built with Flask, SQLite and Flask-SocketIO (live chat).

---

## How to run it on any computer

You only need the code from GitHub. Pick **one** of the options.

### Option 1: GitHub Codespaces (nothing to install, runs in the browser)

Best for a college computer. You need a browser and your GitHub login.

1. Open the repository page on GitHub.
2. Click the green **Code** button → **Codespaces** tab → **Create codespace on main**.
3. Wait about a minute. A VS Code opens inside the browser and installs everything by itself.
4. In its terminal (bottom), run:
   ```
   python app.py
   ```
5. A pop-up says *"Your application running on port 5000 is available"*. Click **Open in Browser**.
   (If it doesn't appear: open the **Ports** tab next to the terminal and click the globe icon next to port 5000.)

The demo data is created automatically when the codespace is built, so you can log in right away (see *Demo accounts*).

### Option 2: Run it on a Windows computer

You need **Python 3.9 or newer**. Check in PowerShell:
```
python --version
```
If that fails, install Python from <https://www.python.org/downloads/> (tick **Add python.exe to PATH**; "Install for me only" needs no admin rights). Then:

```powershell
# 1. Get the code (no Git? On the repository page click Code -> Download ZIP, unzip it, and open PowerShell in that folder)
git clone https://github.com/TaehoJun-08/AI_SM.git
cd AI_SM

# 2. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1
#    If PowerShell says scripts are disabled, run this once and activate again:
#    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# 3. Install the libraries
pip install -r requirements.txt

# 4. Add the demo accounts and posts
python seed_demo.py

# 5. Start the app
python app.py
```

Open <http://127.0.0.1:5000> in a browser.

On Mac or Linux, step 2 is `python3 -m venv venv` and `source venv/bin/activate`.

---

## Demo accounts

After `python seed_demo.py` (or automatically in a codespace), log in with an email and the password `demo1234`:

| Email | Name |
|---|---|
| `maya_k@example.com` | Maya Kulkarni: has a post with 3 photos, a sponsor, a chat and a server |
| `dev_patel@example.com` | Dev Patel: sponsors Maya's project |
| `sushil_r@example.com` | Sushil Rao |
| `surbhi_m@example.com` | Surbhi Mehta: has pending sponsorship offers |

Tip for a live demo: open two different browsers (or one normal and one private window) and log in as two people,
so you can show live chat, likes and sponsorship offers going both ways.

## Things worth showing

- **Feed** with infinite scroll, double-click to like, comments, carousels for several photos/videos
- **Sponsor** a project (amount + message), edit or withdraw the offer, owner accepts in **Activity**
- **Messages**: right-click a message for Reply / Copy / Edit / Delete, attach files, emoji picker, paste a GIF link
- **Servers**: create a server, add roles, categories and private channels, invite people with a code
- **Settings**: profile photo, light / dark / system theme, accent colours, privacy, download your data
- **PDF report** of a project (⋯ menu on your own post)

## Optional settings (a `.env` file in the project folder)

```
SECRET_KEY=any-long-random-text
GIPHY_API_KEY=your_free_key_from_developers.giphy.com   # only needed for GIF *search*
```

Without a `.env` the app still starts. Pasting a GIF link into the message box always works.

## Project layout

| Folder / file | What it does |
|---|---|
| `app.py` | Starts the app and registers every part |
| `auth/` `profiles/` `settings/` | Accounts, public profiles, settings |
| `feed/` `projects/` `interests/` | Feed, posts, comments, sponsorship offers |
| `dms/` `servers/` | Direct messages and Discord-style servers (with live chat in `chat.py`) |
| `reports/` | PDF reports |
| `db/schema.sql`, `db.py` | Database tables and automatic upgrades |
| `templates/`, `static/` | Pages, styles (`css/app.css`), scripts, bundled emoji picker |
| `seed_demo.py` | Creates the demo accounts and content |
