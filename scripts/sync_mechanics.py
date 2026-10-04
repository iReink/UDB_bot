"""Build/apply a public handbook delta; secrets and unrelated paths excluded."""
import argparse,json,sys,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mechanics_docs as docs


def bundle(remote,target):
    current=docs.manifest();previous={d['path']:d['hash'] for d in remote.get('documents',[])}
    paths={d['path'] for d in current['documents']}
    changed=[d['path'] for d in current['documents'] if previous.get(d['path'])!=d['hash']]
    deleted=sorted(set(previous)-paths)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in changed:archive.write(docs.ROOT/path,path)
        archive.writestr('docs/mechanics/manifest.json',json.dumps(current,ensure_ascii=False))
        archive.writestr('deleted.json',json.dumps(deleted))
    return dict(changed=changed,deleted=deleted)


def public_path(name):
    path=Path(name)
    if path.is_absolute() or path.parts[:2]!=('docs','mechanics') or len(path.parts)!=3 or path.suffix not in ('.md','.json'):
        raise ValueError('Unexpected handbook path')
    return docs.ROOT/path


def apply_bundle(path):
    with zipfile.ZipFile(path) as archive:
        # Validate the entire bundle before the first mutation.
        names=[n for n in archive.namelist() if n!='deleted.json']
        for name in names:public_path(name)
        deleted=json.loads(archive.read('deleted.json'))
        for name in deleted:public_path(name)
        originals={name:public_path(name).read_bytes() if public_path(name).exists() else None for name in set(names+deleted)}
        try:
            for name in names:
                target=public_path(name);target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(name))
            for name in deleted:public_path(name).unlink(missing_ok=True)
            errors=docs.validate()
            if errors:raise ValueError('; '.join(errors))
            import mechanics
            mechanics.sync()
        except Exception:
            for name,content in originals.items():
                target=public_path(name)
                if content is None:target.unlink(missing_ok=True)
                else:target.write_bytes(content)
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote-manifest',type=Path)
    parser.add_argument('--bundle',type=Path)
    parser.add_argument('--apply',type=Path)
    args=parser.parse_args()
    if args.apply:apply_bundle(args.apply)
    elif args.bundle:
        previous=json.loads(args.remote_manifest.read_text(encoding='utf-8-sig')) if args.remote_manifest else {}
        print(json.dumps(bundle(previous,args.bundle),ensure_ascii=False))
    else:parser.error('Specify --bundle or --apply')
