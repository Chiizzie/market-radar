"""Update the rolling draft PR branch used as the ChatGPT event bridge."""
import json
import os
import subprocess
import sys
from pathlib import Path


REPO = os.environ.get("GITHUB_REPOSITORY", "Chiizzie/market-radar")
ALERT_BRANCH = "radar-alert-live"


def run(args, *, capture=False, check=True):
    return subprocess.run(
        args,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def main():
    scan_path = Path("latest_scan.json")
    if not scan_path.exists():
        raise FileNotFoundError("latest_scan.json was not created by run_radar.py")

    payload = json.loads(scan_path.read_text(encoding="utf-8"))
    signals = payload.get("potential_signals", [])
    if not signals:
        print("No potential signals passed the pre-filter; no GitHub event sent.")
        return

    remote_branch = run(
        ["git", "ls-remote", "--heads", "origin", ALERT_BRANCH],
        capture=True,
    )
    if not remote_branch.stdout.strip():
        raise RuntimeError(
            "The rolling alert branch is missing. Restore the radar-alert-live branch in GitHub."
        )

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])
    run(["git", "fetch", "origin", ALERT_BRANCH])
    run(["git", "checkout", "--force", "-B", ALERT_BRANCH, f"origin/{ALERT_BRANCH}"])

    Path("radar_alert.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    run(["git", "add", "radar_alert.json"])
    changed = run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0
    if changed:
        run(["git", "commit", "-m", "Refresh latest qualifying radar scan"])
        run(["git", "push", "origin", ALERT_BRANCH])
        print(f"Updated {ALERT_BRANCH} with {len(signals)} potential signal(s).")
    else:
        print("Alert payload is unchanged; no new commit or event needed.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"GitHub alert bridge command failed: {exc}", file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        raise
