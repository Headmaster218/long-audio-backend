"""Prepared model-free parser and closed-dispatch cases. No child is launched."""
import ast
import importlib.util
import json
from pathlib import Path
import unittest
HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('prepared_loader_probe',HERE/'loader_probe.py')
p=importlib.util.module_from_spec(s);s.loader.exec_module(p)

class LoaderCases(unittest.TestCase):
    def test_exact_three_commands(self):
        self.assertEqual(set(p.PROGRAMS),{'loader-ffmpeg','loader-ffprobe','loader-readelf'})
        for step,target in p.PROGRAMS.items():self.assertEqual(p.loader_argv(step),[p.LOADER,'--list',target])
    def test_arbitrary_dispatch_rejected(self):
        for value in ['/bin/sh','/usr/bin/ffmpeg','loader-ffmpeg --help','',None,[],3]:
            with self.subTest(value=value),self.assertRaises(RuntimeError):p.loader_argv(value)
    def test_parse_fixed_forms(self):
        raw=b'\tlinux-vdso.so.1 (0xabcdef)\n\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x1234)\n\t/lib64/ld-linux-x86-64.so.2 (0x5678)\n'
        rows,vdso=p.parse_listing(raw)
        self.assertTrue(vdso);self.assertEqual(len(rows),2)
        self.assertEqual(rows[0],{'soname':'libc.so.6','reported_path':'/lib/x86_64-linux-gnu/libc.so.6'})
    def test_outside_or_malformed_paths_rejected(self):
        for path in ['/tmp/lib.so','/etc/passwd','/usr/lib/x86_64-linux-gnu/../secret','/usr/lib/x86_64-linux-gnu//lib.so','/lib/x86_64-linux-gnu/./lib.so','/usr/lib/x86_64-linux-gnu/a b.so']:
            with self.subTest(path=path),self.assertRaises(RuntimeError):p.allowed_spelling(path)
    def test_diagnostics_duplicates_and_bounds_rejected(self):
        line=b'libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x1234)\n'
        for raw in [b'',b'warning: unexpected\n',b'libx.so => not found\n',line+line,b'x'*32769,b'\xff']:
            with self.subTest(raw=raw[:40]),self.assertRaises((RuntimeError,UnicodeError)):p.parse_listing(raw)
    def test_parent_component_alias_target_rejected(self):
        for target in ['../lib.so','a/../lib.so','a/../../lib.so','/usr/lib/../lib.so']:
            with self.subTest(target=target),self.assertRaises(RuntimeError):p.checked_alias_target(target)
    def test_exact_loader_arrow(self):
        rows,_=p.parse_listing(f'{p.LOADER_ALIASES[0]} => {p.LOADER} (0x1)\n'.encode())
        self.assertEqual(rows[0],dict(soname='ld-linux-x86-64.so.2',reported_path=p.LOADER_ALIASES[0]))
    def test_wrong_loader_arrows_rejected(self):
        for left,right in [('/tmp/ld.so',p.LOADER),(p.LOADER_ALIASES[0],p.SYSTEM+'/libc.so.6')]:
            with self.assertRaises(RuntimeError):p.parse_listing(f'{left} => {right} (0x1)\n'.encode())
    def test_alternative_changed_target(self):
        first=next(iter(p.ALTERNATIVE_CHAINS));middle,last,tail,_=p.ALTERNATIVE_CHAINS[first]
        links={first:middle,middle:last,last:tail}
        self.assertEqual(p.checked_alternative(first,links.__getitem__),str(Path(last).parent/tail))
        links[middle]=p.SYSTEM+'/wrong.so'
        with self.assertRaises(RuntimeError):p.checked_alternative(first,links.__getitem__)
    def test_alternative_missing_link(self):
        first=next(iter(p.ALTERNATIVE_CHAINS))
        def missing(path):raise FileNotFoundError(path)
        with self.assertRaises(FileNotFoundError):p.checked_alternative(first,missing)
    def test_other_etc_alternative_rejected(self):
        with self.assertRaises(RuntimeError):p.checked_alternative('/etc/alternatives/other',lambda path:'unused')
    def test_spelling_union_convergence_and_conflict(self):
        rows={'a':{'reported_path':'/lib/x.so','canonical_path':'/usr/lib/x.1.so'},'b':{'reported_path':'/usr/lib/x.so','canonical_path':'/usr/lib/x.1.so'}}
        targets=[{'libraries':rows},{'libraries':dict(rows)}]
        self.assertEqual(p.spelling_union(targets),{r['reported_path']:r['canonical_path'] for r in rows.values()})
        targets[1]['libraries']['a']={'reported_path':'/lib/x.so','canonical_path':'/usr/lib/wrong.so'}
        with self.assertRaises(RuntimeError):p.spelling_union(targets)
    def test_report_exact_and_one_over(self):
        self.assertEqual(len(p.checked_report(b'x'*163840)),163840)
        with self.assertRaises(RuntimeError):p.checked_report(b'x'*163841)
        self.assertEqual(3*p.LOG_CAP+p.REPORT_CAP+4096,266240)
    def test_ownership_AST_unchanged(self):
        get=lambda path:next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='child')
        previous=HERE.parent/'runtime-inventory-prep/inspect_runtime_guarded.py'
        self.assertEqual(ast.dump(get(previous),include_attributes=False),ast.dump(get(HERE/'loader_probe.py'),include_attributes=False))
    def test_fixed_caps_and_ordinary_guard(self):
        self.assertEqual((p.MAX_FILES,p.MAX_FILE_BYTES,p.MAX_TOTAL_BYTES,p.MAX_ALIASES),(256,128*1024*1024,384*1024*1024,256))
        self.assertLessEqual(3*p.LOG_CAP+p.REPORT_CAP+4096,288*1024)
        d=json.loads((HERE/'probe.synthetic.spec.json').read_text())
        self.assertEqual((d['absolute_seconds'],d['work_seconds'],d['rss_cap_bytes'],d['address_space_cap_bytes']),(300,240,67108864,1073741824))
        self.assertEqual(set(d['runtime_files']),{d['argv'][0]})
    def test_no_new_supervisor(self):
        tree=ast.parse((HERE/'loader_probe.py').read_text());calls=[ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n,ast.Call)]
        self.assertEqual(calls.count('subprocess.Popen'),1)
        for forbidden in ['os._exit','os.kill','os.killpg','signal.setitimer','subprocess.run']:self.assertNotIn(forbidden,calls)

if __name__=='__main__':unittest.main()
