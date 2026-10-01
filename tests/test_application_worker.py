import io
import json
import unittest
from unittest.mock import patch

from edge_form_graph.application_worker import serve


class WorkerTests(unittest.TestCase):
    def test_sequential_requests_use_separate_arguments_and_responses(self):
        source = io.StringIO('\n'.join(json.dumps({'id': i, 'args': [a]})
                                      for i, a in [(1, 'status'), (2, 'resume')]) + '\n')
        result = io.StringIO()
        def fake(args):
            print(json.dumps({'action': args[0]}))
        with patch('edge_form_graph.application_worker.main', side_effect=fake) as cli:
            serve(source, result)
        self.assertEqual(cli.call_args_list[0].args, (['status'],))
        self.assertEqual([json.loads(s) for s in result.getvalue().splitlines()],
                         [{'id': 1, 'result': {'action': 'status'}},
                          {'id': 2, 'result': {'action': 'resume'}}])

    def test_failed_command_is_reported_once_and_never_replayed(self):
        source = io.StringIO('{"id":1,"args":["resume"]}\n{"id":2,"args":["status"]}\n')
        result = io.StringIO()
        def fake(args):
            if args == ['resume']:
                raise ValueError('needs_reconciliation')
            print('{"settled": false}')
        with patch('edge_form_graph.application_worker.main', side_effect=fake) as cli:
            serve(source, result)
        messages = [json.loads(s) for s in result.getvalue().splitlines()]
        self.assertEqual(messages[0]['error'], 'needs_reconciliation')
        self.assertEqual(messages[1]['result'], {'settled': False})
        self.assertEqual(cli.call_count, 2)
