import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from edge_form_graph.cli import operate
from edge_form_graph.contracts import ContractError
from tests.test_graph import Model, snapshot, receipt


class SaveDraftTests(unittest.TestCase):
    def test_save_requires_review_and_unchanged_fresh_fields(self):
        with tempfile.TemporaryDirectory() as d, patch('edge_form_graph.cli.CodexJsonModel', return_value=Model()):
            root = Path(d)
            def put(name, data):
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps(data), encoding='utf-8')
                return str(path)
            before = put('before.json', snapshot())
            profile = put('profile.json', {'school': '示例大学'})
            operate(SimpleNamespace(action='start', run_dir=d, profile=profile, snapshot=before, allow_save=False, model=None))
            args = SimpleNamespace(action='save', run_dir=d, snapshot=before)
            with self.assertRaises(ContractError):
                operate(args)
            command = json.loads((root / 'request.json').read_text(encoding='utf-8'))['command']
            result = receipt(command)
            put('receipt.json', result)
            put('writer/' + command['command_id'] + '.json', {'receipt': result})
            state = operate(SimpleNamespace(action='resume', run_dir=d, receipt=None))
            self.assertEqual(state['status'], 'verified_draft')
            with self.assertRaisesRegex(ContractError, 'reviewed_fields_changed'):
                operate(args)
            args.snapshot = put('after.json', result['snapshot'])
            self.assertEqual(operate(args)['status'], 'awaiting_save')


if __name__ == '__main__':
    unittest.main()
