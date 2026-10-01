"""Synthetic/local-loopback only: never contact a real model or read credentials."""
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from edge_form_graph.isolation import probe
from edge_form_graph.model import CHATGPT_CODEX_BASE_URL, DISABLED_FEATURES, HTTP_PROVIDER_ID, worker_args


class WorkerIsolationTests(unittest.TestCase):
    def test_worker_preserves_tool_free_contract_without_unsupported_builtin_override(self):
        args = worker_args('codex', 'WORKER', 'SCHEMA', 'OUTPUT', 'gpt-6-luna')
        overrides = [args[i+1] for i, arg in enumerate(args[:-1]) if arg == '-c']
        self.assertNotIn('model_providers.openai.supports_websockets=false', overrides)
        self.assertFalse(any('features.responses_websockets' in value for value in overrides))
        self.assertIn(f'model_provider="{HTTP_PROVIDER_ID}"', overrides)
        self.assertIn(f'model_providers.{HTTP_PROVIDER_ID}.name="OpenAI"', overrides)
        self.assertIn(f'model_providers.{HTTP_PROVIDER_ID}.base_url="{CHATGPT_CODEX_BASE_URL}"', overrides)
        self.assertIn(f'model_providers.{HTTP_PROVIDER_ID}.requires_openai_auth=true', overrides)
        self.assertIn(f'model_providers.{HTTP_PROVIDER_ID}.supports_websockets=false', overrides)
        for feature in DISABLED_FEATURES:
            self.assertIn(f'features.{feature}=false', overrides)
        for item in ('--ignore-user-config', '--ephemeral', '--skip-git-repo-check'):
            self.assertIn(item, args)
        self.assertEqual(args[args.index('--sandbox')+1], 'read-only')
        self.assertEqual(args[args.index('--model')+1], 'gpt-6-luna')
        self.assertIn('mcp_servers={}', overrides)
        self.assertIn('project_doc_max_bytes=0', overrides)

    def test_mock_child_has_temporary_home_and_no_auth_environment(self):
        observed = {}
        def fake_run(args, **kwargs):
            env = kwargs['env']
            observed['home'] = env['CODEX_HOME']
            self.assertTrue(Path(env['CODEX_HOME']).is_dir())
            self.assertEqual([k for k in env if k.upper().startswith(('OPENAI_', 'CODEX_'))], ['CODEX_HOME'])
            self.assertNotIn('features.responses_websockets=false', args)
            self.assertNotIn('features.responses_websockets_v2=false', args)
            self.assertNotIn('model_provider="openai"', args)  # proves the default provider
            return SimpleNamespace(returncode=1, stderr='synthetic failure')
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-secret', 'CODEX_API_KEY': 'synthetic-secret',
                                     'OPENAI_BASE_URL': 'https://invalid.test', 'CODEX_HOME': 'synthetic-global-home'}), \
                patch('edge_form_graph.isolation.subprocess.run', side_effect=fake_run):
            result = probe('gpt-6-luna')
            self.assertEqual(os.environ['CODEX_HOME'], 'synthetic-global-home')
            self.assertNotIn('synthetic-secret', str(result))
        self.assertFalse(Path(observed['home']).exists())

    @unittest.skipUnless(shutil.which('codex'), 'Installed CLI required for local contract')
    def test_installed_custom_provider_uses_sse_without_tools_or_auth(self):
        result = probe('gpt-6-luna')
        self.assertEqual(result['codex_exit'], 0, result)
        self.assertTrue(result['structured_output'], result)
        self.assertEqual(result['tools'], [], result)
        self.assertEqual(result['model'], 'gpt-6-luna', result)
        self.assertEqual(result['post_count'], 1, result)
        self.assertEqual(result['websocket_attempts'], 0, result)
        self.assertFalse(result['auth_header_present'], result)

    @unittest.skipUnless(shutil.which('codex'), 'Installed CLI required for local contract')
    def test_installed_cli_rejects_builtin_provider_override_before_request(self):
        def overridden(*args, **kwargs):
            return worker_args(*args, **kwargs) + ['-c', 'model_providers.openai.supports_websockets=false']
        with patch('edge_form_graph.isolation.worker_args', side_effect=overridden):
            result = probe('gpt-6-luna')
        self.assertNotEqual(result['codex_exit'], 0)
        self.assertEqual(result['config_error'], 'reserved_builtin_provider', result)
        self.assertEqual(result['post_count'], 0, result)
        self.assertEqual(result['websocket_attempts'], 0, result)
        self.assertFalse(result['auth_header_present'], result)


if __name__ == '__main__':
    unittest.main()
