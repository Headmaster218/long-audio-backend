#!/usr/bin/env python3
"""Source-only configuration and closed metadata validation; no project imports."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

HERE=Path(__file__).resolve().parent
MIB=2**20
GIB=2**30
PYTHON_PIN={'bytes':30894944,'sha256':'fa67443527ed9647f760d807e2a38f26340757123e643c4639cf273ed15d5ea7'}
CAPS={'acquisition':7*GIB,'runtime':512*MIB,'proof':512*MIB,'actual':544*MIB,'source':64*MIB,'evidence':64*MIB}
LIMITS={'global_disk_bytes':12*GIB,'category_bytes':CAPS,'ordinary_free_floor_bytes':11*2**29,
        'minimum_available_bytes':2952790016,'rss_bytes':256*MIB,'address_space_bytes':GIB,
        'cpu':3,'nice':19,'acquisition_absolute_seconds':10800,'cleanup_seconds':60,
        'read_timeout_seconds':30,'rate_bytes_per_second':4*MIB,'block_bytes':256*1024,
        'attempts_per_artifact':1,'automatic_retries':0,'receipt_reserve_bytes':MIB}
PHASES={'owned-success':('synthetic','test_guard.py',300),'owned-nonzero':('synthetic','test_guard.py',300),'guard-fixtures':('synthetic','test_guard.py',300),'pacing-fixtures':('synthetic','test_pacing.py',300),'download':('download','acquire.py',10800)}
FILE_CEILINGS={'README.md':16384,'bootstrap.py':32768,'acquisition_guard.py':40960,
 'acquire.py':65536,'guard_driver.py':32768,'test_guard.py':40960,'test_pacing.py':32768,
 'config.schema.json':16384,'assets.lock.json':98304,'PACKET_MANIFEST.json':16384}

class Refused(RuntimeError): pass
class PromotionRace(Refused): pass

def require(ok,reason):
    if not ok: raise Refused(reason)

def closed(value,keys,reason):
    require(type(value) is dict and set(value)==set(keys),reason)

def check_deadline(end):
    require(time.monotonic()<end,'deadline')

def path_value(raw,exists=True):
    require(type(raw) is str and 0<len(raw)<=4096 and '\x00' not in raw,'path_shape')
    p=Path(raw)
    require(p.is_absolute() and str(p)==raw and '..' not in p.parts and p.resolve(strict=False)==p,'canonical_path_required')
    for q in (p,*p.parents): require(not q.is_symlink(),'symlink_path')
    if exists: require(p.exists(),'required_path_missing')
    return p

def pin_file(path,maximum=32*MIB,end=None):
    p=Path(path);before=p.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_size<=maximum,'regular_file_cap')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK); h=hashlib.sha256();n=0
    key=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
    try:
        require(key(before)==key(os.fstat(fd)),'file_open_race')
        while True:
            if end is not None:check_deadline(end)
            block=os.read(fd,min(65536,maximum+1-n))
            if not block:break
            n+=len(block);require(n<=maximum,'file_growth_cap');h.update(block)
        require(key(before)==key(os.fstat(fd))==key(p.lstat()),'file_changed')
    finally:os.close(fd)
    return {'bytes':n,'sha256':h.hexdigest()}

def json_file(path,maximum=MIB,sha=None):
    p=Path(path);pin=pin_file(p,maximum)
    if sha is not None:require(pin['sha256']==sha,'json_pin_mismatch')
    raw=p.read_bytes();require(len(raw)==pin['bytes'] and hashlib.sha256(raw).hexdigest()==pin['sha256'],'json_changed')
    def pairs(items):
        result={}
        for k,v in items:
            require(k not in result,'duplicate_json_key');result[k]=v
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda value:(_ for _ in ()).throw(Refused('nonfinite_json')))

def write_new(path,value,maximum=MIB):
    p=Path(path);raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    require(len(raw)<=maximum,'output_metadata_cap')
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    directory=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(directory)
    finally:os.close(directory)
    return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

def module(name,filename):
    p=HERE/filename;spec=importlib.util.spec_from_file_location(name,p)
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj

def verify_packet():
    p=HERE/'PACKET_MANIFEST.json'; value=json_file(p,FILE_CEILINGS[p.name])
    closed(value,('schema','status','files','upstream','preserved_ast_sha256','tests_executed','future_files'),'packet_schema')
    require(value['schema']=='portable-recovery-packet-v1' and value['tests_executed'] is False,'packet_identity')
    require(set(value['files'])==set(FILE_CEILINGS)-{'PACKET_MANIFEST.json'},'packet_membership')
    actual={p.name for p in HERE.iterdir()}
    require(actual==set(FILE_CEILINGS),'unexpected_packet_member')
    for name,row in value['files'].items():
        closed(row,('bytes','sha256'),'packet_file_pin_shape')
        require(pin_file(HERE/name,FILE_CEILINGS[name])==row,'packet_file_pin')
    return value

def assets():
    value=json_file(HERE/'assets.lock.json',FILE_CEILINGS['assets.lock.json'])
    closed(value,('schema','status','source','artifacts','total_download_bytes','retained_download_plus_future_extraction_ceiling','attempts_per_artifact','automatic_retries','range_requests','extraction_authorized_by_this_manifest','local_source_copies','future_extraction_ceiling_bytes','future_input'),'assets_schema')
    require(value['schema']=='portable-recovery-assets-v1' and value['attempts_per_artifact']==1 and value['automatic_retries']==0 and value['range_requests'] is False and value['extraction_authorized_by_this_manifest'] is False,'assets_policy')
    require(type(value['artifacts']) is list and len(value['artifacts'])==38,'asset_count')
    ids=set();dest=set();total=0
    for a in value['artifacts']:
        closed(a,('id','url','bytes','sha256','destination','group'),'asset_schema')
        require(type(a['id']) is str and re.fullmatch('[a-zA-Z0-9._-]{1,96}',a['id']) and a['id'] not in ids,'asset_id')
        require(type(a['bytes']) is int and 0<a['bytes']<4*GIB,'asset_size')
        require(type(a['sha256']) is str and re.fullmatch('[0-9a-f]{64}',a['sha256']),'asset_digest')
        require(type(a['destination']) is str and a['destination'] not in dest and not a['destination'].startswith('/') and all(x not in ('','.','..') for x in a['destination'].split('/')),'asset_destination')
        require(a['group'] in ('models','wheels','corpus'),'asset_group')
        ids.add(a['id']);dest.add(a['destination']);total+=a['bytes']
    require(total==value['total_download_bytes']==5150756491,'asset_total')
    require(value['retained_download_plus_future_extraction_ceiling']==6504423186<CAPS['acquisition'],'prospective_asset_reservation')
    source=value['source'];closed(source,('commit','tree','file_count','bytes','files'),'product_pin_schema')
    require(source['commit']=='1fc066910ca78b2e57d7a28f79b91cafc06f5dd5' and source['tree']=='6ca0c2b0e045c5ea8fdb9449bbaf6172d251b527' and source['file_count']==351 and source['bytes']==4651663 and len(source['files'])==351,'product_identity')
    return value

def source_inventory(root,end=None):
    root=path_value(str(root));result={};total=0
    for base,dirs,files in os.walk(root,followlinks=False):
        for n in dirs:require(not (Path(base)/n).is_symlink(),'source_directory_symlink')
        for n in sorted(files):
            if end is not None:check_deadline(end)
            p=Path(base)/n;row=pin_file(p,32*MIB,end);total+=row['bytes']
            require(len(result)<4096 and total<=32*MIB,'source_inventory_cap')
            result[str(p.relative_to(root))]=row['sha256']
    return result,total

def validate_config(c):
    closed(c,('schema','roots','host','limits','assets_sha256','packet_sha256','created_boot_id','created_pid_namespace'),'config_schema')
    require(c['schema']=='portable-recovery-config-v1' and c['limits']==LIMITS,'config_limits')
    for k,v in c['limits'].items():
        if k=='category_bytes':require(all(type(x) is int for x in v.values()),'category_integer_types')
        else:require(type(v) is int,'limit_integer_types')
    closed(c['roots'],('workspace','source_recovery','product','packet','work','runtime','runtime_container','proof_source'),'roots_schema')
    roots={k:path_value(v,exists=k not in ('work','runtime','runtime_container','proof_source')) for k,v in c['roots'].items()}
    require(roots['packet']==HERE and roots['packet'].parent==roots['workspace'],'packet_location')
    require(roots['product'].is_relative_to(roots['source_recovery']),'product_root_scope')
    for k in ('source_recovery','packet','work','runtime_container','proof_source'):
        require(roots[k]!=roots['workspace'] and roots[k].is_relative_to(roots['workspace']),'charged_root_scope')
    require(roots['runtime_container'].parent==roots['workspace'] and roots['runtime'].is_relative_to(roots['runtime_container']),'runtime_container_scope')
    names=('source_recovery','packet','work','runtime_container','proof_source')
    for i,a in enumerate(names):
        for b in names[i+1:]:require(not roots[a].is_relative_to(roots[b]) and not roots[b].is_relative_to(roots[a]),'charged_roots_overlap')
    closed(c['host'],('python','python_bytes','python_sha256','filesystem_device','python_version'),'host_schema')
    require(path_value(c['host']['python'])==Path(sys.executable).resolve(),'host_python_origin')
    require({'bytes':c['host']['python_bytes'],'sha256':c['host']['python_sha256']}==PYTHON_PIN,'host_python_identity')
    require(type(c['host']['filesystem_device']) is int and roots['workspace'].stat().st_dev==c['host']['filesystem_device'],'filesystem_device_changed')
    require(c['host']['python_version']=='3.12.14' and sys.version_info[:3]==(3,12,14),'python_version')
    for k in ('assets_sha256','packet_sha256'):require(type(c[k]) is str and re.fullmatch('[0-9a-f]{64}',c[k]),'config_hash_shape')
    require(c['created_boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip() and c['created_pid_namespace']==os.readlink('/proc/self/ns/pid'),'fresh_instance_changed')
    return c

def load_config(path,sha):
    require(type(sha) is str and re.fullmatch('[0-9a-f]{64}',sha),'config_sha_required')
    c=validate_config(json_file(path,65536,sha))
    require(Path(path)==Path(c['roots']['work'])/'evidence/config.json','config_path')
    require(pin_file(HERE/'assets.lock.json',98304)['sha256']==c['assets_sha256'] and pin_file(HERE/'PACKET_MANIFEST.json',16384)['sha256']==c['packet_sha256'],'config_packet_pins')
    require(pin_file(c['host']['python'])==PYTHON_PIN,'host_python_bytes')
    return c

def _resource_usage_once(c,end):
    roots=c['roots'];dev=c['host']['filesystem_device'];usage={k:0 for k in CAPS};count=0
    work=Path(roots['work']);source=(Path(roots['source_recovery']),Path(roots['packet']),Path(roots['proof_source']))
    def category(p):
        if any(p==r or p.is_relative_to(r) for r in source):return 'source'
        r=Path(roots['runtime_container'])
        if p==r or p.is_relative_to(r):return 'runtime'
        for name,sub in (('acquisition','assets'),('proof','proof'),('actual','actual')):
            if p==work/sub or p.is_relative_to(work/sub):return name
        return 'evidence'
    def visit(p,root):
        nonlocal count
        check_deadline(end);count+=1;require(count<=32768,'charged_entry_cap')
        try:st=p.lstat()
        except FileNotFoundError:
            if p.parent==work/'assets/tmp' and re.fullmatch(r'[0-9a-f]{64}\.part',p.name):raise PromotionRace('known_part_promotion_rescan')
            if p.is_relative_to(work/'evidence/runs') and ('tmp' in p.parts or p.name.endswith('.tmp')):return
            raise
        require(st.st_dev==dev,'charged_filesystem_changed')
        require(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode),'charged_special_file')
        if stat.S_ISLNK(st.st_mode):
            literal=os.readlink(p);target=p.resolve(strict=True)
            require(not Path(literal).is_absolute() and len(literal)<=4096 and target.is_relative_to(root) and target.is_file(),'charged_alias_escape')
        usage[category(p)]+=max(st.st_size,st.st_blocks*512)
        if stat.S_ISDIR(st.st_mode):
            with os.scandir(p) as entries:
                for entry in entries:visit(Path(entry.path),root)
    for key in ('source_recovery','packet','work','runtime_container','proof_source'):
        root=Path(roots[key])
        if not root.exists():require(key in ('runtime_container','proof_source','work'),'charged_root_missing');continue
        require(root.is_dir() and not root.is_symlink(),'charged_root_type');visit(root,root)
    for name,total in usage.items():require(total<=CAPS[name],'category_cap_'+name)
    require(sum(usage.values())+MIB<=12*GIB,'global_disk_cap')
    return usage

def resource_usage(c,end):
    # A listed hashed part may disappear only through its approved atomic
    # promotion. Restart the complete census, never omit its unobserved bytes.
    for attempt in range(3):
        check_deadline(end)
        try:return _resource_usage_once(c,end)
        except PromotionRace:
            if attempt==2:raise Refused('promotion_rescan_exhausted')
    raise Refused('unreachable_census')


def admission(c,end,prospective=False):
    usage=resource_usage(c,end);total=sum(usage.values())
    mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    available=int(mem['MemAvailable'].split()[0])*1024
    fs=os.statvfs(c['roots']['workspace']);free=fs.f_bavail*fs.f_frsize
    require(available>=LIMITS['minimum_available_bytes'],'host_available_floor')
    required=LIMITS['ordinary_free_floor_bytes']+LIMITS['global_disk_bytes']-total
    require(free>=required,'free_floor_with_remaining_global_reservation')
    require(3 in os.sched_getaffinity(0),'cpu3_unavailable')
    if prospective:require(usage['acquisition']+6504423186+MIB<=CAPS['acquisition'],'prospective_acquisition_cap')
    return {'charged_by_category':usage,'charged_bytes':total,'free_bytes':free,'available_bytes':available,'required_free_bytes':required}

def process_identity():
    raw=Path('/proc/self/stat').read_text();items=raw[raw.rindex(')')+2:].split()
    return {'pid':os.getpid(),'start_ticks':int(items[19]),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'pid_namespace':os.readlink('/proc/self/ns/pid')}

def launch_binding(start,phase):
    require(phase in PHASES and type(start) in (int,float) and math.isfinite(start),'launch_shape')
    return {'start_monotonic':start,'absolute_end_monotonic':start+PHASES[phase][2],'identity':process_identity()}

def validate_launch(launch,phase):
    closed(launch,('start_monotonic','absolute_end_monotonic','identity'),'launch_schema')
    start=launch['start_monotonic'];end=launch['absolute_end_monotonic']
    require(type(start) in (int,float) and type(end) in (int,float) and math.isfinite(start) and math.isfinite(end),'launch_clock_shape')
    require(end==start+PHASES[phase][2] and start<=time.monotonic()<end-60,'original_launch_deadline')
    require(launch['identity']==process_identity(),'same_pid_exec_identity')
    return launch

def make_spec(c,config_path,config_sha,phase,launch=None):
    require(phase in PHASES,'phase_not_allowed');stage,script,seconds=PHASES[phase]
    launch=launch_binding(time.monotonic(),phase) if launch is None else validate_launch(launch,phase)
    a=assets();p=verify_packet();external={str(HERE/name):row['sha256'] for name,row in p['files'].items()}
    external[str(HERE/'PACKET_MANIFEST.json')]=c['packet_sha256'];external[str(config_path)]=config_sha
    return {'schema':3,'stage':stage,'absolute_seconds':seconds,'work_seconds':seconds-60,
      'address_space_cap_bytes':GIB,'rss_cap_bytes':256*MIB,'scratch_cap_bytes':12*GIB,
      'guard_sha256':p['files']['acquisition_guard.py']['sha256'],'source_root':c['roots']['product'],
      'source_files':a['source']['files'],'cwd':str(Path(c['roots']['work'])/'evidence'),
      'external_files':external,'runtime_files':{c['host']['python']:PYTHON_PIN['sha256']},
      'config_path':str(config_path),'config_sha256':config_sha,'phase':phase,'launch':launch,
      'argv':[c['host']['python'],'-I','-B','-S',str(HERE/script),'--config',str(config_path),'--config-sha256',config_sha]+(['--probe-mode',phase.removeprefix('owned-')] if phase.startswith('owned-') else [])}

def configure(args):
    verify_packet();a=assets();end=time.monotonic()+120
    roots={'workspace':str(HERE.parent),'packet':str(HERE),'source_recovery':args.source_recovery,
      'product':args.product,'work':args.work,'runtime':args.runtime,
      'runtime_container':str(HERE.parent/Path(args.runtime).relative_to(HERE.parent).parts[0]),
      'proof_source':args.proof_source or str(HERE.parent/'portable-cell-store-proof')}
    c={'schema':'portable-recovery-config-v1','roots':roots,'host':{'python':str(Path(sys.executable).resolve()),
      'python_bytes':PYTHON_PIN['bytes'],'python_sha256':PYTHON_PIN['sha256'],
      'filesystem_device':HERE.stat().st_dev,'python_version':'3.12.14'},'limits':LIMITS,
      'assets_sha256':pin_file(HERE/'assets.lock.json',98304)['sha256'],
      'packet_sha256':pin_file(HERE/'PACKET_MANIFEST.json',16384)['sha256'],
      'created_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
      'created_pid_namespace':os.readlink('/proc/self/ns/pid')}
    validate_config(c);require(pin_file(c['host']['python'])==PYTHON_PIN,'host_python_bytes')
    actual,total=source_inventory(args.product,end)
    require(actual==a['source']['files'] and total==a['source']['bytes'],'recovered_product_bytes')
    work=Path(args.work);require(not work.exists() and not Path(roots['runtime_container']).exists(),'fresh_work_runtime_container_required')
    before=admission(c,end,prospective=True)
    work.mkdir(mode=0o700);(work/'evidence').mkdir(mode=0o700);(work/'evidence/runs').mkdir(mode=0o700)
    path=work/'evidence/config.json';pin=write_new(path,c,65536)
    print(json.dumps({'status':'CONFIGURED_SOURCE_ONLY','config':str(path),**pin,'admission':before,'execution_authorized':False},sort_keys=True))

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('configure')
    for name in ('product','source-recovery','work','runtime'):p.add_argument('--'+name,required=True)
    p.add_argument('--proof-source')
    args=parser.parse_args();configure(args)

if __name__=='__main__':
    try:main()
    except Exception as error:print(json.dumps({'status':'REFUSED','error_type':type(error).__name__}),file=sys.stderr);raise SystemExit(2)
