import streamlit as st
import logging
import re
import time
import json
from pathlib import Path

SERVERS_FILE = Path(__file__).parent / "servers.json"

try:
	import boto3
	from botocore.exceptions import BotoCoreError, ClientError
except Exception:
	boto3 = None
	BotoCoreError = ClientError = Exception

try:
	import paramiko
except Exception:
	paramiko = None

# configure a simple file logger so SSH/SSM outputs are captured in streamlit.log
logger = logging.getLogger("server_agent")
if not logger.handlers:
	handler = logging.FileHandler("streamlit.log")
	handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
	logger.addHandler(handler)
	logger.setLevel(logging.INFO)


def ensure_session():
	if "servers" not in st.session_state:
		# load servers from file if present, else use defaults
		if SERVERS_FILE.exists():
			try:
				with open(SERVERS_FILE, "r") as f:
					st.session_state.servers = json.load(f)
			except Exception:
				st.session_state.servers = [
					{"name": "server-01", "instance_id": "i-0123456789abcdef0", "host": "", "ssh_user": "", "ssh_key": None},
					{"name": "server-02", "instance_id": "i-0fedcba9876543210", "host": "", "ssh_user": "", "ssh_key": None},
				]
		else:
			st.session_state.servers = [
				{"name": "server-01", "instance_id": "i-0123456789abcdef0", "host": "", "ssh_user": "", "ssh_key": None},
				{"name": "server-02", "instance_id": "i-0fedcba9876543210", "host": "", "ssh_user": "", "ssh_key": None},
			]

	# ensure aws_region is initialized to avoid KeyError when referenced in UI
	if "aws_region" not in st.session_state:
		st.session_state.aws_region = "us-east-1"


def save_servers():
	try:
		with open(SERVERS_FILE, "w") as f:
			json.dump(st.session_state.servers, f, indent=2)
	except Exception:
		pass

	# ensure aws_region is initialized (fixes session_state attribute error)
	if "aws_region" not in st.session_state:
		st.session_state.aws_region = "us-east-1"


def add_server(name: str, instance_id: str, host: str = "", ssh_user: str = "", ssh_key: str = None):
	name = name.strip()
	instance_id = instance_id.strip()
	host = host.strip()
	ssh_user = ssh_user.strip()
	if not name:
		return
	# avoid duplicates by instance_id or host
	for s in st.session_state.servers:
		if instance_id and s.get("instance_id") == instance_id:
			return
		if host and s.get("host") == host:
			return
	st.session_state.servers.append({"name": name, "instance_id": instance_id, "host": host, "ssh_user": ssh_user, "ssh_key": ssh_key})
	save_servers()


def remove_server_by_label(label: str):
	# label is "name — instance_id"
	for s in list(st.session_state.servers):
		lab = f"{s.get('name')} — {s.get('instance_id','-')}"
		if lab == label:
			st.session_state.servers.remove(s)
			save_servers()
			return


def technical_summary(query: str, server: str) -> str:
	q = query.lower()
	bullets = []
	# Basic intent detection
	if any(k in q for k in ["network", "ping", "latency", "traceroute", "connect"]):
		bullets.append(f"Check basic connectivity to {server}: `ping -c 4 {server}` and `traceroute {server}`.")
		bullets.append("Inspect interface stats: `ip -s link` and `ss -tunlp` for socket states.")
		bullets.append("If high latency, check path MTU and upstream firewall rules.")
	if any(k in q for k in ["health", "status", "uptime", "cpu", "memory", "disk"]):
		bullets.append(f"Run system checks on {server}: `uptime`, `top`/`htop`, `free -m`, and `df -h`.")
		bullets.append("Check service statuses: `systemctl status <service>` and recent logs via `journalctl -u <service> -n 200`.")
	if any(k in q for k in ["logs", "error", "fail", "crash"]):
		bullets.append("Gather recent logs (`journalctl`, application logs`) and search for exceptions or ERROR lines.")
		bullets.append("Correlate timestamps with deployments or config changes.")
	if not bullets:
		# Generic guidance
		bullets.append(f"Requested info about `{query}` on {server}.")
		bullets.append("Start with connectivity, resource usage, and service logs; then deep dive based on findings.")

	# Compose summary
	summary = f"Technical summary for {server}:\n\n"
	for b in bullets:
		summary += "- " + b + "\n"

	# Suggested next steps as a compact checklist
	summary += "\nSuggested next steps:\n"
	summary += "1. Run the basic commands above and collect outputs.\n"
	summary += "2. If an issue is found, capture logs and timestamps; escalate with collected artifacts.\n"
	return summary


