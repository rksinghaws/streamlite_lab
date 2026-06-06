# Server Agent (Streamlit)

A small Streamlit web UI to query servers and gather basic diagnostics. The app can run recommended checks via SSH (Paramiko) or AWS SSM (boto3) and shows results in the web UI.

**Key files**
- [app.py](app.py): main Streamlit application.
- [servers.json](servers.json): local server inventory (name, instance_id, host, ssh_user, ssh_key).
- [requirements.txt](requirements.txt): Python dependencies.
- backups/: timestamped copies created during maintenance.

**Purpose**
- Ask questions about a server (network, health, logs) and get a technical summary and recommended commands.
- Automatically run recommended commands over SSH when `host` is present and `paramiko` is available.
- Optionally run commands via SSM when `instance_id` is present and `boto3` + AWS credentials are available.

Prerequisites
- Python 3.8+ (3.9 in this workspace).
- Virtual environment (recommended).
- Network/SSH access to target hosts or AWS credentials for SSM.

Quick setup
1. Create and activate a virtualenv inside the project:

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

Run the app

```bash
source .venv/bin/activate
streamlit run app.py
```

Open: http://localhost:8501 (or the Network URL printed by Streamlit).

Servers inventory
- Edit `servers.json` to add servers. Each entry supports:
  - `name` (display name)
  - `instance_id` (for AWS SSM RunCommand)
  - `host` (IP or hostname for SSH)
  - `ssh_user` (SSH username)
  - `ssh_key` (path to private key on the Streamlit host)

Behavior: SSH vs SSM
- SSH path: used when a server entry has `host` and `paramiko` is installed. The app will attempt to run recommended commands automatically and display Stdout/Stderr.
- SSM path: used when a server entry has `instance_id` and `boto3` is installed; requires AWS credentials and SSM agent on the instance.
- If both are present, both capabilities are available; the UI prioritizes showing SSH results if `host` is present and paramiko is available.

Backups & Restore points
- Local backups are saved under `backups/` with timestamps. Example: `backups/servers.json.2026-06-06-1934.json`.
- A git restore point was created and pushed. Branch: `restore/2026-06-06-1934`. Tag created: `restore-point-2026-06-06-1934`.

To restore:
```bash
# checkout restore branch
git checkout restore/2026-06-06-1934
# or checkout the commit/tag
git checkout restore-point-2026-06-06-1934
```

Logs
- The app writes captured SSH/SSM outputs to `streamlit.log` in the project root.

Security notes
- Do NOT commit private keys into the repository. Use local key paths or paste key text temporarily in the UI.
- Ensure `ssh_key` file is readable only by the process user (chmod 600).
- If using SSM, provide only the minimal AWS permissions required (ssm:SendCommand, ssm:GetCommandInvocation, iam as needed).

Troubleshooting
- If widgets fail to render, ensure `st.set_page_config` is called before other Streamlit calls in `app.py`.
- If you see `st.session_state has no key "aws_region"`, re-run the app; `ensure_session()` initializes `aws_region` to `us-east-1`.
- If SSH auto-run fails, check `streamlit.log` and verify key path and network accessibility.

Contributing / Development notes
- Keep UI changes in `app.py`; small helper functions live in the same file for now.
- When making edits, create a branch and push. Use the restore branch/tag if you need to roll back.

If you want, I can:
- Add a CI-friendly `requirements-dev.txt` and a simple test harness.
- Add a safer key management flow (file upload + in-memory use), or mask key paths in UI.

