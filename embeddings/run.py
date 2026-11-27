"""Frozen run creation, local encoding, and exact cosine retrieval."""

import fcntl
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .catalog import Catalog, PLATFORMS, RULE_INPUT, TEXT_FIELDS, digest, input_parts, same_group

DEFAULT_MODEL = "embeddinggemma"
MODEL_REVISIONS = {
    DEFAULT_MODEL: ("google/embeddinggemma-300m", "57c266a740f537b4dc058e1b0cda161fd15afa75"),
    "nomic": ("nomic-ai/nomic-embed-text-v1.5", "e9b6763023c676ca8431644204f50c2b100d9aab"),
}
TOP_K = 10
EMBED_CHUNK = 256
RANK_CHUNK = 128
CHECKPOINT_VERSION = 1
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


def freeze_catalog(source, destination, store):
    paths = [source / f"{platform}.parquet" for platform in PLATFORMS]

    def identity(path):
        stat = path.stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    before = [identity(path) for path in paths]
    destination.mkdir()
    store.mkdir(exist_ok=True)
    hashes = [digest(path) for path in paths]
    for path, sha256 in zip(paths, hashes):
        shared = store / f"{sha256}.parquet"
        if not shared.exists():
            with tempfile.TemporaryDirectory(dir=store, prefix=".capture-") as temp:
                copy = Path(temp) / path.name
                shutil.copyfile(path, copy)
                if digest(copy) != sha256:
                    raise ValueError(f"Catalog changed during capture: {path}")
                copy.chmod(0o444)
                try:
                    os.link(copy, shared)
                except FileExistsError:
                    pass
        os.link(shared, destination / path.name)
    if before != [identity(path) for path in paths]:
        raise ValueError("Collector catalog changed during capture. Retry once the current sweep finishes.")
    return dict(zip(PLATFORMS, hashes))


