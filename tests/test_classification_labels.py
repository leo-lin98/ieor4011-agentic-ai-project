import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from propose.classification import __main__ as classifier


class ClassificationLabelsTest(TestCase):
    def test_new_and_legacy_runs_use_their_own_choices(self):
        manifest = {'events': [{'event_uid': 'k', 'title': 'K', 'candidates': [
            {'candidate_uid': 'p', 'candidate_title': 'P'}]}]}
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sample = root / 'sample.json'
            sample.write_text(json.dumps(manifest))
            for legacy in (False, True):
                output = root / ('old.json' if legacy else 'new.json')
                if legacy:
                    output.write_text(json.dumps({'manifest': manifest, 'variant': 'titles', 'model': 'test',
                                                  'instructions': 'old prompt', 'results': []}))
                calls = []
                class Decisions:
                    def create(self, **kwargs):
                        calls.append(kwargs)
                        return SimpleNamespace(answers=[SimpleNamespace(type='choice', choice='test', confidence=0.5)], usage=None)
                args = SimpleNamespace(manifest=str(sample), output=str(output), max_pairs=10, variant='titles',
                                       model='test', instructions=None, retry_failed=False, profiles='')
                with patch.dict(os.environ, {'OPENAI_API_KEY': 'test'}), patch('openai.OpenAI', return_value=SimpleNamespace(decisions=Decisions())):
                    classifier.run(args)
                question = calls[0]['questions'][0]
                self.assertEqual([x['value'] for x in question['choices']], classifier.LEGACY_LABELS if legacy else classifier.LABELS)
                self.assertEqual(question['instructions'], 'old prompt' if legacy else classifier.INSTRUCTIONS)
