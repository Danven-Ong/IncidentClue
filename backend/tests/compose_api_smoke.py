"""Credential-free container HTTP/restart smoke invoked explicitly by CI."""

import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen


def main():
    base, record, mode = sys.argv[1:]
    record = Path(record)

    def request(path, data=None):
        req = Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=15) as response:
            return response.status, json.load(response)

    assert request("/health") == (200, {"status": "ok", "database": "ready"})
    if mode == "create":
        code, before = request("/investigations", {
            "incident": {"incident_id": "compose-http", "service": "checkout", "summary": "Offline smoke", "evidence": []},
            "health": {"service": "checkout", "evidence": []},
        })
        assert code == 201 and before["status"] == "paused"
        assert before["charged"] == {"model": 0, "tool": 0}
        record.write_text(json.dumps(before), encoding="utf-8")
    else:
        before = json.loads(record.read_text(encoding="utf-8"))
    code, after = request("/investigations/" + before["investigation_id"])
    assert code == 200 and before == after
    print(before["investigation_id"])


if __name__ == "__main__":
    main()
