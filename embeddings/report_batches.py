"""Render a large streamed retrieval result as a directory of smaller reports."""
import argparse
import base64
import gzip
import html
import json
from pathlib import Path
from datetime import datetime, timezone
from .report import render

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

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input'); p.add_argument('output_dir'); p.add_argument('--batch-size', type=int, default=100)
    p.add_argument('--start', type=int, default=1); p.add_argument('--limit', type=int, default=None)
    p.add_argument('--category', action='append', help='Include a Kalshi category (repeat for multiple categories)')
    p.add_argument('--compress', action='store_true', help='Compress report pages for modern browsers')
    p.add_argument('--volume-snapshots', help='Kalshi pricing snapshots directory')
    p.add_argument('--profiles', default='data/market_profiles/market_profiles.parquet')
    a = p.parse_args()
    if a.batch_size < 1 or a.start < 1 or (a.limit is not None and a.limit < 1): p.error('batch size, start and limit must be positive')
    out = Path(a.output_dir); out.mkdir(parents=True, exist_ok=True)
    iterator = iter_results(a.input); meta = next(iterator); batch = []; links = []; count = 0
    volume_by_event = {}; snapshot_time = None; index_rows = []
    if a.volume_snapshots:
        from .volume import event_volumes
        volume_by_event, snapshot_ms = event_volumes(a.volume_snapshots, a.profiles)
        snapshot_time = datetime.fromtimestamp(snapshot_ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    # Write each batch as a standalone report, optionally compressed for the browser.
    def write_batch():
        nonlocal batch
        first = a.start + count - len(batch)
        last = a.start + count - 1
        filename = f'events-{first:05d}-{last:05d}.html'
        content = render({'metadata': meta, 'results': batch})
        if a.compress:
            encoded = base64.b64encode(gzip.compress(content.encode('utf-8'), compresslevel=6)).decode('ascii')
            content = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Loading event review</title>'
                       '<p id="status">Loading report…</p><script>const encoded="' + encoded + '";'
                       'async function load(){try{const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));'
                       'const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));'
                       'const page=await new Response(stream).text();document.open();document.write(page);document.close();if(location.hash){const target=document.getElementById(location.hash.slice(1));if(target)target.scrollIntoView();}'
                       '}catch(e){document.getElementById("status").textContent="Could not open this compressed report. Try a current version of Chrome, Safari, or Firefox: "+e.message;}}load();</script></html>')
        (out / filename).write_text(content, encoding='utf-8')
        links.append(f'<li><a href="{html.escape(filename)}">Events {first:,}–{last:,}</a></li>')
        batch = []
    # Filter and paginate results, adding volume data for the optional index.
    selected = 0
    categories = {c.strip().casefold() for c in a.category or []}
    for item in iterator:
        if categories and (item['query'].get('category') or '').strip().casefold() not in categories: continue
        selected += 1
        if selected < a.start: continue
        if a.limit is not None and count >= a.limit: break
        count += 1
        query = item['query']
        if a.volume_snapshots:
            volume = volume_by_event.get(query['event_uid'], {'total': None, 'observed': 0, 'contracts': query['option_count']})
            query['volume_summary'] = volume
            page_start = a.start + ((count - 1) // a.batch_size) * a.batch_size
            position = ((count - 1) % a.batch_size) + 1
            index_rows.append({'title': query.get('title') or 'Untitled event', 'uid': query['event_uid'],
                               'volume': volume['total'], 'observed': volume['observed'], 'contracts': volume['contracts'],
                               'page_start': page_start, 'position': position})
        batch.append(item)
        if len(batch) == a.batch_size: write_batch()
    if batch: write_batch()
    description = ', '.join(a.category) if a.category else 'All categories'
    if a.volume_snapshots:
        index_path = Path(__file__).parent / 'volume_index.html'
        template = index_path.read_text(encoding='utf-8')
        for row in index_rows:
            first = row.pop('page_start')
            last = min(first + a.batch_size - 1, a.start + count - 1)
            row['link'] = f'events-{first:05d}-{last:05d}.html#query-{row.pop("position")}'
        # Escape characters significant in an HTML script element.
        payload = json.dumps(index_rows, ensure_ascii=True).replace('<', chr(92) + 'u003c').replace('>', chr(92) + 'u003e').replace('&', chr(92) + 'u0026')
        page = template.replace('__EVENTS__', payload).replace('__SNAPSHOT__', html.escape(snapshot_time)).replace('__DESCRIPTION__', html.escape(description))
    else:
        page = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Event review</title><h1>Event review</h1><p>' + html.escape(description) + ': ' + f'{count:,} Kalshi events.' + '</p><ul>' + ''.join(links) + '</ul></html>'
    (out / 'index.html').write_text(page, encoding='utf-8')
    print(f'Wrote {len(links)} reports covering {count:,} events to {out / "index.html"}')
if __name__ == '__main__': main()
