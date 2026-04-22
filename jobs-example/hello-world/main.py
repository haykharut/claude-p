import datetime
import os
import socket
from pathlib import Path


def main():
    run_id = os.environ.get("CLAUDE_P_RUN_ID", "local")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    host = socket.gethostname()
    text = f"hello from run {run_id} on {host} at {now}\n"
    Path("greeting.txt").write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
