"""Explicit acknowledgement after reviewing handbook against changed code."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import mechanics_docs as docs

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args()
    if args.write:
        docs.MANIFEST.write_text(json.dumps(docs.manifest(),ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    else:
        errors=docs.validate()
        for error in errors:print(error)
        raise SystemExit(bool(errors))
