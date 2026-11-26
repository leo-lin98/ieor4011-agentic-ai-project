"""Command line entry point: python -m proposer."""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI

from collector.config import ROOT
from proposer import config
from proposer.llm import OpenAIProposerClient
from proposer.runner import RunSummary, run

_STANDARD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {'message', 'asctime'}


class JsonLineFormatter(logging.Formatter):
    """Format each log record as one JSON line, keeping the fields passed through `extra=`."""

    def format(self, record: logging.LogRecord) -> str:
        """Return time, level, logger, message, every extra attribute and the exception text if any."""
        data: dict[str, object] = {
            'time': datetime.fromtimestamp(record.created, UTC).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        data.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRIBUTES})
        if record.exc_info:
            data['exception'] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


def main(argv: list[str]) -> None:
    """Propose matches for every Kalshi event in the candidates file and print the run summary."""
    parser = argparse.ArgumentParser(prog='proposer', description='Propose Kalshi/Polymarket US matches with an LLM.')
    parser.add_argument('--candidates', type=Path, default=ROOT / 'data' / 'embeddings' / 'candidates.json')
    parser.add_argument('--concurrency', type=int, default=config.CONCURRENCY)
    args = parser.parse_args(argv)
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        sys.exit('OPENAI_API_KEY is not set; export it before running the proposer (see .env.example)')
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLineFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    run_id = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    summary = asyncio.run(_run_with_openai(api_key, args.candidates, run_id, args.concurrency))
    print(f'run {run_id}: {summary}')


async def _run_with_openai(api_key: str, candidates_path: Path, run_id: str, concurrency: int) -> RunSummary:
    """Run the batch with an OpenAI client that is closed when the run ends, normally or not."""
    async with AsyncOpenAI(
        api_key=api_key, max_retries=config.API_MAX_RETRIES, timeout=config.API_TIMEOUT_S
    ) as openai_client:
        client = OpenAIProposerClient(
            client=openai_client, model=config.MODEL, reasoning_effort=config.REASONING_EFFORT,
            max_output_tokens=config.MAX_OUTPUT_TOKENS)
        return await run(client, candidates_path, config.PROPOSALS_DIR, run_id, concurrency)


if __name__ == '__main__':
    main(sys.argv[1:])
