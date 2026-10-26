"""One-shot Daytona sandbox smoke: create → exec → delete. Never prints API keys."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load backend/.env into process env without printing values.
env_path = ROOT / ".env"
if env_path.is_file():
    for ln in env_path.read_text(encoding="utf-8-sig").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k:
            os.environ[k] = v

from app.computers.daytona_client import SdkDaytonaClient, resolve_daytona_api_key  # noqa: E402


async def main() -> int:
    key = resolve_daytona_api_key(os.environ.get("AI_TEAMMATES_DAYTONA_API_KEY") or os.environ.get("DAYTONA_API_KEY"))
    if not key:
        print("FAIL: no Daytona API key in env")
        return 1
    enabled = (os.environ.get("AI_TEAMMATES_COMPUTER_CLOUD_SANDBOX_ENABLED") or "").lower()
    if enabled not in {"1", "true", "yes"}:
        print("FAIL: COMPUTER_CLOUD_SANDBOX_ENABLED is not true")
        return 1
    billable = (os.environ.get("AI_TEAMMATES_COMPUTER_ALLOW_BILLABLE") or "false").lower()
    if billable in {"1", "true", "yes"}:
        print("FAIL: ALLOW_BILLABLE must stay false")
        return 1

    client = SdkDaytonaClient(api_key=key)
    sandbox_id = None
    try:
        print("creating sandbox...")
        info = await client.create_sandbox(
            name="aitm-smoke-test",
            labels={"ai-teammates": "true", "purpose": "smoke"},
            auto_stop_minutes=10,
            public=False,
        )
        sandbox_id = info.id
        print(f"created id={sandbox_id[:12]}... state={info.state} public={info.public}")

        print("exec echo...")
        out = await client.exec_command(sandbox_id, "echo opendot-daytona-ok")
        result = str(out.get("result") or "")
        print(f"exec exit={out.get('exit_code')} ok={'opendot-daytona-ok' in result}")

        print("computer_use start...")
        cu = await client.start_computer_use(sandbox_id)
        print(f"computer_use started={cu.get('started')}")

        print("screenshot...")
        png = await client.screenshot_png(sandbox_id)
        png_magic = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        print(f"screenshot bytes={len(png)} png={png[:8] == png_magic}")

        print("desktop click+type...")
        await client.mouse_click(sandbox_id, x=40, y=40)
        typed = await client.keyboard_type(sandbox_id, text="opendot")
        print(f"typed_chars={typed.get('typed_chars')}")

        print("computer_use stop...")
        await client.stop_computer_use(sandbox_id)

        print("health get...")
        got = await client.get_sandbox(sandbox_id)
        print(f"state={got.state}")

        print("stop...")
        stopped = await client.stop_sandbox(sandbox_id)
        print(f"stopped state={stopped.state}")
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 2
    finally:
        if sandbox_id:
            try:
                print("delete...")
                await client.delete_sandbox(sandbox_id)
                print("deleted")
            except Exception as exc:
                print(f"WARN delete failed: {exc}")
        await client.close()
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
