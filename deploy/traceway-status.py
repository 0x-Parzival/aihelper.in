"""Show production Traceway HTTP monitor states without printing credentials."""
import json
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8082"
PROJECT = "dfac06f9-732d-468f-a8f9-b110f8f528f6"
PASSWORD_FILE = Path("/root/traceway-admin-password.txt")


def request(path, body=None, token=""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


if __name__ == "__main__":
    password = next(line.split(": ", 1)[1] for line in PASSWORD_FILE.read_text().splitlines() if line.startswith("Password: "))
    token = request("/api/login", {"email": "ops@aihelper.in", "password": password})["token"]
    checks = request(f"/api/synthetics/checks?projectId={PROJECT}", token=token)["checks"]
    for check in checks:
        print(f"{check['currentStatus']:7} {check['name']}  last run: {check['lastRunAt'] or 'never'}")