class Embedder:
    def __init__(self, device, batch_size, truncation, model=DEFAULT_MODEL):
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError
        from sentence_transformers import SentenceTransformer

        self.model_id, self.revision = MODEL_REVISIONS[model]
        try:
            path = snapshot_download(self.model_id, revision=self.revision, local_files_only=True)
        except LocalEntryNotFoundError as exc:
            raise ValueError(f"Cache {self.model_id} at revision {self.revision} before running embeddings") from exc
        self.model = SentenceTransformer(path, device=device, local_files_only=True)
        if model == "nomic":
            self.model.max_seq_length = 1024
            self.prompts = {"KALSHI": "search_query: ", "POLYUS": "search_document: "}
        else:
            self.prompts = dict(zip(PLATFORMS, (self.model.prompts["query"], self.model.prompts["document"])))
        self.tokenizer = self.model.tokenizer
        self.tokenizer.truncation_side = "right"
        self.limit = self.model.max_seq_length
        dimension = getattr(self.model, "get_embedding_dimension", self.model.get_sentence_embedding_dimension)
        self.dimension = dimension()
        self.batch_size = batch_size
        self.truncation = truncation

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
        parts = [input_parts(markets[uid]) for uid in uids]
        texts = ["\n".join(filter(None, fields)) for fields in parts]
        tokens = self.tokenizer([prompt + text for text in texts], truncation=False,
                                return_length=True, verbose=False)["length"]
        truncated = {}
        for index, (uid, size) in enumerate(zip(uids, tokens)):
            if size > self.limit:
                truncated[uid] = size
                if self.truncation == "head-tail":
                    texts[index] = self.head_tail(*parts[index], prompt)
        vectors = encode(texts, prompt=prompt, batch_size=self.batch_size,
                         normalize_embeddings=True, show_progress_bar=False)
        return vectors, truncated

    def settings(self):
        return {
            "id": self.model_id, "revision": self.revision, "prompts": self.prompts,
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


def rank_batches(run_id, uids, vectors, groups, skip=0):
    query_uids, candidate_uids = (uids[platform] for platform in PLATFORMS)
    queries, candidates = (vectors[platform] for platform in PLATFORMS)
    query_groups, candidate_groups = (groups[platform] for platform in PLATFORMS)
    for group in (True, False, None):
        query_positions = [i for i, uid in enumerate(query_uids) if query_groups[uid] == group]
        candidate_positions = [i for i, uid in enumerate(candidate_uids) if same_group(group, candidate_groups[uid])]
        if not query_positions or not candidate_positions:
            continue
        batch_count = (len(query_positions) + RANK_CHUNK - 1) // RANK_CHUNK
        if skip >= batch_count:
            skip -= batch_count
            continue
        pool = candidates[candidate_positions]
        pool_uids = [candidate_uids[i] for i in candidate_positions]
        for start in range(skip * RANK_CHUNK, len(query_positions), RANK_CHUNK):
            positions = query_positions[start:start + RANK_CHUNK]
            scores = queries[positions] @ pool.T
            rows = []
            for position, row_scores in zip(positions, scores):
                for i in top_indices(row_scores):
                    rows.append({
                        "run_id": run_id, "kalshi_market_uid": query_uids[position],
                        "polyus_market_uid": pool_uids[i], "similarity_score": float(row_scores[i]),
                    })
            yield rows
        skip = 0


def create_run(root, source, requested, device, batch_size, truncation, model=DEFAULT_MODEL):
    run_id = reserve_run(root, requested)
    log.info("Reserved %s", run_id)
    work = root / f".{run_id}.checkpoint"
    with tempfile.TemporaryDirectory(dir=root, prefix=f".{run_id}-") as temp:
        staging = Path(temp)
        output = staging / "output"
        output.mkdir()
        hashes = freeze_catalog(source, output / "catalog", root / ".catalogs")
        manifest = {
            "format_version": CHECKPOINT_VERSION, "run_id": run_id,
            "created_at": datetime.now(UTC).isoformat(), "hashes": hashes,
            "device": device, "batch_size": batch_size, "truncation": truncation, "model": model,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        staging.rename(work)
    log.info("Resume this run with --resume %s", run_id)
    return resume_run(root, run_id)


def encode_platform(db, embedder, platform, markets, uids):
    vectors = np.empty((len(uids), embedder.dimension), dtype=np.float32)
    truncated = {}
    saved_count = db.execute(
        "SELECT count(*) FROM embeddings WHERE platform = ?", (platform,),
    ).fetchone()[0]
    if saved_count:
        log.info("Restoring %s embeddings %d/%d", platform, min(saved_count * EMBED_CHUNK, len(uids)), len(uids))
    for index, start in enumerate(range(0, len(uids), EMBED_CHUNK)):
        batch = uids[start:start + EMBED_CHUNK]
        saved = db.execute(
            "SELECT vectors, truncated FROM embeddings WHERE platform = ? AND batch = ?", (platform, index),
        ).fetchone()
        if saved is None:
            chunk, counts = embedder.encode(platform, markets, batch)
            chunk = np.asarray(chunk, dtype="<f4")
            if chunk.shape != (len(batch), embedder.dimension) or not np.isfinite(chunk).all():
                raise ValueError(f"Invalid embeddings produced for {platform}, batch {index}")
            with db:
                db.execute("INSERT INTO embeddings VALUES (?, ?, ?, ?)",
                           (platform, index, chunk.tobytes(), json.dumps(counts)))
            if index % 16 == 0 or start + len(batch) == len(uids):
                log.info("Embedded %s %d/%d", platform, start + len(batch), len(uids))
        else:
            data, counts_json = saved
            chunk = np.frombuffer(data, dtype="<f4").reshape(len(batch), embedder.dimension)
            counts = json.loads(counts_json)
        vectors[start:start + len(batch)] = chunk
        truncated.update(counts)
    return vectors, truncated


def write_candidates(db, candidate_path, run_id, uids, vectors, groups):
    saved_count = db.execute("SELECT count(*) FROM ranks").fetchone()[0]
    if saved_count:
        log.info("Restoring %d ranked blocks", saved_count)
    for index, rows in enumerate(rank_batches(run_id, uids, vectors, groups, skip=saved_count), saved_count):
        table = pa.Table.from_pylist(rows, schema=CANDIDATES)
        buffer = pa.BufferOutputStream()
        pq.write_table(table, buffer, compression="zstd")
        with db:
            db.execute("INSERT INTO ranks VALUES (?, ?)", (index, buffer.getvalue().to_pybytes()))
        if index % 32 == 0:
            log.info("Ranked %d blocks", index + 1)
    count = buffered = 0
    pending = []
    with pq.ParquetWriter(candidate_path, CANDIDATES, compression="zstd") as writer:
        for (data,) in db.execute("SELECT parquet FROM ranks ORDER BY batch"):
            table = pq.ParquetFile(pa.BufferReader(data)).read()
            pending.append(table)
            buffered += table.num_rows
            if buffered >= 20_000:
                writer.write_table(pa.concat_tables(pending))
                count += buffered
                pending.clear()
                buffered = 0
        if pending:
            writer.write_table(pa.concat_tables(pending))
            count += buffered
    return count


def resume_run(root, run_id):
    run_number(run_id)
    destination = root / run_id
    work = root / f".{run_id}.checkpoint"
    if destination.exists():
        raise ValueError(f"{run_id} is already complete")
    if not work.is_dir():
        raise ValueError(f"No resumable checkpoint found for {run_id}")
    with (work / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"{run_id} is already running") from exc
        if destination.exists():
            raise ValueError(f"{run_id} is already complete")
        output = work / "output"
        manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        if manifest["format_version"] != CHECKPOINT_VERSION or manifest["run_id"] != run_id:
            raise ValueError(f"Incompatible checkpoint for {run_id}")
        for platform, sha256 in manifest["hashes"].items():
            if digest(output / "catalog" / f"{platform}.parquet") != sha256:
                raise ValueError(f"Frozen catalog hash changed for {platform} in {run_id}")
        catalog = Catalog(output / "catalog")
        with closing(sqlite3.connect(work / "progress.sqlite")) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS model (settings TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS embeddings (
                    platform TEXT NOT NULL, batch INTEGER NOT NULL, vectors BLOB NOT NULL,
                    truncated TEXT NOT NULL, PRIMARY KEY (platform, batch)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS ranks (batch INTEGER PRIMARY KEY, parquet BLOB NOT NULL);
            """)
            previous = db.execute("SELECT settings FROM model").fetchone()
            saved = json.loads(previous[0]) if previous else None
            device = saved["settings"]["device"] if saved else manifest["device"]
            model = manifest.get("model", DEFAULT_MODEL)
            embedder = Embedder(device, manifest["batch_size"], manifest["truncation"], model)
            model_settings = {"settings": embedder.settings(), "dimension": embedder.dimension, "limit": embedder.limit}
            if saved is None:
                with db:
                    db.execute("INSERT INTO model VALUES (?)", (json.dumps(model_settings),))
            elif model_settings != saved:
                raise ValueError("Embedding settings changed. Restore the original model environment or start a new run.")
            uids = {platform: sorted(catalog.groups[platform]) for platform in PLATFORMS}
            vectors = {}
            truncated = {}
            for platform in PLATFORMS:
                vectors[platform], truncated[platform] = encode_platform(
                    db, embedder, platform, catalog.markets[platform], uids[platform],
                )
            candidate_path = output / "candidates.parquet"
            count = write_candidates(db, candidate_path, run_id, uids, vectors, catalog.groups)
        metadata = {
            "run_id": run_id, "created_at": manifest["created_at"], "model": model_settings["settings"],
            "input": {"fields": dict(TEXT_FIELDS), "Rules": RULE_INPUT,
                      "deduplication": "case-folded, collapsed whitespace, first field wins"},
            "truncation": {"strategy": manifest["truncation"], "limit": embedder.limit,
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
                        "platforms": {p: {"sha256": manifest["hashes"][p], "pulled_ms": catalog.latest[p]}
                                      for p in PLATFORMS}},
            "categories": catalog.categories, "embedded_markets": {p: len(uids[p]) for p in PLATFORMS},
            "candidate_rows": count,
        }
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        validate_candidates(candidate_path, run_id, count)
        output.rename(destination)
        shutil.rmtree(work)
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
