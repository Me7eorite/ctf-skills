"""Run the research backfill HTML fixture through an existing Chrome CDP endpoint."""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.parse
import urllib.request

import websockets

CDP_HTTP = "http://127.0.0.1:9223"
FIXTURE_URL = "http://127.0.0.1:4186/tests/browser/research-backfill-fixture.html"


def _new_target() -> str:
    request = urllib.request.Request(
        f"{CDP_HTTP}/json/new?{urllib.parse.quote(FIXTURE_URL, safe=':/')}",
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)["webSocketDebuggerUrl"]


async def _run() -> str:
    websocket_url = _new_target()
    async with websockets.connect(websocket_url) as socket:
        sequence = 0

        async def evaluate(expression: str):
            nonlocal sequence
            sequence += 1
            await socket.send(
                json.dumps(
                    {
                        "id": sequence,
                        "method": "Runtime.evaluate",
                        "params": {"expression": expression, "returnByValue": True},
                    }
                )
            )
            while True:
                message = json.loads(await socket.recv())
                if message.get("id") == sequence:
                    return message["result"]["result"].get("value")

        for _ in range(200):
            status = await evaluate("document.body?.dataset.status || ''")
            if status:
                result = await evaluate("document.querySelector('#fixture-result')?.textContent || ''")
                if status != "passed":
                    raise RuntimeError(result)
                return result
            await asyncio.sleep(0.05)
    raise TimeoutError("browser fixture did not finish within 10 seconds")


if __name__ == "__main__":
    try:
        print(asyncio.run(_run()))
    except Exception as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
