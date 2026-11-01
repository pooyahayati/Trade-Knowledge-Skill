"""Verify the exact portable distribution; requires only Python's standard library."""
import ast,hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
m=read(ROOT/'PUBLICATION_MANIFEST.json')
actual={p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.relative_to(ROOT).parts and '__pycache__' not in p.parts and p.suffix!='.pyc'}
assert actual==set(m['public_files'])|{'PUBLICATION_MANIFEST.json'},'unmanifested_or_missing_public_files'
for rel,record in m['public_files'].items():
    p=ROOT/rel
    assert p.resolve().is_relative_to(ROOT.resolve()) and p.is_file(),rel
    assert sha(p)==record['sha256'] and p.stat().st_size==record['bytes'],rel
private=re.compile(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]|/(?:Users|home|mnt)/')
links=0
for rel in m['public_files']:
    p=ROOT/rel
    if p.suffix in {'.json','.md','.py','.yaml','.yml','.pine','.mq4','.mq5','.mqh'}:
        text=p.read_text(encoding='utf-8')
        assert not private.search(text),rel
    if p.suffix=='.py':ast.parse(text,filename=rel)
    if p.suffix=='.md':
        for href in re.findall(r'\]\(([^)]+)\)',text):
            target=href.strip('<>').split('#')[0]
            if not target or '://' in target:continue
            q=(p.parent/target).resolve()
            assert q.is_relative_to(ROOT) and q.is_file(),(rel,target)
            links+=1
skill=ROOT/'skills/trade-chart-scenarios'
for row in read(skill/'assets/conformance/index.json')['fixtures']:
    assert sha(skill/'assets/conformance'/row['file'])==row['sha256']
for row in read(skill/'references/provenance.json')['snapshots']:
    assert sha(skill/'references'/row['file'])==row['sha256'],row['file']
for row in read(skill/'references/DATA-PROVENANCE.json')['files']:
    assert sha(ROOT/row['file'])==row['sha256'],row['file']
assert '0.17.0' in (skill/'SKILL.md').read_text(encoding='utf-8')
print(json.dumps({'status':'passed','files':len(m['public_files']),'portable_links':links,'variant':'public','original_user_documents_included':False}))
