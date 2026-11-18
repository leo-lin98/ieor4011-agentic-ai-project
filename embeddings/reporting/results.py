"""Read retrieval results without loading the entire file into memory."""
import json


def iter_results(path):
    # Decode one result at a time so large retrieval files need not fit in memory.
    decoder = json.JSONDecoder()
    with open(path, encoding='utf-8') as f:
        buffer = ''; eof = False
        def read_more():
            nonlocal buffer, eof
            chunk = f.read(1024 * 1024)
            if not chunk: eof = True
            buffer += chunk
        def decode():
            nonlocal buffer
            while True:
                try:
                    obj, end = decoder.raw_decode(buffer.lstrip())
                    buffer = buffer[len(buffer) - len(buffer.lstrip()) + end:]
                    return obj
                except json.JSONDecodeError:
                    if eof: raise
                    read_more()
        read_more()
        prefix = '{"metadata":'
        if not buffer.startswith(prefix): raise ValueError('Expected streamed retrieval output')
        buffer = buffer[len(prefix):]
        delimiter = ',"results":['
        while delimiter not in buffer:
            if eof: raise ValueError('Missing results array')
            read_more()
        pos = buffer.index(delimiter)
        meta = json.loads(buffer[:pos])
        buffer = buffer[pos + len(delimiter):]
        yield meta
        while True:
            buffer = buffer.lstrip()
            if not buffer and not eof: read_more(); continue
            if buffer.startswith(']'): break
            if buffer.startswith(','): buffer = buffer[1:].lstrip()
            yield decode()
