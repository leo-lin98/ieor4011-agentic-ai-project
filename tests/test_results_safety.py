import json
import os
from pathlib import Path
import tempfile
import unittest

from embeddings.embed import atomic_output, validate_output_path
from embeddings.reporting.results import iter_results


class OutputSafetyTests(unittest.TestCase):
    def test_output_cannot_replace_inputs_or_hard_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles, cache, alias = (root / name for name in ('profiles', 'cache', 'alias'))
            profiles.write_text('profiles')
            cache.write_text('cache')
            os.link(profiles, alias)
            for output in (profiles, cache, alias):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    validate_output_path(output, profiles, cache)
            validate_output_path(root / 'results', profiles, cache)

    def test_failed_write_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'results.json'
            output.write_text('previous')
            with self.assertRaises(RuntimeError):
                with atomic_output(output) as stream:
                    stream.write('partial')
                    raise RuntimeError('interrupted')
            self.assertEqual(output.read_text(), 'previous')
            self.assertEqual(list(Path(tmp).iterdir()), [output])
            with atomic_output(output) as stream:
                stream.write('complete')
            self.assertEqual(output.read_text(), 'complete')


class ResultsReaderTests(unittest.TestCase):
    def test_valid_and_invalid_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'results.json'
            metadata = {'description': ',"results":['}
            valid = json.dumps({'metadata': metadata, 'results': [{'id': 1}, {'id': 2}]}, separators=(',', ':'))
            path.write_text(valid + ' \n')
            self.assertEqual(list(iter_results(path)), [metadata, {'id': 1}, {'id': 2}])
            for content in (valid[:-1], valid + 'junk', valid.replace('},{', '} {'),
                            valid.replace('}]}', '},]}')):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises((ValueError, json.JSONDecodeError)):
                        list(iter_results(path))


if __name__ == '__main__':
    unittest.main()
