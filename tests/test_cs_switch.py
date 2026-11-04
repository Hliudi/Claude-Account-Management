import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs_accounts as accounts
import cs_switch as switch
from cs_session import HandoffError, ownership_dir


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.home.mkdir()
        self.directory = ownership_dir(Path(self.temp.name) / 'cs', self.home)
        self.db = self.home / 'state_5.sqlite'
        self.con = sqlite3.connect(self.db)
        self.addCleanup(self.con.close)
        self.con.executescript('CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT, '
                              'model_provider TEXT, model TEXT, history_mode TEXT, archived INTEGER);'
                              'CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT, status TEXT);')
        self.root = self.add_thread()
        self.child = self.add_thread(parent=self.root)
        self.grandchild = self.add_thread(parent=self.child, archived=1)
        self.unrelated = self.add_thread()

    def add_thread(self, parent=None, archived=0, mode='paginated'):
        tid = str(uuid.uuid4())
        path = self.home / (tid + '.jsonl')
        reasoning = {'type': 'reasoning', 'id': 'rs_synthetic', 'summary': [], 'encrypted_content': 'synthetic-opaque'}
        rows = [
            {'type': 'session_meta', 'payload': {'id': tid, 'model_provider': 'openai'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '保留我的工作'}]}},
            {'type': 'response_item', 'payload': reasoning},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'call_id': 'call_1', 'name': 'exec_command', 'arguments': '{"cmd":"true"}'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'call_1', 'output': 'tool-result'}},
            {'type': 'compacted', 'payload': {'replacement_history': [reasoning], 'message': 'summary kept'}},
        ]
        path.write_bytes(b''.join(json.dumps(r, ensure_ascii=False).encode() + b' ' * 100 + b'\n' for r in rows))
        self.con.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)', (tid, str(path), 'openai', 'original-model', mode, archived))
        if parent:
            self.con.execute('INSERT INTO thread_spawn_edges VALUES (?,?,?)', (parent, tid, 'completed'))
        self.con.commit()
        return tid

    def path(self, tid):
        return self.home / (tid + '.jsonl')

    def prepare(self, provider='cs_proxy', **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            switch.prepare_resume('/fake/codex', self.home, self.directory, self.root, provider, 'selected-model', **kwargs)

    def test_tree_routing_and_encrypted_history_preserve_offsets_and_tools(self):
        originals = {t: self.path(t).read_bytes() for t in (self.root, self.child, self.grandchild, self.unrelated)}
        self.prepare()
        for tid in (self.root, self.child, self.grandchild):
            updated = self.path(tid).read_bytes()
            self.assertEqual(list(map(len, originals[tid].splitlines(keepends=True))), list(map(len, updated.splitlines(keepends=True))))
            old = [json.loads(x) for x in originals[tid].splitlines()]
            new = [json.loads(x) for x in updated.splitlines()]
            for i in (0, 1, 3, 4): self.assertEqual(old[i], new[i])
            self.assertIsNone(new[2]['payload']['encrypted_content'])
            self.assertIsNone(new[5]['payload']['replacement_history'][0]['encrypted_content'])
            self.assertEqual(self.con.execute('SELECT model_provider FROM threads WHERE id=?', (tid,)).fetchone()[0], 'cs_proxy')
            backup = next(self.directory.glob('provider-backup-*')) / (tid + '.jsonl')
            self.assertEqual(backup.read_bytes(), originals[tid])
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path(self.unrelated).read_bytes(), originals[self.unrelated])
        self.assertEqual(self.con.execute('SELECT archived,model FROM threads WHERE id=?', (self.grandchild,)).fetchone(), (1, 'selected-model'))
        self.assertEqual(self.con.execute('SELECT model FROM threads WHERE id=?', (self.root,)).fetchone()[0], 'original-model')
        self.prepare('openai')
        self.assertEqual(self.con.execute('SELECT count(*) FROM threads WHERE model_provider="openai"').fetchone()[0], 4)

    def test_legacy_provider_metadata_also_migrates(self):
        self.con.execute('UPDATE threads SET history_mode="legacy"')
        self.con.commit()
        self.prepare()
        self.assertEqual(json.loads(self.path(self.child).read_bytes().splitlines()[0])['payload']['model_provider'], 'cs_proxy')

    def test_same_provider_keeps_encrypted_history_until_explicit_repair(self):
        old = self.path(self.root).read_bytes()
        self.prepare('openai')
        self.assertEqual(self.path(self.root).read_bytes(), old)
        self.prepare('openai', repair_history=True)
        self.assertNotIn(b'synthetic-opaque', self.path(self.root).read_bytes())

    def test_encrypted_only_compaction_is_preserved_without_losing_context(self):
        checkpoint = json.dumps({'type': 'response_item', 'payload': {'type': 'compaction', 'encrypted_content': 'opaque'}}) + '\n'
        with self.path(self.child).open('a') as f:
            f.write(checkpoint)
        self.prepare()
        self.assertTrue(self.path(self.child).read_bytes().endswith(checkpoint.encode()))

    def test_locked_descendant_prevents_migration_without_unlinking_lock(self):
        lockdir = self.home / 'thread-writer-locks'
        lockdir.mkdir()
        path = lockdir / (self.child + '.lock')
        with path.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            inode = path.stat().st_ino
            with self.assertRaises(HandoffError): self.prepare()
            self.assertEqual(path.stat().st_ino, inode)

    def test_partial_publication_recovers_before_retry(self):
        real_copy = switch.private_copy
        def fail_child(source, target):
            if str(source).endswith('.staged') and Path(target) == self.path(self.child):
                raise OSError('synthetic interrupted publish')
            real_copy(source, target)
        with patch.object(switch, 'private_copy', side_effect=fail_child), self.assertRaises(OSError):
            self.prepare()
        self.assertTrue(list(self.directory.glob('*.provider-recovery.json')))
        self.prepare()
        self.assertFalse(list(self.directory.glob('*.provider-recovery.json')))
        self.assertEqual(self.con.execute('SELECT model_provider FROM threads WHERE id=?', (self.child,)).fetchone()[0], 'cs_proxy')

    def test_malformed_and_outside_home_rollouts_are_not_modified(self):
        old = self.path(self.root).read_bytes()
        self.path(self.child).write_bytes(b'not-json\n')
        with self.assertRaises(HandoffError): self.prepare()
        self.assertEqual(self.path(self.root).read_bytes(), old)
        self.con.execute('UPDATE threads SET rollout_path=? WHERE id=?', (str(Path(self.temp.name) / 'outside.jsonl'), self.child))
        self.con.commit()
        with self.assertRaises(HandoffError): self.prepare()

    def test_repair_option_is_consumed_and_literal_prompts_preserved(self):
        with patch.object(accounts, 'launch') as launch:
            accounts.native_cli(['resume', '--repair-history', self.root, '--', '--repair-history'])
            self.assertTrue(launch.call_args.kwargs['repair_history'])
            self.assertEqual(launch.call_args.args[1], ['resume', self.root, '--', '--repair-history'])

    def test_descendant_model_uses_requested_model(self):
        with patch.object(accounts.CSDir, 'APP', self.home):
            (self.home / 'config.toml').write_text('model="official-default"')
            self.assertEqual(accounts.resume_model({}, []), 'official-default')
            self.assertEqual(accounts.resume_model({'model': 'gateway-model'}, ['-m', 'chosen']), 'chosen')
            self.assertEqual(accounts.resume_model({}, ['--config=model="config-model"']), 'config-model')


if __name__ == '__main__':
    unittest.main()
