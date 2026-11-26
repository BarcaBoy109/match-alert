"""Stage the Azure bot with Discord login paused; activate only after stopping Render."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Public container image with immutable SHA tag")
    parser.add_argument("--location", default="eastasia")
    parser.add_argument("--resource-group", default="match-alert-rg")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    values = {**dotenv_values(root / ".env"), **os.environ}
    for name in ("DISCORD_TOKEN", "DATABASE_URL"):
        if not values.get(name):
            parser.error(f"{name} must be set in .env or the environment")
    az = shutil.which("az")
    if not az:
        parser.error("Azure CLI is required; run az login first")
    params = {
        "location": {"value": args.location},
        "image": {"value": args.image},
        "discordToken": {"value": values["DISCORD_TOKEN"]},
        "databaseUrl": {"value": values["DATABASE_URL"]},
        "pollMinutes": {"value": values.get("POLL_MINUTES", "10")},
    }
    exists = subprocess.check_output([
        az, "group", "exists", "--name", args.resource_group, "-o", "tsv"
    ], text=True).strip()
    if exists != "true":
        subprocess.run([az, "group", "create", "--name", args.resource_group,
                        "--location", args.location, "--output", "none"], check=True)
    # ARM securestring parameters keep credentials out of deployment history and
    # process arguments. The private temporary file is removed even on failure.
    with tempfile.TemporaryDirectory(prefix="match-alert-deploy-") as folder:
        path = Path(folder) / "parameters.json"
        path.write_text(json.dumps({"parameters": params}), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
        subprocess.run([
            az, "deployment", "group", "create", "--resource-group", args.resource_group,
            "--name", "match-alert", "--template-file", str(root / "azure" / "main.json"),
            "--parameters", "@" + str(path), "--query", "properties.outputs.url.value", "-o", "tsv",
        ], check=True)
    print("Staged with Discord login paused. Stop the old host before activating; see azure/README.md.")


if __name__ == "__main__":
    main()
