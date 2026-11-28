"""Read retrieval results without loading the entire file into memory."""
import json


def retrieval_direction(metadata):
    """Read the query and candidate venues, including legacy Kalshi-first runs."""
    query = metadata.get('query_platform', 'KALSHI')
    if query not in ('KALSHI', 'POLYUS'):
        raise ValueError('Unknown query venue in retrieval metadata')
    opposite = 'POLYUS' if query == 'KALSHI' else 'KALSHI'
    candidate = metadata.get('candidate_platform', opposite)
    if candidate != opposite:
        raise ValueError('Query and candidate venues must be different')
    return query, candidate


def iter_results(path):
    # Decode one result at a time so large retrieval files need not fit in memory.
    decoder = json.JSONDecoder()
    with open(path, encoding='utf-8') as f:
        buffer = ''; eof = False
        def read_more(size=1024 * 1024):
            nonlocal buffer, eof
            chunk = f.read(size)
            if not chunk: eof = True
            buffer += chunk
        def decode():
            nonlocal buffer
            read_size = 1024 * 1024
            while True:
                try:
                    obj, end = decoder.raw_decode(buffer.lstrip())
                    buffer = buffer[len(buffer) - len(buffer.lstrip()) + end:]
                    return obj
                except json.JSONDecodeError:
                    if eof: raise
                    # Compact full-catalog runs can place a large event lookup in metadata.
                    read_more(read_size)
                    read_size = min(read_size * 2, 16 * 1024 * 1024)
        read_more()
        prefix = '{"metadata":'
        if not buffer.startswith(prefix): raise ValueError('Expected streamed retrieval output')
        buffer = buffer[len(prefix):]
        meta = decode()
        delimiter = ',"results":['
        while len(buffer) < len(delimiter) and not eof:
            read_more()
        if not buffer.startswith(delimiter):
            raise ValueError('Missing results array')
        buffer = buffer[len(delimiter):]
        yield meta
        first = True
        while True:
            buffer = buffer.lstrip()
            if not buffer and not eof: read_more(); continue
            if buffer.startswith(']'):
                buffer = buffer[1:]
                break
            if not first:
                if not buffer.startswith(','): raise ValueError('Missing result separator')
                buffer = buffer[1:].lstrip()
                while not buffer and not eof: read_more(); buffer = buffer.lstrip()
                if buffer.startswith(']'): raise ValueError('Trailing comma in results')
            yield decode()
            first = False
        while not eof: read_more()
        if buffer.strip() != '}':
            raise ValueError('Invalid or incomplete retrieval output ending')
