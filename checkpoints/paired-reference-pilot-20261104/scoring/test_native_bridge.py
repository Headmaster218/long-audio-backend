"""Model-free bridge guard checks; no model or real audio."""
import copy
import unittest
from unittest.mock import patch
import native_bridge as b

class BridgeTests(unittest.TestCase):
    def test_prepare_rejects_external_crop_pin(self):
        with patch.object(b.p,'pin',return_value={'sha256':'a'*64}):
            with self.assertRaisesRegex(ValueError,'SHA mismatch'):
                b.prepare('unused','b'*64,'unused')

    def test_release_requires_paired_lock(self):
        pred={'T':{'split':'T','gallery':[{'person_id':'A','reference_ids':['a','b','c']},{'person_id':'B','reference_ids':['d','e','f']}], 'queries':['q'], 'primary_queries':['q']}}
        plan={'samples':[{'id':q} for q in 'abcdefq']}
        evidence={'bridge_sources':b.bridge_sources(),'native_plans':{'T':'plan'}}
        with patch.object(b.p,'load_protocol',return_value=({'sha256':'1'*64},{'source_evidence':'evidence'},pred)), patch.object(b.p,'read_pinned',side_effect=[evidence,plan]), patch.object(b.runner,'validate_plan_template'):
            with self.assertRaisesRegex(ValueError,'paired C lock required'):
                b.expected('unused','1'*64,'T')

    def test_gate_does_not_publish_after_incomplete_native_run(self):
        with patch.object(b,'expected',return_value=({},{})), patch.object(b.runner,'verify_environment'), patch.object(b.original,'_sealed_run',side_effect=ValueError('incomplete positive wait')), patch.object(b.p,'seal') as seal:
            with self.assertRaisesRegex(ValueError,'positive wait'):
                b.gate('p','sha','C',{}, {}, 'out')
            seal.assert_not_called()

    def test_source_inventory_binds_original_gate(self):
        sources=b.bridge_sources()
        self.assertEqual(set(sources),{'bridge','native_runner','native_gate','gate_scoring','native_helper'})
        for pin in sources.values():b.p.verify_pin(pin)

if __name__=='__main__':unittest.main()
