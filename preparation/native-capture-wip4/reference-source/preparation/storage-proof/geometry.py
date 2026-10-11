"""Proof-local accounting. Imports no product and never runs on import."""
import importlib.util
import os
from pathlib import Path
import stat
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('proof_geometry_common',HERE.parent/'installation/common.py')
c=importlib.util.module_from_spec(s);s.loader.exec_module(c)
LEGACY=HERE/'retained'
RETAINED=HERE/'retained-v2'
ROOT_NAMES=('retained','retained-v2')
CASES=('maximum_ascii','maximum_utf8','transaction_rollback')
FILES=('producer-raw.json','jobs.sqlite3','jobs.sqlite3-wal','jobs.sqlite3-shm','jobs.sqlite3-journal')
CASE_CAP=128*2**20
PROOF_CAP=192*2**20
METADATA_CAP=128*2**10
HISTORY_CAP=40*2**10
FUTURE_METADATA_RESERVE=96*2**10
GROUP_CAPS={'wheels':38442241,'environment':92684832+208*2**20,
            'build':15*2**20,'temp':16*2**20,'bookkeeping':7*2**20}


def classify(entries):
    """Pure strict tree arithmetic, including directories and partial files.

    Entry fields come from lstat only. Missing allowed files are valid; they may
    not exist yet, or SQLite may have removed a sidecar. Listed vanished entries
    fail the reader closed rather than silently dropping their allocation.
    """
    c.require(type(entries) is list and len(entries)<=19,'proof entry limit')
    if not entries:return {'charged_bytes':0,'entries':0,'cases':{},'root_charge':0,'identities':[]}
    names=set();identities=set();cases={};root_charge=0;device=None
    for row in entries:
        rel=row['path'];kind=row['kind']
        c.require(type(rel) is str and rel not in names,'duplicate proof path')
        names.add(rel)
        c.require(kind in ('directory','regular'),'proof alias or special')
        c.require(all(type(row[k]) is int and row[k]>=0 for k in ('size','blocks','dev','ino','nlink')),'proof stat geometry')
        ident=(row['dev'],row['ino']);c.require(ident not in identities,'proof inode alias')
        identities.add(ident)
        if device is None:device=row['dev']
        c.require(row['dev']==device,'proof mounted device alias')
        charge=max(row['size'],row['blocks']*512)
        if rel=='':
            c.require(kind=='directory','proof root type');root_charge=charge;continue
        parts=rel.split('/')
        c.require(len(parts) in (1,2) and parts[0] in CASES,'unexpected proof descendant')
        case=cases.setdefault(parts[0],{'charged_bytes':0,'directory_charge':0,'files':{}})
        if len(parts)==1:
            c.require(kind=='directory','proof case type');case['directory_charge']=charge
        else:
            c.require(kind=='regular' and parts[1] in FILES and row['nlink']==1,'unexpected proof file or hard link')
            case['files'][parts[1]]={'bytes':row['size'],'charged_bytes':charge}
        case['charged_bytes']+=charge
    c.require('' in names,'proof root missing')
    for name,value in cases.items():
        c.require(name in names,'proof case directory missing')
        c.require(value['charged_bytes']<=CASE_CAP,'128 MiB case physical cap')
    total=root_charge+sum(x['charged_bytes'] for x in cases.values())
    c.require(total<=PROOF_CAP,'192 MiB retained proof physical cap')
    return {'charged_bytes':total,'entries':len(entries),'root_charge':root_charge,'cases':cases,
            'identities':[list(x) for x in sorted(identities)]}


def scan_tree(root,checker=lambda:None,allow_missing=False):
    """Tiny bounded lstat walk; shared by live proof and filesystem fixture."""
    root=Path(root)
    c.safe_path(root.parent,root.name)
    if allow_missing and not root.exists() and not root.is_symlink():return classify([])
    rows=[]
    def visit(path,rel):
        checker();st=path.lstat()
        kind='directory' if stat.S_ISDIR(st.st_mode) else 'regular' if stat.S_ISREG(st.st_mode) else 'forbidden'
        rows.append({'path':rel,'kind':kind,'size':st.st_size,'blocks':st.st_blocks,
                     'dev':st.st_dev,'ino':st.st_ino,'nlink':st.st_nlink})
        c.require(len(rows)<=19,'proof entry limit')
        c.require(kind!='forbidden','proof alias or special')
        if kind=='directory':
            c.require(rel=='' or rel in CASES,'unexpected nested proof directory')
            with os.scandir(path) as children:
                names=[]
                for child in children:
                    c.require(len(names)<5,'proof directory child limit')
                    names.append(child.name)
            names.sort()
            for name in names:visit(path/name,name if not rel else rel+'/'+name)
    visit(root,'')
    return classify(rows)


