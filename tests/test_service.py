import http.client
import json
import threading
import time
from datetime import timedelta

from claude_burndown.config import Paths
from claude_burndown.service import LoopbackService


def test_service_endpoints(tmp_path):
    paths = Paths(tmp_path)
    # Pick an arbitrary high port for test
    port = 18788
    service = LoopbackService(paths, host="127.0.0.1", port=port)
    t = threading.Thread(target=service.start, daemon=True)
    t.start()
    time.sleep(0.5)

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        # GET /
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "Claude Burndown" in body
        assert "Enterprise Edition" in body

        # GET /v1/capacity
        conn.request("GET", "/v1/capacity")
        resp = conn.getresponse()
        assert resp.status == 200
        snap = json.loads(resp.read().decode("utf-8"))
        assert snap["schema_version"] == 1
        assert "claude" in snap["provider_states"]

        # POST /v1/policy
        conn.request("POST", "/v1/policy", body=json.dumps({"reserve_pct": 20}), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 200
        resp.read()

        # Check updated policy
        conn.request("GET", "/v1/capacity")
        resp = conn.getresponse()
        snap2 = json.loads(resp.read().decode("utf-8"))
        assert snap2["policy"]["reserve_pct"] == 20
    finally:
        conn.close()
        service.stop()
