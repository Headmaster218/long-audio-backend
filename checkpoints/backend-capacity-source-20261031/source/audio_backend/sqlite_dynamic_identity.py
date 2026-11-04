"""Finite Linux ELF64/x86-64 SQLite binding identity, never runtime qualification.

Verify actual resolved SQLite relocation slots, not merely a library filename or
handle lookup. Unsupported ELF/mapping forms fail closed. This does not defend
against hostile concurrent same-process code or private executable-page mutation.
"""
from contextlib import contextmanager, ExitStack
import ctypes
import _ctypes
from dataclasses import dataclass
import hashlib
from importlib.machinery import ExtensionFileLoader
import os
from pathlib import Path
import struct
import sys

from .native_owned_cells import Unavailable, IO_BYTES

BINDING = 'observed-dynamic-elf64-x86_64-sqlite-relocations-v1'
MAX_MAPS = 256 * 1024
MAX_EXTENSION = 4 * 1024**2
MAX_LIBRARY = 16 * 1024**2
_PROCESS = ctypes.CDLL(None)
_DLADDR = _PROCESS.dladdr
_DLADDR.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_DLADDR.restype = ctypes.c_int


@contextmanager
def loaded_object(path):
    handle = ctypes.CDLL(str(path), mode=os.RTLD_NOLOAD | os.RTLD_LAZY)
    try:yield handle
    finally:_ctypes.dlclose(handle._handle)


def refuse(code):raise Unavailable(code)


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class Mapping:
    start: int
    end: int
    permissions: str
    offset: int
    device: tuple
    inode: int
    path: str


def parse_maps(raw):
    if len(raw) > MAX_MAPS:refuse('runtime_mapping_capacity')
    lines = raw.splitlines()
    if len(lines) > 4096:refuse('runtime_mapping_capacity')
    result = []
    try:
        for line in lines:
            fields = line.split(None, 5)
            if len(fields) < 5:refuse('runtime_mapping_malformed')
            start, end = (int(v, 16) for v in fields[0].split(b'-'))
            permissions = fields[1].decode('ascii')
            device = tuple(int(v, 16) for v in fields[3].split(b':'))
            if (start >= end or len(device) != 2 or len(permissions) != 4
                    or permissions[0] not in 'r-' or permissions[1] not in 'w-'
                    or permissions[2] not in 'x-' or permissions[3] not in 'ps'):
                refuse('runtime_mapping_malformed')
            result.append(Mapping(start,end,permissions,int(fields[2],16),device,
                int(fields[4]),os.fsdecode(fields[5]) if len(fields)==6 else ''))
    except (ValueError, UnicodeError):refuse('runtime_mapping_malformed')
    if any(a.end > b.start for a,b in zip(result,result[1:])):refuse('runtime_mapping_overlap')
    return result


def read_maps(budget):
    raw = bytearray()
    with open('/proc/self/maps','rb',buffering=0) as stream:
        while True:
            budget.read(IO_BYTES);block=stream.read(IO_BYTES);budget.check()
            if not block:break
            raw.extend(block)
            if len(raw)>MAX_MAPS:refuse('runtime_mapping_capacity')
    return parse_maps(bytes(raw))


def file_group(mappings, path, info):
    key = ((os.major(info.st_dev),os.minor(info.st_dev)),info.st_ino)
    selected = [m for m in mappings if (m.device,m.inode)==key]
    if not selected or not any('x' in m.permissions for m in selected):
        refuse('runtime_object_mapping_missing')
    for m in selected:
        if (not m.path.startswith('/') or m.path.endswith(' (deleted)') or '\\' in m.path
                or Path(m.path).resolve(strict=True)!=path):
            refuse('runtime_object_mapping_alias')
        if signature(os.stat(m.path)) != signature(info):refuse('runtime_object_mapping_changed')
    return tuple(selected)


def at_address(mappings, address, size=1, permission='x'):
    found = [m for m in mappings if m.start<=address and address+size<=m.end and permission in m.permissions]
    if len(found)!=1:refuse('runtime_symbol_mapping_ambiguous')
    return found[0]