def recommend_commands_based_on_query(query: str) -> list:
	q = query.lower()
	cmds = ["uptime", "df -h", "free -m"]
	if any(k in q for k in ["network", "ping", "latency", "traceroute"]):
		cmds = ["ping -c 4 8.8.8.8", "ip -4 addr show", "ss -tunlp | head -n 200"]
	if any(k in q for k in ["logs", "error", "fail", "crash"]):
		cmds = ["journalctl -n 200 --no-pager", "tail -n 200 /var/log/syslog || true"]
	if any(k in q for k in ["cpu", "memory", "disk"]):
		cmds = ["top -b -n1 | head -n 20", "free -m", "df -h"]
	return cmds


def ssm_send_commands(instance_id: str, commands: list, region: str = None, timeout: int = 60) -> dict:
	"""
	Send commands via SSM RunCommand and return a dict of outputs per command.
	Requires AWS credentials/configured environment or role.
	"""
	if boto3 is None:
		raise RuntimeError("boto3 is not available; install boto3 in your environment")
	region = region or st.session_state.get("aws_region", "us-east-1")
	ssm = boto3.client("ssm", region_name=region)
	try:
		resp = ssm.send_command(
			InstanceIds=[instance_id],
			DocumentName="AWS-RunShellScript",
			Parameters={"commands": commands},
			TimeoutSeconds=max(timeout, 30),
		)
	except (BotoCoreError, ClientError) as e:
		raise RuntimeError(f"Failed to send SSM command: {e}")

	cmd_id = resp["Command"]["CommandId"]

	# poll for completion
	end = time.time() + timeout
	while time.time() < end:
		try:
			out = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
		except ClientError as e:
			time.sleep(1)
			continue
		status = out.get("Status")
		if status in ("Success", "Failed", "Cancelled", "TimedOut"):
			return {"Status": status, "Stdout": out.get("StandardOutputContent"), "Stderr": out.get("StandardErrorContent")}
		time.sleep(1)

	return {"Status": "Timeout", "Stdout": "", "Stderr": ""}


def ssh_run_commands(host: str, user: str, key_text: str = None, key_path: str = None, port: int = 22, commands: list = None, timeout: int = 60) -> dict:
	"""
	Run commands over SSH using Paramiko. Returns dict with Status, Stdout, Stderr.
	"""
	if paramiko is None:
		raise RuntimeError("paramiko is not installed; add paramiko to requirements.txt")
	if commands is None:
		commands = ["uptime"]

	tmp_path = None
	try:
		client = paramiko.SSHClient()
		client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

		pkey = None
		if key_text:
			import tempfile, os

			tf = tempfile.NamedTemporaryFile(delete=False, mode="w", prefix="sshkey_", suffix=".pem")
			tf.write(key_text)
			tf.close()
			tmp_path = tf.name
			os.chmod(tmp_path, 0o600)
			key_path_to_use = tmp_path
		else:
			key_path_to_use = key_path

		connect_kwargs = {"hostname": host, "username": user, "port": port, "timeout": 10}
		if key_path_to_use:
			connect_kwargs["key_filename"] = key_path_to_use

		client.connect(**connect_kwargs)

		full_stdout = []
		full_stderr = []
		for cmd in commands:
			stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
			out = stdout.read().decode(errors="ignore")
			err = stderr.read().decode(errors="ignore")
			full_stdout.append(f"$ {cmd}\n{out}")
			if err:
				full_stderr.append(f"$ {cmd}\n{err}")

		client.close()
		return {"Status": "Success", "Stdout": "\n".join(full_stdout), "Stderr": "\n".join(full_stderr)}
	except Exception as e:
		try:
			client.close()
		except Exception:
			pass
		return {"Status": f"Failed: {e}", "Stdout": "", "Stderr": ""}
	finally:
		if tmp_path:
			try:
				import os

				os.remove(tmp_path)
			except Exception:
				pass


