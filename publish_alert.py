"""Publish qualified scans to a rolling draft PR used as a ChatGPT event bridge."""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


REPO = os.environ.get("GITHUB_REPOSITORY", "Chiizzie/market-radar")
OWNER, _ = REPO.split("/", 1)
BASE_BRANCH = "main"
ALERT_TITLE = "[RADAR ALERT] Live scan feed"
BRANCH_PREFIX = "radar-alert-" + datetime.now(ZoneInfo("Europe/Dublin")).strftime("%Y%m%d")


def run(args, *, capture=False, check=True):
    return subprocess.run(
        args,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def prs_for_branch(branch):
    result = run(
        [
            "gh", "pr", "list", "--repo", REPO, "--head", f"{OWNER}:{branch}",
            "--state", "all", "--json", "number,state",
        ],
        capture=True,
    )
    return json.loads(result.stdout)


def choose_branch():
    for slot in range(1, 100):
        branch = f"{BRANCH_PREFIX}-{slot}"
        prs = prs_for_branch(branch)
        if not prs:
            return branch, False
        if any(pr["state"] == "OPEN" for pr in prs):
            return branch, True
    raise RuntimeError("Could not find an available daily radar alert branch")


def main():
    scan_path = Path("latest_scan.json")
    if not scan_path.exists():
        raise FileNotFoundError("latest_scan.json was not created by run_radar.py")

    payload = json.loads(scan_path.read_text(encoding="utf-8"))
    signals = payload.get("potential_signals", [])
    if not signals:
        print("No potential signals passed the pre-filter; no GitHub event sent.")
        return

    branch, has_open_pr = choose_branch()

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    run(["git", "fetch", "origin", BASE_BRANCH])

    remote_branch = run(
        ["git", "ls-remote", "--heads", "origin", branch],
        capture=True,
        check=False,
    )
    if remote_branch.stdout.strip():
        run(["git", "fetch", "origin", branch])
        run(["git", "checkout", "--force", "-B", branch, f"origin/{branch}"])
    else:
        run(["git", "checkout", "--force", "-B", branch, f"origin/{BASE_BRANCH}"])

    Path("radar_alert.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    run(["git", "add", "radar_alert.json"])
    changed = run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0
    if changed:
        run(["git", "commit", "-m", "Refresh latest qualifying radar scan"])
        run(["git", "push", "--set-upstream", "origin", branch])
    else:
        print("Alert payload is unchanged; no new commit or event needed.")

    if not has_open_pr:
        body = (
            "Automated rolling feed for qualifying market scans. ChatGPT should read "
            "`radar_alert.json`, verify live prices and catalysts, and apply the user's "
            "trading rules before deciding whether to notify.\n\n"
            "This draft PR is an event bridge only. Do not merge it."
        )
        run(
            [
                "gh", "pr", "create", "--repo", REPO, "--base", BASE_BRANCH,
                "--head", branch, "--title", ALERT_TITLE, "--body", body, "--draft",
            ]
        )

    print(f"Updated {branch} with {len(signals)} potential signal(s).")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"GitHub alert bridge command failed: {exc}", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        raise