def combine(trees):
    """Both exact attempts share one unchanged physical and per-case ceiling."""
    c.require(set(trees)==set(ROOT_NAMES),'exact proof attempt roots')
    identities=[];cases={};total=0;entries=0;root_charge=0
    for attempt,geo in trees.items():
        total+=geo['charged_bytes'];entries+=geo['entries'];root_charge+=geo['root_charge']
        identities.extend(tuple(x) for x in geo['identities'])
        for name,case in geo['cases'].items():
            target=cases.setdefault(name,{'charged_bytes':0,'attempts':{}})
            target['charged_bytes']+=case['charged_bytes'];target['attempts'][attempt]=case
            c.require(target['charged_bytes']<=CASE_CAP,'128 MiB case physical cap across both attempts')
    c.require(len(set(identities))==len(identities),'cross-attempt inode alias')
    c.require(len({x[0] for x in identities})<=1,'cross-attempt device alias')
    c.require(total<=PROOF_CAP,'192 MiB retained proof physical cap across both attempts')
    return {'charged_bytes':total,'entries':entries,'root_charge':root_charge,'cases':cases,
            'attempt_charges':{name:geo['charged_bytes'] for name,geo in trees.items()}}


def retained(checker=c.check):
    for name in ROOT_NAMES:c.safe_path(c.PREP,'storage-proof/'+name)
    old=scan_tree(LEGACY,checker)
    c.require(set(old['cases'])=={'maximum_ascii'} and old['entries']==3,'first failed proof exact tree')
    files=old['cases']['maximum_ascii']['files']
    c.require(set(files)=={'producer-raw.json'} and files['producer-raw.json']['bytes']==8387276,
              'first failed proof raw shape changed')
    new=scan_tree(RETAINED,checker,allow_missing=True)
    return combine({'retained':old,'retained-v2':new})


def classify_groups(rows):
    groups={key:0 for key in GROUP_CAPS}
    for name,size in rows.items():
        parts=Path(name).parts
        c.require(not (len(parts)>=2 and parts[0]=='storage-proof' and parts[1].startswith('retained')),'proof file leaked into generic category')
        key=parts[0] if parts[0] in ('wheels','environment','build') else ('temp' if len(parts)>=3 and parts[0]=='runs' and parts[2] in ('tmp','home','cache') else 'bookkeeping')
        groups[key]+=size
    for key,cap in GROUP_CAPS.items():c.require(groups[key]<=cap,key+' logical ceiling')
    return groups


def category(checker=c.check):
    """Partition the full category exactly once; only proof gets a new group."""
    charges={}
    rows,base,count=c.scan(c.PREP,checker,exclude=('models','vendor','corpus','storage-proof'),charges=charges)
    meta,meta_charge,meta_count=c.scan(HERE,checker,exclude=ROOT_NAMES)
    history=sum(size for name,size in meta.items() if name.startswith('history/'))
    live_meta=sum(meta.values())-history
    c.require(live_meta<=METADATA_CAP,'128 KiB proof live source/metadata bookkeeping component')
    c.require(history<=HISTORY_CAP,'40 KiB retained preparation history component')
    for name,size in meta.items():rows['storage-proof/'+name]=size
    proof=retained(checker)
    excluded=c.verified_category_exclusions(rows,checker)
    for name,pin in excluded.items():c.require(charges[name]==pin['category_charge_removed'],'exact exclusion allocation changed')
    total=base+meta_charge+proof['charged_bytes']-sum(charges[x] for x in excluded)
    generic={name:size for name,size in rows.items() if name not in excluded}
    groups=classify_groups(generic)
    c.require(groups['bookkeeping']+FUTURE_METADATA_RESERVE<=GROUP_CAPS['bookkeeping'],
              '7 MiB bookkeeping cap with 96 KiB future proof records reserved')
    remaining=PROOF_CAP-proof['charged_bytes']
    c.require(0<=total and total+c.RESERVE+remaining<=c.CAP,'512 MiB category with all remaining proof capacity reserved')
    return {'charged_bytes':total,'cap_bytes':c.CAP,'reserve_bytes':c.RESERVE,
            'remaining_proof_reservation':remaining,'proof_cap_bytes':PROOF_CAP,
            'proof_physical_charge':proof['charged_bytes'],'proof':proof,
            'bookkeeping_future_reserve_bytes':FUTURE_METADATA_RESERVE,
            'proof_metadata_logical_bytes':sum(meta.values()),'live_proof_metadata_logical_bytes':live_meta,
            'proof_history_logical_bytes':history,'logical_groups':groups,
            'entries':count+meta_count+proof['entries'],
            'exact_file_exclusions':excluded,'excluded_roots':['models','vendor','corpus'],
            'proof_globally_charged':True,'global_cap_bytes':7*2**30,
            'global_enforcement':'unchanged acquisition_guard scratch scan and remaining aggregate free reservation'}


def enter_case(name,checker=c.check):
    c.require(name in CASES,'fixed proof case')
    before=category(checker)
    root=RETAINED/name
    c.require(not root.exists() and not root.is_symlink(),'case evidence already exists; never overwrite')
    if not RETAINED.exists():RETAINED.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    after=category(checker)
    return root,{'before':before,'after':after}