def main():
	# Ensure page config is set before any Streamlit calls to avoid rendering issues
	st.set_page_config(page_title="Server Agent UI", layout="wide")

	ensure_session()
	st.markdown(
	"""
	<style>
	:root { --bg:#0b1220; --panel:#0f1724; --accent:#7c3aed; --accent-2:#06b6d4; --muted:#9aa4b2; --card:#0b1228; }
	.app-title {font-family: 'Segoe UI', Roboto, sans-serif; font-size:28px; font-weight:700; color: #ffffff;}
	.hero {background: linear-gradient(90deg,var(--accent), var(--accent-2)); padding:14px; border-radius:10px; color: white}
	.card {background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01)); padding:12px; border-radius:10px; box-shadow: 0 6px 18px rgba(2,6,23,0.6);}
	.left-panel {background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01)); color: #e6eef6; padding:12px; border-radius:10px}
	.server-item {background: rgba(255,255,255,0.02); padding:10px; margin:8px 0; border-radius:8px; color: #dbeafe}
	.muted {color: var(--muted); font-size:13px}
	.agent-card {background: linear-gradient(180deg, rgba(124,58,237,0.06), rgba(6,182,212,0.04)); padding:14px; border-radius:10px}
	/* Avoid styling Streamlit internals to prevent control breakage. */
	.hero, .card, .left-panel, .agent-card {max-width:100%;}
	</style>
	""",
	unsafe_allow_html=True,
)
	st.markdown('<div class="hero"><span class="app-title">📡 Server Agent — Query & Summary</span></div>', unsafe_allow_html=True)

	left_col, right_col = st.columns([1, 3])  # 25% / 75%

	with left_col:
		st.markdown('<div class="left-panel">\n<h3>Server List</h3>\n<p class="muted">Manage servers you want to query (placeholder).</p>\n</div>', unsafe_allow_html=True)
		with st.form(key="server_form", clear_on_submit=True):
			add_input = st.text_input("Add server (format: name,host,instance_id) — only host/name required", placeholder="my-server,34.204.72.11,i-0123...")
			add = st.form_submit_button("Add")
			if add and add_input:
				parts = [p.strip() for p in add_input.split(",") if p.strip()]
				name = parts[0] if len(parts) >= 1 else ""
				host = parts[1] if len(parts) >= 2 else ""
				instance_id = parts[2] if len(parts) >= 3 else ""
				add_server(name, instance_id, host)

		# display server list with small badges
		st.markdown("<div class='card' style='margin-top:12px;'>", unsafe_allow_html=True)
		for s in st.session_state.servers:
			lab = f"{s.get('name')} — {s.get('instance_id','-')}"
			st.markdown(f"<div class='server-item'><strong>{lab}</strong></div>", unsafe_allow_html=True)
		st.markdown("</div>", unsafe_allow_html=True)

		# single dropdown for servers
		labels = [f"{s.get('name')} — {s.get('host') or s.get('instance_id') or '-'}" for s in st.session_state.servers]
		sel_label = st.selectbox("Select server", options=labels)
		st.markdown("<div style='margin-top:8px'></div>", unsafe_allow_html=True)
		st.button("Remove selected", on_click=remove_server_by_label, args=(sel_label,))

	with right_col:
		# Debug panel to help diagnose missing output issues
		with st.expander("Debug: session & environment", expanded=False):
			st.write({k: st.session_state.get(k) for k in list(st.session_state.keys())})
			st.write("boto3 installed:", boto3 is not None)
			st.write("paramiko installed:", paramiko is not None)
			st.write("servers loaded:", len(st.session_state.servers) if "servers" in st.session_state else 0)

		st.markdown('<div class="card">\n<h3>Query Box</h3>\n<p class="muted">Ask about network, healthcheck, logs, or general status for the selected server.</p>\n</div>', unsafe_allow_html=True)
		# AWS region input (use .get to avoid AttributeError if not initialized)
		region = st.text_input("AWS region (used for SSM)", value=st.session_state.get("aws_region", "us-east-1"))
		st.session_state.aws_region = region

		# Prominent query box
		st.subheader("Ask a question about the selected server")
		query = st.text_area("Enter your query", height=220, key="main_query")
		run = st.button("Run Query")

		agent_area = st.empty()

		if run:
			if not query or not query.strip():
				st.warning("Please enter a query before running.")
			else:
				# defensive handling: catch unexpected errors and show them in the UI
				try:
					sel_index = labels.index(sel_label)
					server_obj = st.session_state.servers[sel_index]
					server_display = f"{server_obj.get('name')} ({server_obj.get('instance_id','-')})"
					# immediate debug message so user sees the run was triggered
					agent_area.info(f"Running query for: {server_display}")
					with st.spinner("Analyzing query and preparing summary..."):
						summary = technical_summary(query, server_display)

					# show selected server details and explain execution capability
					agent_area.markdown("**Selected server details:**")
					agent_area.write(server_obj)
					if not server_obj.get("instance_id") and not server_obj.get("host"):
						agent_area.info("Selected server has no `host` or `instance_id`. Only generic guidance will be provided.")
					else:
						if server_obj.get("instance_id") and boto3 is None:
							agent_area.warning("Instance ID present but `boto3` is not installed — cannot run SSM commands.")
						if server_obj.get("host") and paramiko is None:
							agent_area.warning("Host present but `paramiko` is not installed — cannot run SSH commands.")
					# colorful output card
					agent_area.markdown('<div class="card">', unsafe_allow_html=True)
					agent_area.code(summary)

					# recommended commands
					cmds = recommend_commands_based_on_query(query)
					agent_area.markdown("**Recommended commands to run:**")
					for c in cmds:
						agent_area.write(f"- `{c}`")

					# Auto-run SSH when host is available
					if server_obj.get('host'):
						if paramiko is None:
							agent_area.warning("paramiko not installed; cannot automatically run SSH commands.")
						else:
							agent_area.info("Auto-executing recommended commands via SSH...")
							ssh_user = server_obj.get('ssh_user') or 'ec2-user'
							ssh_key_path = server_obj.get('ssh_key') or ""
							try:
								out = ssh_run_commands(server_obj.get('host'), ssh_user, key_path=ssh_key_path, commands=cmds, timeout=60)
								agent_area.markdown("**Auto SSH Execution result:**")
								agent_area.write(out.get('Status'))
								if out.get('Stdout'):
									agent_area.markdown("**Stdout**")
									agent_area.code(out.get('Stdout'))
									# persist and log output for visibility across reruns
									st.session_state['last_ssh_output'] = out.get('Stdout')
									logger.info("SSH Stdout:\n%s", out.get('Stdout'))
								if out.get('Stderr'):
									agent_area.markdown("**Stderr**")
									agent_area.code(out.get('Stderr'))
									st.session_state['last_ssh_stderr'] = out.get('Stderr')
									logger.warning("SSH Stderr:\n%s", out.get('Stderr'))
							except Exception as e:
								agent_area.error(f"Auto SSH failed: {e}")

					# execute button (SSM)
					if boto3 is None:
						agent_area.warning("boto3 not installed; cannot execute commands. Add boto3 to requirements.")
					elif not server_obj.get('instance_id'):
						agent_area.info("No instance ID provided for this server — cannot run SSM commands.")
					else:
						if agent_area.button("Execute recommended checks on server"):
							try:
								out = ssm_send_commands(server_obj.get('instance_id'), cmds, region=st.session_state.aws_region, timeout=90)
								agent_area.markdown("**Execution result:**")
								agent_area.write(out.get('Status'))
								if out.get('Stdout'):
									agent_area.markdown("**Stdout**")
									agent_area.code(out.get('Stdout'))
								if out.get('Stderr'):
									agent_area.markdown("**Stderr**")
									agent_area.code(out.get('Stderr'))
							except Exception as e:
								agent_area.error(f"SSM execution failed: {e}")

					# SSH execution path (if host provided)
					if server_obj.get('host'):
						agent_area.markdown("---")
						agent_area.markdown("**SSH Execution**")
						agent_area.write(f"Host: {server_obj.get('host')}")
						agent_area.write(f"User: {server_obj.get('ssh_user') or 'not set'}")
						# allow paste key or use uploaded key
						# Prefill SSH inputs from server info to simplify execution
						ssh_key_input = agent_area.text_area("Paste private key here (optional)")
						ssh_key_path = agent_area.text_input("Or provide key path on this host (optional)", value=server_obj.get("ssh_key") or "")
						ssh_user_input = agent_area.text_input("SSH user", value=server_obj.get("ssh_user") or "ec2-user")
						if agent_area.button("Execute via SSH"):
							if paramiko is None:
								agent_area.error("paramiko not installed; cannot run SSH. Add paramiko to requirements.")
							else:
								host = server_obj.get('host')
								user = ssh_user_input or server_obj.get('ssh_user') or 'ec2-user'
								key_text = ssh_key_input if ssh_key_input.strip() else None
								key_path_to_use = ssh_key_path or server_obj.get('ssh_key')
								# prefer provided key text, else key path
								if key_path_to_use:
									key_text = None
								try:
									out = ssh_run_commands(host, user, key_text=key_text, key_path=ssh_key_path, commands=cmds, timeout=90)
									agent_area.markdown("**SSH Execution result:**")
									agent_area.write(out.get('Status'))
									if out.get('Stdout'):
										agent_area.markdown("**Stdout**")
										agent_area.code(out.get('Stdout'))
									if out.get('Stderr'):
										agent_area.markdown("**Stderr**")
										agent_area.code(out.get('Stderr'))
								except Exception as e:
									agent_area.error(f"SSH execution failed: {e}")

					agent_area.markdown('</div>', unsafe_allow_html=True)
				except Exception as e:
					agent_area.error(f"An unexpected error occurred: {e}")

		# Persistent results card (shows last SSH output across reruns)
		if st.session_state.get('last_ssh_output') or st.session_state.get('last_ssh_stderr'):
			st.markdown('<div class="card" style="margin-top:12px;">', unsafe_allow_html=True)
			st.markdown('**Last Execution Results**')
			if st.session_state.get('last_ssh_output'):
				st.markdown('**Stdout**')
				st.code(st.session_state.get('last_ssh_output'))
			if st.session_state.get('last_ssh_stderr'):
				st.markdown('**Stderr**')
				st.code(st.session_state.get('last_ssh_stderr'))
			st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
	main()
