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
