import unittest
from unittest.mock import patch

from edge_form_graph.runtime import components, review_model


class ModelRoutingTests(unittest.TestCase):
    def test_semantic_and_independent_review_use_explicit_luna(self):
        with patch('edge_form_graph.runtime.local_config', return_value={}):
            _, semantic = components()
            reviewer = review_model()
        self.assertEqual(semantic.model, 'gpt-6-luna')
        self.assertEqual(reviewer.model, 'gpt-6-luna')
        self.assertIsNot(semantic, reviewer)

    def test_wrong_review_model_is_not_a_silent_fallback(self):
        with patch('edge_form_graph.runtime.local_config', return_value={'review_model': 'other'}):
            with self.assertRaisesRegex(ValueError, 'review_model_must_be'):
                review_model()
