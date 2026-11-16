# Deploying AgriCredit Agent

There are two ways to run AgriCredit Agent for real users. Both use the same code, the same real
Benin open data and the same officer sign-in.

| | Public demo (Hugging Face Space) | Lender install (Docker on the lender's computer) |
|---|---|---|
| Who it is for | Judges, investors, partners trying it | A microfinance institution, cooperative or extension office |
| Where it runs | A free Hugging Face server | The lender's own office computer |
| Farmer data | Sample cases only | The lender's data, which never leaves the computer |
| Qwen 2.5 7B | Runs, but slowly on the free server; a Fast mode gives the same decision in seconds | Runs privately; about 16 GB of memory recommended |
| Cost | Free | The lender's computer; no subscription |

Both deployments require a sign-in. Every approval is recorded under the name of the officer who
is signed in.

---

## A. Public demo for judges (Hugging Face Space)

The Space contains only two files, `deploy/huggingface/Dockerfile` and `deploy/huggingface/README.md`.
When it builds, it downloads this repository from GitHub (branch `deploy` by default; change
`ARG REF=` in the Dockerfile to `main` after you merge), builds the real-data store and bakes the Qwen
model into the image.

1. Create a free account on https://huggingface.co.
2. Click your profile picture, then **New Space**.
   - Space name: `agricredit-agent`
   - License: MIT
   - SDK: **Docker**, template **Blank**
   - Hardware: **CPU basic (free)**
   - Visibility: **Public**
   - Click **Create Space**.
3. In the new Space, open the **Files** tab, then **Add file > Upload files**. Upload
   `deploy/huggingface/Dockerfile` and `deploy/huggingface/README.md` from this repository. Upload
   the README last: it replaces the one Hugging Face created. Click **Commit**.
4. Open **Settings > Variables and secrets > New secret**.
   - Name: `AGRICREDIT_BOOTSTRAP_USERS`
   - Value: `judge:CHOOSE-A-PASSWORD:OFF-001` (at least 10 characters for the password).
     Add more accounts separated by commas, for example `judge:...:OFF-001,investor:...:OFF-002`.
5. Open the **App** tab. The first build takes about 15 to 30 minutes (it downloads Qwen, about
   4.7 GB). When it says **Running**, the sign-in page appears.
6. Give the judges the direct link `https://YOUR-HF-USERNAME-agricredit-agent.hf.space`, the
   username `judge` and the password you chose.

Notes:
- A free Space goes to sleep after a long time without visitors. The next visitor wakes it, which
  takes a minute or two. Open it yourself before the judging session.
- The free server has 2 CPU cores, so Qwen takes several minutes per case. The sidebar lets each
  visitor choose **Fast mode**: the evidence, figures, recommendation and safety checks are
  identical; only the wording of the explanation comes from a fixed template instead of Qwen.
- For fast Qwen, upgrade the Space hardware (**Settings > Space hardware**, for example a small GPU).
  Paid hardware is billed by the hour: check the price shown there and set a short **Sleep time**.
- Data on the free Space is reset when it restarts. That is fine for a demo; use the lender install
  for real work.
- After you push changes to GitHub, update the demo with **Settings > Factory rebuild**.

### Optional: your own address, such as demo.yourdomain.com (Namecheap)

This keeps your domain where it is, on Namecheap; nothing moves to another provider.

1. Sign in to Namecheap, go to **Domain List**, and click **Manage** next to your domain.
2. Open **Advanced DNS**, then **Add New Record**.
3. Type: **URL Redirect Record**. Host: `demo`. Value: your Space link,
   `https://YOUR-HF-USERNAME-agricredit-agent.hf.space`. Choose **Permanent (301)**. Save.
4. After up to 30 minutes, `demo.yourdomain.com` forwards visitors to the demo.

Test it yourself before sharing it. If a browser shows a security warning for the `https://`
version of the short address, share the `hf.space` link instead.

---

## B. Lender install (Docker on the lender's computer)

Everything runs on one computer in the lender's office: the dashboard, the Qwen model and the
database. Officers use a web browser on that computer or on the office network.

### What the computer needs

- Windows 10/11, macOS or Linux, with **Docker Desktop** (Windows/macOS) or Docker Engine (Linux).
- 16 GB of memory recommended for Qwen 2.5 7B (8 GB works with AI writing turned off).
- About 15 GB of free disk space.
- Internet for the first installation only (to download the software and the model).

### Install

In a terminal (on Windows, the Ubuntu app if WSL is installed, or PowerShell):

```bash
git clone https://github.com/amonyepraise-hub/agricredit-agent.git
cd agricredit-agent
docker compose up -d --build
```

The first start downloads the Qwen model (about 4.7 GB). Watch progress with:

```bash
docker compose logs -f app
```

When the log says `officer dashboard on port 8501`, press Ctrl+C to stop watching (the app keeps
running) and create the first officer account:

```bash
docker compose exec app python -m agricredit.auth add houngbo --officer-id OFF-101 --name "Afi Houngbo"
```

It asks for a password twice (at least 10 characters; nothing is shown while typing).

Open http://localhost:8501 and sign in.

### Everyday use

| Task | Command |
|---|---|
| Stop | `docker compose down` (all data is kept) |
| Start again | `docker compose up -d` |
| Add an officer | `docker compose exec app python -m agricredit.auth add USERNAME --officer-id OFF-102 --name "Full Name"` |
| Change a password | `docker compose exec app python -m agricredit.auth passwd USERNAME` |
| Disable an officer | `docker compose exec app python -m agricredit.auth disable USERNAME` |
| List accounts | `docker compose exec app python -m agricredit.auth list` |
| Update the software | `git pull` then `docker compose up -d --build` |
| Back up all data | `docker run --rm -v agricredit_agricredit-data:/data -v "$PWD":/backup alpine tar czf /backup/agricredit-backup.tgz -C /data .` |

Officers on the same office network open `http://COMPUTER-IP:8501`. Find the computer's IP with
`ipconfig` (Windows) or `ip addr` (Linux).

### Settings

Copy `deploy/lender.env.example` to `.env` in the same folder and edit it, then run
`docker compose up -d`. You can set the explanation language (French by default), the port,
the model, or turn AI writing off.

### What is stored where

All lasting data is in two Docker volumes, kept when the app is stopped or updated:

- `agricredit-data`: the database (cases, advisories, approvals, officers), officer accounts
  (hashed passwords only), the evidence-signing key, and the audit logs.
- `ollama-models`: the downloaded Qwen model.

---

## Security notes

- Passwords are stored only as salted PBKDF2-SHA256 hashes (600,000 iterations).
- After five wrong passwords, sign-in for that browser session waits before allowing another try.
- The evidence-signing key is created on first start, inside the data volume, and never leaves it.
- Error details are hidden from users; the full logs stay on the server.
- The released advice goes to a sandbox outbox. Connecting a real SMS gateway is a separate step.
- To reach the lender install from outside the office, use a secure tunnel or VPN (for example
  Cloudflare Tunnel or Tailscale), not an open port on the router.
