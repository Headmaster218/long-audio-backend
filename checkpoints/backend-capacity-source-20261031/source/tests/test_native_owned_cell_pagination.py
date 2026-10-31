"""Model-free pagination regressions with generated real-authority fixtures.

All cells are synthetic two-source composites. Standard unittest discovery uses
fresh temporary SQLite/WAV files and actual proof parsing/PrimaryAuthority.
No owner/API/model startup or runtime-qualification claim is involved.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import time
import unittest
from unittest.mock import patch
import wave

import test_native_owned_cell_list_authority as authority_fixture
from test_native_owned_seam_cells import synthetic_pieces
from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_seam_cells as seam
from audio_backend import native_owned_cell_store as store
from audio_backend import native_owned_cell_capture as cap
from audio_backend.native_owned_support import validate_observed_provenance

class PaginationTests(unittest.TestCase):
    # Reuse generated fixture setup without inheriting its sixteen test cases.
    writer = authority_fixture.ListAuthorityTests.writer
    close_connections = authority_fixture.ListAuthorityTests.close_connections
    commit = authority_fixture.ListAuthorityTests.commit
    mutate_after_validation = authority_fixture.ListAuthorityTests.mutate_after_validation

    def setUp(self):
        self.deep = self._testMethodName == 'test_single_unreturnable'
        authority_fixture.ListAuthorityTests.setUp(self)
        self.owner._db.execute('DELETE FROM dependencies')
        self.owner._db.execute('DELETE FROM cells')
        self.owner._db.execute('DELETE FROM sources')
        with self.writer() as db:
            db.execute('DELETE FROM stream_chunks')
            db.execute('DELETE FROM chunks')
        self.owner._receipts.clear()
        self.uploads = self.root / 'uploads'
        if self.deep:
            source_parent = self.root
            for index in range(16):
                source_parent = source_parent / (str(index).zfill(2) + 'd' * 148)
                source_parent.mkdir()
            self.uploads = source_parent / 'uploads'
            self.uploads.mkdir()
            self.authority.uploads = self.uploads
        self.assertLess(len(str(self.primary)), 512)
        self.assertLess(len(str(self.uploads / 'right00.audio')), 4096)
        self.assertLessEqual(len(self.uploads.parts), 33)
        self.expected = []
        self.metrics = []
        self.proof_sizes = []
        self.addCleanup(self.record_size)

    def record_size(self):
        files = [p for p in self.root.rglob('*') if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        self.assertLess(total, 4 * 1024**2)

    def add_cell(self, index, *, heavy=False):
        stream = 'stream' + str(index)
        cids = (f'left{index:02d}', f'right{index:02d}')
        receipts = []
        for pos, cid in enumerate(cids):
            path = self.uploads / (cid + '.audio')
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2)
                output.setframerate(cells.RATE); output.writeframes(b'\x01\0' * 16)
            raw = path.read_bytes()
            receipts.append(store.Receipt(self.sid, cid, hashlib.sha256(raw).hexdigest(),
                16, 44, 76, cap.signature(path.stat()), pos * 16 / cells.RATE, stream))
        pieces = synthetic_pieces(cids=cids, receipt_ids=tuple(r.receipt_id for r in receipts),
            source_hashes=tuple(r.digest for r in receipts))
        adjusted = []
        for piece in pieces:
            scope = dict(piece.scope, stream_id=stream)
            commit = deepcopy(piece.commit); witness = deepcopy(piece.witness)
            if heavy:
                provenance = witness['native']['provenance']
                provenance['diarization_runtime_files_sha256'] = {
                    f'{j:02d}' + 'f' * 190: ('1' if j == 0 else '2') * 64
                    for j in range(32)}
                base = {k: v for k, v in provenance.items() if k not in (
                    'diarization_fingerprint', 'diarization_wrapper_cpu_limit',
                    'diarization_cpu_affinity_enforced', 'diarization_cpu_cap_warning')}
                provenance['diarization_fingerprint'] = cells.sha(base)
                validate_observed_provenance(provenance)
                witness['native']['state']['fingerprint'] = provenance['diarization_fingerprint']
                commit['evidence']['native']['provenance'] = deepcopy(provenance)
                commit['evidence_sha256'] = cells.sha(commit['evidence'])
                witness['turns'] = witness['turns'] * 64
            adjusted.append(replace(piece, scope=scope, commit=commit, witness=witness))
        cell = seam.make_composite(*adjusted)
        cells.validate_document(cell.proof)
        self.proof_sizes.append(len(cell.proof))
        for pos, (receipt, piece) in enumerate(zip(receipts, adjusted)):
            self.owner._receipts[receipt.chunk_id] = receipt
            evidence = piece.commit['evidence']
            with self.writer() as db:
                db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)',
                    (receipt.chunk_id, self.sid, str(self.uploads / (receipt.chunk_id + '.audio')),
                     receipt.digest, 16 / cells.RATE, receipt.timeline_start, 16 / cells.RATE,
                     'completed', json.dumps({'native_diarization': evidence['native'],
                                             'core_cut': evidence['core_cut']})))
                db.execute('INSERT INTO stream_chunks VALUES(?,?,?,?)',
                    (receipt.chunk_id, self.sid, stream, pos))
            self.owner._db.execute('INSERT INTO sources VALUES(?,?,?,?)',
                (receipt.chunk_id, self.sid, receipt.digest, receipt.encoded))
        inserted = self.owner._db.execute('INSERT INTO cells(cell_id,ownership,sid,cid,proof,pcm,state) '
            "VALUES(?,?,?,?,?,?,'retained')", (cell.cell_id, cell.ownership, self.sid,
                cell.chunk_id, cell.proof, cell.pcm))
        for pos, part in enumerate(cell.parts):
            self.owner._db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?,?,?)',
                (cell.cell_id, pos, part.chunk_id, part.source_sha256, part.receipt_sha256,
                 part.source_start, part.source_end, part.logical_start, part.logical_end))
        self.expected.append((inserted.lastrowid, cell.cell_id))
        return cell

    def page(self, after=0, limit=20):
        op = store.Operation('jobs_list', (self.sid, after, limit), self.owner._epoch)
        budget = store.Budget.for_owner(op)
        connection_start = len(self.connections)
        metric = {'after': after, 'validations': []}
        self.metrics.append(metric)
        original = self.authority.cell_valid
        def observed(doc, budget, *, publishing):
            before = budget.sql_bytes
            result = original(doc, budget, publishing=publishing)
            metric['validations'].append({'chunk': seam.anchor(doc)['chunk_id'],
                'sql_bytes': budget.sql_bytes - before, 'valid': result})
            return result
        try:
            with patch.object(self.authority, 'cell_valid', side_effect=observed), budget.phase():
                self.owner._db.set_progress_handler(budget.progress, 100)
                result = self.owner._list(op, budget)
                op.check(); self.owner._generation(op)
            metric.update(count=len(result['cells']), cursor=result['next_cursor'],
                          has_more=result['has_more'])
            return result
        except cells.Unavailable as exc:
            metric['error'] = str(exc)
            raise
        finally:
            self.owner._db.set_progress_handler(None, 0)
            metric.update(steps=budget.steps, sql_bytes=budget.sql_bytes,
                          read_bytes=budget.read_bytes, elapsed=budget.elapsed)
            self.assertIsNone(getattr(self.authority._list_reads, 'current', None))
            self.assertTrue(all(db.record['closed'] for db in self.connections))
            readers = self.connections[connection_start:]
            self.assertEqual(len(readers), 1)
            metric['fresh_begins'] = readers[0].record['begin']
            metric['fresh_rollbacks'] = readers[0].record['rollback']
            if 'error' not in metric:
                self.assertEqual(metric['fresh_begins'], metric['fresh_rollbacks'])
                self.assertLessEqual(budget.sql_bytes, store.SQL_PROJECTION_BYTES)
                self.assertLessEqual(budget.steps, store.SQL_STEPS)

    def traverse(self):
        after = 0; found = []
        for _ in range(21):
            result = self.page(after)
            rows = [(r['sequence'], r['cell_id']) for r in result['cells']]
            found.extend(rows)
            pending = [r for r in self.expected if r[0] > result['next_cursor']]
            self.assertEqual(result['has_more'], bool(pending))
            if not result['has_more']:
                self.assertEqual(found, self.expected)
                return
            self.assertGreater(result['next_cursor'], after)
            after = result['next_cursor']
        self.fail('Finite twenty-cell continuation did not terminate')

    def test_twenty_lightweight_composites(self):
        for i in range(20):self.add_cell(i)
        self.traverse()

    def test_twenty_heavy_composites(self):
        for i in range(20):self.add_cell(i, heavy=True)
        self.traverse()

    def test_mixed_boundary(self):
        # Three measured light first passes remain below the heuristic cutoff.
        # A much larger fourth proof now exercises the attempted-cell boundary.
        for i in range(20):self.add_cell(i, heavy=(i % 4 == 3))
        self.traverse()

    def test_single_unreturnable(self):
        self.add_cell(0, heavy=True)
        with self.assertRaisesRegex(cells.Unavailable, '^operation_work_capacity$'):
            self.page()
        self.assertTrue(self.metrics[-1]['validations'][0]['valid'])
        self.assertNotIn('has_more', self.metrics[-1])
        self.assertGreater(self.metrics[-1]['sql_bytes'], store.SQL_PROJECTION_BYTES)

    def test_final_mutation(self):
        self.add_cell(0, heavy=True)
        self.mutate_after_validation(1, lambda: self.commit(
            "UPDATE chunks SET metadata=json_set(metadata,'$.core_cut.nominal',999) WHERE id='left00'"))
        with self.assertRaisesRegex(cells.Unavailable, '^source_changed_after_list_read$'):
            self.page()

    def test_final_revocation(self):
        self.add_cell(0, heavy=True)
        self.mutate_after_validation(2, lambda: self.owner.revoked())
        with self.assertRaisesRegex(cells.Unavailable, '^publication_generation_revoked$'):
            self.page()

    def test_deadline_cancellation(self):
        self.add_cell(0, heavy=True)
        original = self.authority.cell_valid
        def expired(doc, budget, *, publishing):
            result = original(doc, budget, publishing=publishing)
            budget.deadline = time.monotonic() - 1.
            return result
        with patch.object(self.authority, 'cell_valid', side_effect=expired):
            with self.assertRaisesRegex(cells.Unavailable, '^cooperative_deadline$'):
                self.page()

    def test_malformed_boundary(self):
        for i in range(4):self.add_cell(i, heavy=(i == 3))
        self.owner._db.execute('UPDATE cells SET proof=? WHERE cell_id=?',
                               (b'{}', self.expected[-1][1]))
        with self.assertRaisesRegex(cells.Unavailable, '^proof_shape$'):
            self.page()
        self.assertEqual(len(self.metrics[-1]['validations']), 3)
        self.assertNotIn('has_more', self.metrics[-1])

    def test_final_projection_growth(self):
        self.add_cell(0, heavy=True)
        def expand_json_escapes():
            # Semantically identical valid metadata, larger SQL projection.
            # This happens only between completed fresh authority transactions.
            for cid in ('left00', 'right00'):
                with self.writer() as db:
                    raw = db.execute('SELECT metadata FROM chunks WHERE id=?', (cid,)).fetchone()[0]
                expanded = raw.replace('f' * 190, ('\\u0066' * 190))
                self.assertEqual(json.loads(expanded), json.loads(raw))
                self.assertLessEqual(len(expanded), cells.PROOF_BYTES)
                self.assertGreater(len(expanded), len(raw))
                self.commit('UPDATE chunks SET metadata=? WHERE id=?', (expanded, cid))
        self.mutate_after_validation(1, expand_json_escapes)
        with self.assertRaisesRegex(cells.Unavailable, '^operation_work_capacity$'):
            self.page()
        self.assertTrue(self.metrics[-1]['validations'][0]['valid'])
        self.assertNotIn('has_more', self.metrics[-1])