def elf_sqlite_relocations(raw, with_loads=False, check=None):
    """Read loader-consumed PT_DYNAMIC RELA tables; section headers are not trusted."""
    if len(raw)>MAX_EXTENSION or len(raw)<64:refuse('runtime_extension_elf_bound')
    def unpack(fmt, offset):
        size=struct.calcsize(fmt)
        if offset<0 or offset+size>len(raw):refuse('runtime_extension_elf_extent')
        return struct.unpack_from(fmt,raw,offset)
    header=unpack('<16sHHIQQQIHHHHHH',0)
    ident,kind,machine,version,_,phoff,_,_,ehsize,phsize,phnum,*_=header
    if (ident[:7]!=b'\x7fELF\x02\x01\x01' or kind!=3 or machine!=62 or version!=1
            or ehsize!=64 or phsize!=56 or not 1<=phnum<=64):
        refuse('runtime_extension_elf_form')
    segments=[unpack('<IIQQQQQQ',phoff+i*phsize) for i in range(phnum)]
    loads=[s for s in segments if s[0]==1];dynamic=[s for s in segments if s[0]==2]
    if not loads or len(dynamic)!=1:refuse('runtime_extension_elf_segments')
    for _,_,offset,virtual,_,filesize,memsize,align in loads:
        if (filesize>memsize or offset+filesize>len(raw) or virtual+memsize>=2**64
                or not 1<=align<=2**21 or align&(align-1) or offset%align!=virtual%align):
            refuse('runtime_extension_elf_extent')
    def location(address,size):
        found=[s[2]+address-s[3] for s in loads if s[3]<=address and address+size<=s[3]+s[5]]
        if len(found)!=1:refuse('runtime_extension_virtual_extent')
        return found[0]
    dyn=dynamic[0]
    if dyn[5]%16 or not 16<=dyn[5]<=16384:refuse('runtime_extension_dynamic_bound')
    if location(dyn[3],dyn[5])!=dyn[2]:refuse('runtime_extension_dynamic_extent')
    tags={};ended=False
    for offset in range(dyn[2],dyn[2]+dyn[5],16):
        tag,value=unpack('<qQ',offset)
        if tag==0:ended=True;break
        if tag==1:continue  # NEEDED can repeat; actual relocation origin is authoritative.
        if tag in tags:refuse('runtime_extension_dynamic_duplicate')
        tags[tag]=value
    if not ended or 17 in tags or 18 in tags:refuse('runtime_extension_relocation_form')
    if tags.get(11)!=24 or tags.get(9)!=24 or tags.get(20)!=7:
        refuse('runtime_extension_relocation_form')
    if not all(k in tags for k in (5,6,10,7,8,23,2)) or not 1<=tags[10]<=256*1024:
        refuse('runtime_extension_dynamic_missing')
    strings=location(tags[5],tags[10]);relocations=[];seen=set();all_slots=set()
    for address,size in ((tags[7],tags[8]),(tags[23],tags[2])):
        if size%24 or size>8192*24:refuse('runtime_extension_relocation_bound')
        start=location(address,size)
        for offset in range(start,start+size,24):
            if check is not None:check()
            slot,info,addend=unpack('<QQq',offset);index=info>>32;kind=info&0xffffffff
            if not index:continue
            if index>=4096:refuse('runtime_extension_symbol_bound')
            symbol=unpack('<IBBHQQ',location(tags[6]+index*24,24))
            name_offset,symbol_info,_,section,_,_=symbol
            if 0x6ffffff0 in tags:
                symbol_version=unpack('<H',location(tags[0x6ffffff0]+index*2,2))[0]
            else:symbol_version=1
            if name_offset>=tags[10]:refuse('runtime_extension_symbol_name')
            end=raw.find(b'\0',strings+name_offset,min(strings+tags[10],strings+name_offset+129))
            if end<0:refuse('runtime_extension_symbol_name')
            name=bytes(raw[strings+name_offset:end])
            if not name.startswith(b'sqlite3_'):continue
            if (not 1<=len(name)<=128 or any(c not in b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' for c in name)
                    or section!=0 or symbol_info&15!=2 or symbol_info>>4!=1 or symbol_version!=1 or kind not in (6,7) or addend!=0
                    or slot in all_slots):
                refuse('runtime_extension_sqlite_relocation')
            # Slots must be within a load's memory extent, and later a live readable mapping.
            if not any(s[3]<=slot and slot+8<=s[3]+s[6] for s in loads):
                refuse('runtime_extension_slot_extent')
            relocations.append((slot,name.decode('ascii')));seen.add(name);all_slots.add(slot)
            if len(relocations)>512:refuse('runtime_extension_sqlite_capacity')
    if not {b'sqlite3_open_v2',b'sqlite3_prepare_v2',b'sqlite3_libversion'}.issubset(seen):
        refuse('runtime_extension_sqlite_symbols_missing')
    return (relocations,loads) if with_loads else relocations


class DlInfo(ctypes.Structure):
    _fields_=[('name',ctypes.c_char_p),('base',ctypes.c_void_p),
              ('symbol',ctypes.c_char_p),('address',ctypes.c_void_p)]


def dynamic_fingerprints(module,budget,runtime_hash):
    spec=module.__spec__
    if (sys.platform!='linux' or ctypes.sizeof(ctypes.c_void_p)!=8 or spec is None
            or spec.name!='_sqlite3' or not isinstance(spec.loader,ExtensionFileLoader)
            or type(spec.origin) is not str or spec.origin!=getattr(module,'__file__',None)):
        refuse('unqualified_sqlite_binding_form')
    try:
        return _dynamic_fingerprints(module,budget,runtime_hash)
    except Unavailable:raise
    except (OSError,ValueError,AttributeError,struct.error,OverflowError):
        refuse('runtime_dynamic_identity_unavailable')


def _dynamic_fingerprints(module,budget,runtime_hash):
    extension,ext_info,ext_hash=runtime_hash(module.__file__,budget,MAX_EXTENSION)
    # Read the exact hashed ELF through a second bounded descriptor and revalidate identity.
    fd=os.open(extension,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        if signature(os.fstat(fd))!=signature(ext_info):refuse('runtime_extension_changed')
        raw=bytearray()
        while len(raw)<ext_info.st_size:
            count=min(IO_BYTES,ext_info.st_size-len(raw));budget.read(count)
            block=os.read(fd,count);budget.check()
            if not block:refuse('runtime_extension_truncated')
            raw.extend(block)
        if signature(os.fstat(fd))!=signature(ext_info) or hashlib.sha256(raw).hexdigest()!=ext_hash:
            refuse('runtime_extension_changed')
    finally:os.close(fd)
    relocations,loads=elf_sqlite_relocations(raw, with_loads=True, check=budget.check)
    dladdr=_DLADDR
    with ExitStack() as handles:
        handle=handles.enter_context(loaded_object(extension))
        def origin(pointer,name):
            info=DlInfo()
            if (not pointer or not dladdr(pointer,ctypes.byref(info)) or not info.name or not info.base
                    or not info.symbol or info.symbol!=name.encode('ascii') or info.address!=pointer):
                refuse('runtime_symbol_origin_mismatch')
            if len(info.name)>4096 or b'\\' in info.name or info.name.endswith(b' (deleted)'):
                refuse('runtime_symbol_origin_path')
            return Path(os.fsdecode(info.name)).resolve(strict=True),info.base
        init_pointer=ctypes.cast(getattr(handle,'PyInit__sqlite3'),ctypes.c_void_p).value
        init_path,base=origin(init_pointer,'PyInit__sqlite3')
        if init_path!=extension:refuse('runtime_extension_origin_mismatch')
        mappings=read_maps(budget);ext_group=file_group(mappings,extension,ext_info)
        if at_address(mappings,init_pointer) not in ext_group:refuse('runtime_extension_origin_mapping')
        for segment in loads:
            cursor=base+segment[3];end=cursor+segment[5]
            while cursor<end:
                mapping=at_address(mappings,cursor,1,'r')
                if (mapping not in ext_group or mapping.offset+cursor-mapping.start
                        != segment[2]+cursor-(base+segment[3])):
                    refuse('runtime_extension_load_bias')
                cursor=min(end,mapping.end)
        library=None;lib_group=None;lib_info=None;slot_values=[]
        memory=os.open('/proc/self/mem',os.O_RDONLY|getattr(os,'O_CLOEXEC',0))
        try:
            for slot,name in relocations:
                address=base+slot
                if at_address(mappings,address,8,'r') not in ext_group:refuse('runtime_extension_slot_mapping')
                budget.read(8);pointer_raw=os.pread(memory,8,address);budget.check()
                if len(pointer_raw)!=8:refuse('runtime_extension_slot_read')
                pointer=int.from_bytes(pointer_raw,'little')
                live=at_address(mappings,pointer)
                path,_=origin(pointer,name)
                if library is None:
                    library=path;lib_info=library.stat();lib_group=file_group(mappings,library,lib_info)
                    if library==extension:refuse('runtime_sqlite_library_not_separate')
                    library_handle=handles.enter_context(loaded_object(library))
                if path!=library or live not in lib_group:refuse('runtime_sqlite_library_ambiguous')
                if (pointer!=ctypes.cast(getattr(handle,name),ctypes.c_void_p).value
                        or pointer!=ctypes.cast(getattr(library_handle,name),ctypes.c_void_p).value):
                    refuse('runtime_sqlite_symbol_interposed')
                slot_values.append((address,pointer_raw))
            _,hashed_info,lib_hash=runtime_hash(library,budget,MAX_LIBRARY)
            if signature(hashed_info)!=signature(lib_info):refuse('runtime_sqlite_library_changed')
            # Recheck the complete selected mappings, file identities and all actual slots.
            after=read_maps(budget)
            if file_group(after,extension,ext_info)!=ext_group or file_group(after,library,lib_info)!=lib_group:
                refuse('runtime_dynamic_mapping_changed')
            for m in after:
                if ('libsqlite3' in m.path or '_sqlite3.' in m.path) and m not in ext_group and m not in lib_group:
                    refuse('runtime_sqlite_extra_mapping')
            for address,old in slot_values:
                budget.read(8)
                if os.pread(memory,8,address)!=old:refuse('runtime_sqlite_binding_changed')
                budget.check()
            if signature(extension.stat())!=signature(ext_info) or signature(library.stat())!=signature(lib_info):
                refuse('runtime_dynamic_file_changed')
        finally:os.close(memory)
        return dict(sqlite3_binding=BINDING,sqlite3_extension_sha256=ext_hash,
            sqlite3_library_sha256=lib_hash,
            sqlite3_library_realpath_sha256=hashlib.sha256(os.fsencode(library)).hexdigest())
