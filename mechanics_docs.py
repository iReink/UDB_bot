"""Versioned public handbook compiler. No bot imports or private configuration."""
from pathlib import Path
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parent
DIRECTORY=ROOT/'docs/mechanics'
MANIFEST=DIRECTORY/'manifest.json'


def hash_file(path):
    # Git checkouts on Windows/Linux may have different newline conventions.
    # Hash source meaning consistently; BOM/newlines do not change the rules.
    text=path.read_text(encoding='utf-8-sig')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def documents(root=ROOT):
    directory=Path(root)/'docs/mechanics'
    items=[];ids=set()
    for path in sorted(directory.glob('*.md')):
        text=path.read_text(encoding='utf-8-sig')
        header,body=text.split('\n---\n',1)
        meta=json.loads(header)
        if meta['id'] in ids: raise ValueError('Duplicate document ID: '+meta['id'])
        ids.add(meta['id'])
        sections=[];seen=set()
        for match in re.finditer(r'^## (.+?) \{#([\w-]+)\}\n(.*?)(?=^## |\Z)',body,re.M|re.S):
            title,ident,content=match.groups()
            if ident in seen: raise ValueError('Duplicate section ID: '+ident)
            seen.add(ident);sections.append(dict(id=ident,title=title,text=content.strip()))
        if not sections: raise ValueError('No sections: '+str(path))
        dependencies={}
        for source in meta['sources']:
            candidate=Path(root)/source
            if not candidate.is_file() or Path(source).is_absolute() or '..' in Path(source).parts or candidate.suffix not in ('.py','.md','.js','.css','.html'):
                raise ValueError('Invalid source: '+source)
            dependencies[source]=hash_file(candidate)
        items.append(dict(meta,path=path.relative_to(root).as_posix(),hash=hash_file(path),dependencies=dependencies,sections=sections))
    return items


def manifest(root=ROOT):
    return {'version':1,'documents':[{k:d[k] for k in ('id','title','keywords','path','hash','dependencies')} for d in documents(root)]}


def validate(root=ROOT):
    path=Path(root)/'docs/mechanics/manifest.json'
    if not path.exists(): return ['Mechanics manifest missing']
    try:
        expected=manifest(root);actual=json.loads(path.read_text(encoding='utf-8-sig'))
        return [] if actual==expected else ['Mechanics documentation/source hashes changed: review handbook and run scripts/mechanics_manifest.py --write']
    except (ValueError,KeyError,OSError) as exc: return [str(exc)]


def catalog():
    return '\n'.join(d['title']+': '+', '.join(d['keywords']) for d in documents())
