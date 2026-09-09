from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    depot = pathlib.Path('depot')
    pdfs = sorted(
        p for p in depot.rglob('*')
        if p.is_file() and p.suffix.lower() == '.pdf'
    )

    if not pdfs:
        print('Aucun PDF trouvé dans depot/.')
        return 0

    records = []
    for pdf in pdfs:
        stat = pdf.stat()
        record = {
            'path': pdf.as_posix(),
            'filename': pdf.name,
            'size_bytes': stat.st_size,
            'sha256': sha256_file(pdf),
        }
        records.append(record)
        print(f"PDF: {record['path']}")
        print(f"  size_bytes: {record['size_bytes']}")
        print(f"  sha256: {record['sha256']}")

    output_dir = pathlib.Path('out/intake')
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / 'pdf_intake_manifest.json'
    output_file.write_text(
        json.dumps({'pdfs': records}, indent=2, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )
    print(f'Manifeste écrit: {output_file}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
