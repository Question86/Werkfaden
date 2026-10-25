from __future__ import annotations
import json, tempfile, unittest
from pathlib import Path

from kairos.guardian_bridge import GuardianBridgeError, source_permit_guard
from kairos.util import sha256_text


def cj(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class GuardianBridgeTests(unittest.TestCase):
    def _fixture(self, root: Path, step: str = "EXACT_SOURCE"):
        ws=root/'ws'; k=ws/'.kairos'; k.mkdir(parents=True)
        cfg={"schema":"runtime-sync-workshop-config/v1","guardian_required":True,"state_directory":".state"}
        (k/'workshop.config.json').write_text(json.dumps(cfg),encoding='utf-8')
        (k/'runtime_state.json').write_text(json.dumps({"active_task":"TASK","active_criterion":"CRIT"}),encoding='utf-8')
        gd=k/'.state'/'guardian-v2'; gd.mkdir(parents=True)
        event={"sequence":1,"kind":"STATE_ENTER","at":"2026-09-16T00:00:00Z","prev_hash":"0"*64,"memory_sha256":"a"*64,"rolling_package_sha256":"b"*64,"details":{}}
        event['event_hash']=sha256_text(cj(event))
        payload={"schema":"werkfaden-guardian/v2","guardian_id":"GRD2_"+'1'*24,"closed":False,"project_scope":{"task_id":"TASK","criterion_id":"CRIT"},"active_gate":{"ticket_id":"GST_"+'2'*24,"step":step,"opened_at":"2026-09-16T00:00:00Z","head_hash_before":"0"*64},"events":[event],"head_hash":event['event_hash']}
        (gd/f"{payload['guardian_id']}.json").write_text(json.dumps(payload),encoding='utf-8')
        return ws

    def test_exact_source_gate_binds(self):
        with tempfile.TemporaryDirectory() as t:
            ctx=source_permit_guard(self._fixture(Path(t),'EXACT_SOURCE'),'implementation_verification')
            self.assertEqual(ctx['step'],'EXACT_SOURCE')

    def test_negative_probe_requires_counterprobe(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(GuardianBridgeError):
                source_permit_guard(self._fixture(Path(t),'EXACT_SOURCE'),'negative_proof')

    def test_exception_blocked(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(GuardianBridgeError):
                source_permit_guard(self._fixture(Path(t),'EXACT_SOURCE'),'exact_file_request')


if __name__=='__main__':
    unittest.main()
