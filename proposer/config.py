"""Static configuration for the proposer."""
from collector.config import DATA
from proposer.llm import ReasoningEffortName

MODEL = 'gpt-6-luna'
CANDIDATES_SENT = 3
CONCURRENCY = 16
MAX_OUTPUT_TOKENS = 16000
REASONING_EFFORT: ReasoningEffortName = 'medium'
API_MAX_RETRIES = 5
API_TIMEOUT_S = 120
PROPOSALS_DIR = DATA / 'proposals'
