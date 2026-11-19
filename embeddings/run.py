"""Frozen run creation, EmbeddingGemma encoding, and exact cosine retrieval."""

import fcntl
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .catalog import Catalog, PLATFORMS, RULE_INPUT, TEXT_FIELDS, digest, input_parts, same_group

MODEL = "google/embeddinggemma-300m"
REVISION = "57c266a740f537b4dc058e1b0cda161fd15afa75"
TOP_K = 10
RUN_ID = re.compile(r"run-(\d{3,})", re.ASCII)
CANDIDATES = pa.schema([
    ("run_id", pa.string()), ("kalshi_market_uid", pa.string()),
    ("polyus_market_uid", pa.string()), ("similarity_score", pa.float32()),
])
log = logging.getLogger(__name__)


def run_number(run_id):
    if not isinstance(run_id, str) or not (match := RUN_ID.fullmatch(run_id)):
        raise ValueError("Run ID must use the form run-001")
    number = int(match[1])
    if number < 1 or run_id != f"run-{number:03}":
        raise ValueError("Run ID must use the form run-001 with at least three digits")
    return number


def reserve_run(root, requested=None):
    root.mkdir(parents=True, exist_ok=True)
    counter = root / ".last-run-number"
    with (root / ".run-id.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        highest = int(counter.read_text()) if counter.exists() else 0
        for path in root.iterdir():
            match = RUN_ID.fullmatch(path.name)
            if match:
                highest = max(highest, int(match[1]))
        number = run_number(requested) if requested else highest + 1
        if number <= highest:
            raise ValueError(f"Run ID must exceed run-{highest:03}")
        with tempfile.NamedTemporaryFile(dir=root, prefix=".run-counter-", mode="w", delete=False) as stream:
            temporary = stream.name
            try:
                stream.write(f"{number}\n")
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temporary, counter)
                directory_fd = os.open(root, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return f"run-{number:03}"


def freeze_catalog(source, destination):
    paths = [source / f"{platform}.parquet" for platform in PLATFORMS]

    def identity(path):
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    before = [identity(path) for path in paths]
    destination.mkdir()
    for path in paths:
        shutil.copyfile(path, destination / path.name)
    if before != [identity(path) for path in paths]:
        raise ValueError("Collector catalog changed during capture. Retry once the current sweep finishes.")


class Embedder:
    def __init__(self, device, batch_size, truncation):
        from huggingface_hub import snapshot_download
        from sentence_transformers import SentenceTransformer

        try:
            path = snapshot_download(MODEL, revision=REVISION, local_files_only=True)
        except FileNotFoundError as exc:
            raise ValueError(f"Cache {MODEL} at revision {REVISION} before running embeddings") from exc
        self.model = SentenceTransformer(path, device=device, local_files_only=True)
        self.tokenizer = self.model.tokenizer
        self.tokenizer.truncation_side = "right"
        self.limit = self.model.max_seq_length
        dimension = getattr(self.model, "get_embedding_dimension", self.model.get_sentence_embedding_dimension)
        self.dimension = dimension()
        self.batch_size = batch_size
        self.truncation = truncation
        self.prompts = dict(zip(PLATFORMS, (self.model.prompts["query"], self.model.prompts["document"])))

    def head_tail(self, prefix, rules, prompt):
        if not rules:
            return prefix
        rule_label, rule_text = rules.split(": ", 1)
        stem = "\n".join(part for part in (prefix, f"{rule_label}: ") if part)
        available = self.limit - len(self.tokenizer(prompt + stem, verbose=False)["input_ids"])
        if available < 2:
            return stem
        offsets = self.tokenizer(rule_text, add_special_tokens=False, return_offsets_mapping=True,
                                 verbose=False)["offset_mapping"]
        available = min(available, len(offsets))
        while available >= 2:
            head = (available + 1) // 2
            tail = available // 2
            text = stem + rule_text[:offsets[head - 1][1]] + " … " + rule_text[offsets[-tail][0]:]
            if len(self.tokenizer(prompt + text, verbose=False)["input_ids"]) <= self.limit:
                return text
            available -= 1
        return stem

    def encode(self, platform, markets, uids):
        prompt = self.prompts[platform]
        encode = self.model.encode_query if platform == "KALSHI" else self.model.encode_document
        vectors = np.empty((len(uids), self.dimension), dtype=np.float32)
        truncated = {}
        for start in range(0, len(uids), 256):
            batch = uids[start:start + 256]
            parts = [input_parts(markets[uid]) for uid in batch]
            texts = ["\n".join(filter(None, fields)) for fields in parts]
            tokens = self.tokenizer([prompt + text for text in texts], truncation=False,
                                    return_length=True, verbose=False)["length"]
            for index, (uid, size) in enumerate(zip(batch, tokens)):
                if size > self.limit:
                    truncated[uid] = size
                    if self.truncation == "head-tail":
                        texts[index] = self.head_tail(*parts[index], prompt)
            vectors[start:start + len(batch)] = encode(
                texts, prompt=prompt, batch_size=self.batch_size,
                normalize_embeddings=True, show_progress_bar=False,
            )
            if start % 4096 == 0:
                log.info("Embedded %s %d/%d", platform, start + len(batch), len(uids))
        if not np.isfinite(vectors).all():
            raise ValueError(f"Non-finite embeddings produced for {platform}")
        return vectors, truncated

    def settings(self):
        return {
            "id": MODEL, "revision": REVISION, "prompts": self.prompts,
            "device": str(self.model.device), "batch_size": self.batch_size, "dtype": "float32",
            "versions": {name: version(name) for name in ("sentence-transformers", "transformers", "torch", "numpy")},
        }


def top_indices(scores):
    """UID-sorted candidates make indices the stable tie breaker."""
    if len(scores) <= TOP_K:
        selected = np.arange(len(scores))
    else:
        cutoff = np.partition(scores, -TOP_K)[-TOP_K]
        better = np.flatnonzero(scores > cutoff)
        tied = np.flatnonzero(scores == cutoff)[:TOP_K - len(better)]
        selected = np.concatenate((better, tied))
    return selected[np.lexsort((selected, -scores[selected]))]


def rank_batches(run_id, uids, vectors, groups):
    query_uids, candidate_uids = (uids[platform] for platform in PLATFORMS)
    queries, candidates = (vectors[platform] for platform in PLATFORMS)
    query_groups, candidate_groups = (groups[platform] for platform in PLATFORMS)
    for group in (True, False, None):
        query_positions = [i for i, uid in enumerate(query_uids) if query_groups[uid] == group]
        candidate_positions = [i for i, uid in enumerate(candidate_uids) if same_group(group, candidate_groups[uid])]
        if not query_positions or not candidate_positions:
            continue
        pool = candidates[candidate_positions]
        pool_uids = [candidate_uids[i] for i in candidate_positions]
        for start in range(0, len(query_positions), 128):
            positions = query_positions[start:start + 128]
            scores = queries[positions] @ pool.T
            rows = []
            for position, row_scores in zip(positions, scores):
                for i in top_indices(row_scores):
                    rows.append({
                        "run_id": run_id, "kalshi_market_uid": query_uids[position],
                        "polyus_market_uid": pool_uids[i], "similarity_score": float(row_scores[i]),
                    })
            yield rows


def create_run(root, source, requested, device, batch_size, truncation):
    run_id = reserve_run(root, requested)
    created_at = datetime.now(UTC).isoformat()
    log.info("Reserved %s", run_id)
    with tempfile.TemporaryDirectory(dir=root, prefix=f".{run_id}-") as temp:
        work = Path(temp)
        freeze_catalog(source, work / "catalog")
        catalog = Catalog(work / "catalog")
        embedder = Embedder(device, batch_size, truncation)
        vectors = {}
        uids = {}
        truncated = {}
        for platform in PLATFORMS:
            uids[platform] = sorted(catalog.groups[platform])
            vectors[platform], truncated[platform] = embedder.encode(
                platform, catalog.markets[platform], uids[platform],
            )
        candidate_path = work / "candidates.parquet"
        count = 0
        pending = []
        with pq.ParquetWriter(candidate_path, CANDIDATES, compression="zstd") as writer:
            for rows in rank_batches(run_id, uids, vectors, catalog.groups):
                pending.extend(rows)
                if len(pending) >= 20_000:
                    writer.write_table(pa.Table.from_pylist(pending, schema=CANDIDATES))
                    count += len(pending)
                    pending.clear()
            if pending:
                writer.write_table(pa.Table.from_pylist(pending, schema=CANDIDATES))
                count += len(pending)
        metadata = {
            "run_id": run_id, "created_at": created_at, "model": embedder.settings(),
            "input": {"fields": dict(TEXT_FIELDS), "Rules": RULE_INPUT,
                      "deduplication": "case-folded, collapsed whitespace, first field wins"},
            "truncation": {"strategy": truncation, "limit": embedder.limit,
                           "market_counts": {p: len(rows) for p, rows in truncated.items()},
                           "original_token_counts": truncated},
            "similarity": "cosine",
            "selection": {
                "top_k": TOP_K, "tie_break": "polyus_market_uid ascending",
                "open_market_source": "latest sweep, canonical open, close time after snapshot cutoff if present",
                "category_source": "latest sweep by parent event",
                "category_policy": "same sports status, missing allowed, conflicting events excluded",
            },
            "catalog": {"as_of_ms": catalog.as_of,
                        "platforms": {p: {"sha256": digest(catalog.path(p)), "pulled_ms": catalog.latest[p]}
                                      for p in PLATFORMS}},
            "categories": catalog.categories, "embedded_markets": {p: len(uids[p]) for p in PLATFORMS},
            "candidate_rows": count,
        }
        (work / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        validate_candidates(candidate_path, run_id, count)
        destination = root / run_id
        work.rename(destination)
    return destination


def validate_candidates(path, run_id, count):
    parquet = pq.ParquetFile(path)
    if parquet.schema_arrow != CANDIDATES or parquet.metadata.num_rows != count:
        raise ValueError(f"Invalid candidate schema or row count for {run_id}")
    for batch in parquet.iter_batches(columns=["run_id"]):
        if any(value != run_id for value in batch.column(0).to_pylist()):
            raise ValueError(f"Candidate row run ID does not match {run_id}")


class Run:
    def __init__(self, root, run_id):
        run_number(run_id)
        path = root / run_id
        self.id = run_id
        self.metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        if self.metadata["run_id"] != path.name:
            raise ValueError(f"Metadata run ID does not match {path.name}")
        self.candidate_path = path / "candidates.parquet"
        validate_candidates(self.candidate_path, run_id, self.metadata["candidate_rows"])
        for platform in PLATFORMS:
            frozen = path / "catalog" / f"{platform}.parquet"
            if digest(frozen) != self.metadata["catalog"]["platforms"][platform]["sha256"]:
                raise ValueError(f"Invalid frozen catalog hash for {platform} in {run_id}")
        self.catalog = Catalog(path / "catalog")

    def results(self, kalshi_uids):
        if not kalshi_uids:
            return []
        filters = [("kalshi_market_uid", "in", sorted(kalshi_uids))]
        return pq.read_table(self.candidate_path, filters=filters).to_pylist()
