"""New recovery controls; not byte-identical restoration of lost test files."""
from array import array
from dataclasses import replace
import hashlib
import struct
from types import SimpleNamespace

import pytest

from audio_backend import native_owned_cells as cells
from audio_backend import native_owned_core_seams as core
from test_bounded_core_support import synthetic_case, final_cell, primary_row


def scalar_bytes(pcm):
    return b''.join(struct.pack('<f', n / 32768.) for (n,) in struct.iter_unpack('<h', pcm))


def scalar_digest(pcm):return hashlib.sha256(scalar_bytes(pcm)).hexdigest()


def test_entire_int16_domain_raw_bytes_and_hash(monkeypatch):
    pcm=b''.join(struct.pack('<h', n) for n in range(-32768,32768))
    expected=scalar_bytes(pcm); updates=[]; real=hashlib.sha256
    class Hash:
        def __init__(self):self.h=real()
        def update(self, block):updates.append(bytes(block));self.h.update(block)
        def hexdigest(self):return self.h.hexdigest()
    monkeypatch.setattr(cells,'hashlib',SimpleNamespace(sha256=Hash))
    assert cells.float32_digest(pcm)==real(expected).hexdigest()
    assert b''.join(updates)==expected
    assert all(len(block)<=65536 for block in updates)


@pytest.mark.parametrize('length',[0,2,32766,32768,32770,65534,65536,65538,320000,639998,640000])
def test_boundaries_match_scalar(length):
    pattern=b'\0\x80\xff\x7f\xff\xff\0\0\x01\0'
    pcm=(pattern*((length+9)//10))[:length]
    assert cells.float32_digest(pcm)==scalar_digest(pcm)


class BytesSubclass(bytes):pass


@pytest.mark.parametrize('value',[None,b'\0',b'\0'*32769,b'\0'*640001,b'\0'*640002,
    bytearray(2),memoryview(b'\0\0'),BytesSubclass(b'\0\0')])
def test_invalid_inputs_preserve_refusal(value):
    with pytest.raises(cells.Unavailable,match='^invalid_cell_pcm$'):cells.float32_digest(value)


@pytest.mark.parametrize('layout',['big','short_width','float_width'])
def test_unsupported_layout_uses_scalar(monkeypatch,layout):
    calls=[]
    def factory(kind,*args):
        calls.append(kind)
        if kind=='h' and layout=='short_width':return SimpleNamespace(itemsize=4)
        if kind=='f' and layout=='float_width':return SimpleNamespace(itemsize=8)
        return array(kind,*args)
    monkeypatch.setattr(cells,'sys',SimpleNamespace(byteorder='big' if layout=='big' else 'little'))
    monkeypatch.setattr(cells,'array',factory)
    pcm=struct.pack('<hhhh',-32768,-1,0,32767)
    assert cells.float32_digest(pcm)==scalar_digest(pcm)
    assert len(calls)<=2  # Layout probes only; no block conversion.


def test_supported_layout_really_converts_arrays(monkeypatch):
    if cells.sys.byteorder!='little' or array('h').itemsize!=2 or array('f').itemsize!=4:
        pytest.skip('host does not have supported layout')
    calls=[]
    def factory(kind,*args):calls.append(kind);return array(kind,*args)
    monkeypatch.setattr(cells,'array',factory)
    assert cells.float32_digest(b'\x01\0'*20000)==scalar_digest(b'\x01\0'*20000)
    assert calls.count('h')==calls.count('f')==3  # Probe + two bounded blocks.


@pytest.mark.parametrize('count',[2,3,4])
def test_current_proof_identity_ownership_and_independent_validation(monkeypatch,count):
    case=synthetic_case([10*i for i in range(count+1)],[(2,8)])
    rows=[primary_row(1,0,count*10)]
    original=cells.float32_digest; calls=[]
    def construction(pcm):calls.append('construction');return original(pcm)
    def validation(pcm):calls.append('validation');return original(pcm)
    monkeypatch.setattr(core,'float32_digest',construction)
    monkeypatch.setattr(cells,'float32_digest',validation)
    fast=final_cell(case,rows)
    assert calls.count('construction')==1 and calls.count('validation')==1
    monkeypatch.setattr(core,'float32_digest',scalar_digest)
    monkeypatch.setattr(cells,'float32_digest',scalar_digest)
    slow=final_cell(case,rows)
    assert (fast.proof,fast.cell_id,fast.ownership,fast.pcm,fast.support,fast.parts)==(
        slow.proof,slow.cell_id,slow.ownership,slow.pcm,slow.support,slow.parts)
    with pytest.raises(cells.Unavailable):replace(fast,pcm=b'\x01\0'+fast.pcm[2:])
    with pytest.raises(cells.Unavailable):replace(fast,proof=fast.proof+b' ')
    with pytest.raises(cells.Unavailable):replace(fast,support=replace(fast.support,float32le_sha256='f'*64))
