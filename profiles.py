"""
profiles.py
Contains named profiles that control recommended commands and response formatting.
"""

profiles = {
    "ops_concise": {
        "display_name": "Ops (concise)",
        "response_style": "concise",
        "summary_lines": 3,
        "cpu_commands": ["ps -eo pid,comm,%cpu --sort=-%cpu | head -n 6", "top -b -n1 | head -n 20", "free -m"],
        "default_commands": ["uptime", "df -h", "free -m"],
        "show_chart": True,
    },
    "debug_verbose": {
        "display_name": "Debug (verbose)",
        "response_style": "verbose",
        "summary_lines": 8,
        "cpu_commands": ["top -b -n1 | head -n 40", "ps -eo pid,comm,%cpu --sort=-%cpu | head -n 20", "free -m"],
        "default_commands": ["uptime", "df -h", "free -m", "journalctl -n 200 --no-pager"],
        "show_chart": True,
    },
}


def recommend_commands(query: str, profile_name: str = "ops_concise") -> list:
    """Return a list of recommended commands for the given query and profile."""
    p = profiles.get(profile_name, profiles["ops_concise"])
    q = (query or "").lower()
    if any(k in q for k in ["cpu", "memory"]):
        return p.get("cpu_commands")
    if any(k in q for k in ["log", "error", "fail", "crash"]):
        return ["journalctl -n 200 --no-pager", "tail -n 200 /var/log/syslog || true"]
    if any(k in q for k in ["network", "ping", "latency", "traceroute"]):
        return ["ping -c 4 8.8.8.8", "ip -4 addr show", "ss -tunlp | head -n 200"]
    return p.get("default_commands", [])
