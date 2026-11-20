import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import storage

class Transactions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        storage.DB=str(Path(self.tmp.name)/'test.db')
        storage.init()

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, fail=False):
        return storage.create_session(5,600,fail,True,'a'*32)

    def test_entity_and_outbox_commit_together(self):
        event=self.event()
        self.assertEqual(storage.rows('SELECT id FROM sessions')[0]['id'],event['id'])
        self.assertEqual(json.loads(storage.rows('SELECT body FROM outbox')[0]['body'])['id'],event['id'])

    def test_outbox_error_rolls_back_entity(self):
        with patch.object(storage,'enqueue',side_effect=RuntimeError('DB failure')):
            with self.assertRaises(RuntimeError):
                self.event()
        self.assertEqual(storage.rows('SELECT * FROM sessions'),[])
        self.assertEqual(storage.rows('SELECT * FROM outbox'),[])

    def test_duplicate_event_does_not_double_projection(self):
        event=self.event()
        self.assertEqual(storage.project(event),'results')
        self.assertEqual(storage.project(event),'duplicate')
        self.assertEqual(len(storage.rows('SELECT * FROM projection')),1)
        self.assertEqual(len(storage.rows("SELECT * FROM outbox WHERE destination='results'")),1)

    def test_failed_write_has_no_projection_and_has_failure_outbox(self):
        event=self.event(True)
        self.assertEqual(storage.project(event),'session.dlq')
        self.assertEqual(storage.rows('SELECT * FROM projection'),[])
        self.assertEqual(len(storage.rows("SELECT * FROM outbox WHERE destination='session.dlq'")),1)

    def test_compensation_is_idempotent(self):
        event=self.event(True)
        storage.finish(event,True)
        storage.finish(event,True)
        storage.finish(event,False)
        self.assertEqual(storage.rows('SELECT status FROM sessions')[0]['status'],'compensated')

    def test_response_outbox_failure_rolls_back_inbox_and_projection(self):
        event=self.event()
        with patch.object(storage,'enqueue',side_effect=RuntimeError('DB failure')):
            with self.assertRaises(RuntimeError):
                storage.project(event)
        self.assertEqual(storage.rows('SELECT * FROM inbox'),[])
        self.assertEqual(storage.rows('SELECT * FROM projection'),[])

if __name__=='__main__':
    unittest.main()
