#!/usr/bin/env python3
"""Test-only 32/64/128/256-thread Doom experiment; production API is unchanged.

Larger variants use whole-threadgroup barriers and shared broadcasts. Merely
increasing the original launch size would race multiple game owners and violate
its single-SIMD calling contract. Generated shaders remain under build/.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys

from test_render_states import schedule, TICKS
from test_persistent import KEY_CODES

ROOT = Path(__file__).resolve().parent.parent


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise AssertionError(f'Expected one experiment substitution: {old!r}')
    return text.replace(old, new)


def broadcast(text, scratch):
    return re.sub(r'simd_broadcast_first\(([^\n;]*?)\)',
                  lambda m: f'doom_group_first({m[1]}, lane, {scratch})', text)


def group_barriers(text):
    return text.replace('simdgroup_barrier(mem_flags::mem_device)',
                        'threadgroup_barrier(mem_flags::mem_device | mem_flags::mem_threadgroup)')


def shaders(output):
    engine = (ROOT / 'build/doom-engine.metal').read_text()
    api = (ROOT / 'engine/entrypoints.metal').read_text()
    if not engine.endswith(api):
        raise AssertionError('Rebuild the engine before running this experiment')
    dispatch = (ROOT / 'app/dispatch.metal').read_text()
    protocol = (ROOT / 'app/persistent_protocol.h').read_text()
    persistent = (ROOT / 'app/persistent.metal').read_text()
    baseline = output / 'simd.metal'
    baseline.write_text(engine + dispatch + protocol + persistent)
    helper = '''
// Test-only whole-threadgroup scalar broadcast. The second barrier protects the
// scratch word from reuse until every thread has loaded the previous value.
uint doom_group_first(uint value, uint lane, threadgroup uint *scratch) {
  if (lane == 0) *scratch = value;
  threadgroup_barrier(mem_flags::mem_threadgroup);
  uint result = *scratch;
  threadgroup_barrier(mem_flags::mem_threadgroup);
  return result;
}
'''
    api = replace_once(api, 'uint lane, uint lanes) {\n  uint result = 0;',
                       'uint lane, uint lanes, threadgroup uint *scratch) {\n  uint result = 0;')
    api = group_barriers(broadcast(api, 'scratch'))
    dispatch = replace_once(dispatch, '  const uint op = operation[0], lanes = groupSize.x;',
                            '  threadgroup uint scratch;\n  const uint op = operation[0], lanes = groupSize.x;')
    dispatch = replace_once(dispatch, 'doom_initialize(instance, operation[1], operation[2], lane, lanes)',
                            'doom_initialize(instance, operation[1], operation[2], lane, lanes, &scratch)')
    job_boundary = persistent.index('\ninline void doom_persistent_job(')
    kernel_boundary = persistent.index('\n#ifndef DOOM_EMBEDDED')
    publish = persistent[:job_boundary]
    job, kernel = persistent[job_boundary:kernel_boundary], persistent[kernel_boundary:]
    publish = replace_once(publish, 'uint renders, uint keys, uint lane, uint lanes) {',
                           'uint renders, uint keys, uint lane, uint lanes, threadgroup uint *scratch) {')
    publish = group_barriers(broadcast(publish, 'scratch'))
    job = replace_once(job, 'const device DoomReplayOperation *operations, uint lane, uint lanes) {',
                       'const device DoomReplayOperation *operations, uint lane, uint lanes, '
                       'threadgroup uint *scratch) {')
    kernel = replace_once(kernel, 'uint lane [[thread_index_in_simdgroup]], uint lanes [[threads_per_simdgroup]]) {',
                          'uint lane [[thread_index_in_threadgroup]], uint3 groupSize [[threads_per_threadgroup]]) {\n'
                          '  const uint lanes = groupSize.x;\n  threadgroup uint scratch;')
    kernel = replace_once(kernel,
                          'doom_persistent_job(memory, instance, wad, host, out, config, operations, lane, lanes);',
                          'doom_persistent_job(memory, instance, wad, host, out, config, operations, lane, lanes, &scratch);')
    job = replace_once(job, 'doom_initialize(instance, offset, config.wadBytes, lane, lanes)',
                       'doom_initialize(instance, offset, config.wadBytes, lane, lanes, scratch)')
    # Both initial and later publications participate collectively.
    call = 'result, renders, previousKeys, lane, lanes);'
    if job.count(call) != 2:
        raise AssertionError('Unexpected publication call count')
    job = job.replace(call, 'result, renders, previousKeys, lane, lanes, scratch);')
    job = group_barriers(broadcast(job, 'scratch'))
    variant = engine[:-len((ROOT / 'engine/entrypoints.metal').read_text())] + helper + api + dispatch + protocol + publish + job + kernel
    if 'simd_broadcast_first' in variant or 'simdgroup_barrier' in variant:
        raise AssertionError('Unconverted cross-SIMD synchronization')
    group = output / 'group.metal'
    group.write_text(variant)
    return baseline, group


def operations(events, ticks, per_frame, path):
    result, next_event = [], 0
    for tick in range(1, ticks + 1):
        while next_event < len(events) and events[next_event][0] == tick:
            _, pressed, key = events[next_event]
            result.append((4, pressed, key))
            next_event += 1
        result.append((1, 0, 0))
        if tick % per_frame == 0 or tick == ticks:
            result.extend(((2, 0, 0), (9, 0, 0)))
    path.write_text(''.join(f'{a} {b} {c}\n' for a,b,c in result))


def scripted():
    events, previous = [], 0
    for i in range(350):
        keys = (1 if i < 140 else 0) | (8 if 140 <= i < 210 else 0) | (64 if 210 <= i < 280 else 0) | (128 if i == 280 else 0)
        for bit, key in enumerate(KEY_CODES):
            if (keys ^ previous) & (1 << bit):
                events.append((i+1,(keys >> bit)&1,key))
        previous = keys
    return events


def run(command, log, timeout=180):
    with log.open('w') as stream:
        subprocess.run([str(x) for x in command],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                       timeout=timeout,check=True)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    with path.open(newline='') as stream:
        return list(csv.reader(stream))


def live_checks(args, variants, report):
    """Exercise realtime broadcasts and stop using the existing live fixture."""
    out=args.output.resolve()
    source=(ROOT/'tests/persistent_live.mm').read_text()
    source=replace_once(source,'#include "../app/main.mm"',f'#include "{ROOT / "app/main.mm"}"')
    source=replace_once(source,'if (argc != 7)', 'if (argc != 8)')
    source=replace_once(source,'SECONDS CSV REPORT','SECONDS CSV REPORT THREADS')
    source=replace_once(source,'    engine.start(',
        '    unsigned threads=std::stoul(argv[7]);\n'
        '    if (threads>engine.pipeline.maxTotalThreadsPerThreadgroup)\n'
        '      throw std::runtime_error("Too many experiment threads");\n'
        '    engine.workers=threads;\n    engine.start(')
    fixture=out/'live.mm';fixture.write_text(source)
    binary=out/'parallelism-live'
    run(['xcrun','clang++','-O2','-std=c++17','-fobjc-arc','-framework','Cocoa','-framework','Metal',
         '-framework','QuartzCore',fixture,'-o',binary],out/'compile-live.log')
    winner=max(report['variants'],key=lambda name:report['variants'][name]['wall_fps_median'])
    checks={}
    for name in dict.fromkeys(('simd32',winner)):
        threads,shader=variants[name];folder=out/f'live-{name}';folder.mkdir(exist_ok=True)
        for path in (folder/'gpu.json',folder/'cpu.json'):path.unlink(missing_ok=True)
        run([binary,args.wad.resolve(),shader,ROOT/'build/initial-memory.bin',5,
             folder/'gpu.csv',folder/'gpu.json',threads],out/f'live-{name}.log')
        run([sys.executable,ROOT/'tests/test_persistent.py','--wad',args.wad.resolve(),
             '--cpu-live',folder/'gpu.csv','--output',folder],out/f'live-cpu-{name}.log')
        gpu=json.loads((folder/'gpu.json').read_text());cpu=json.loads((folder/'cpu.json').read_text())
        for key,value in {'passed':True,'dispatches':1,'submissions':1,'status':4,
                          'stop_while_unacknowledged':True,'frames':cpu['frames_compared'],
                          'ticks':cpu['ticks'],'observed_key_mask':cpu['observed_key_mask']}.items():
            if gpu.get(key)!=value:raise AssertionError(f'{name}: live invariant {key}')
        checks[name]={'threads':threads,'gpu':gpu,'cpu_reference':cpu}
        print(f'{name}: realtime CPU exact, held-frame shutdown passed',flush=True)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wad',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'build/parallelism')
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--smoke',action='store_true',help='Check one short replay per variant only')
    args=parser.parse_args()
    if args.repeats < 1: parser.error('repeats must be positive')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    (out/'report.json').unlink(missing_ok=True)
    baseline,group=shaders(out)
    binary=out/'parallelism'
    run(['xcrun','clang++','-O2','-std=c++17','-fobjc-arc','-framework','Cocoa','-framework','Metal',
         '-framework','QuartzCore',ROOT/'tests/parallelism.mm','-o',binary],out/'compile.log')
    variants={'simd32':(32,baseline),'group32':(32,group),'group64':(64,group),
              'group128':(128,group),'group256':(256,group)}
    ops=out/'scripted.txt';operations(scripted(),35 if args.smoke else 350,1,ops)
    def replay(name,label,op_path,hash_frames):
        threads,shader=variants[name];folder=out/f'{label}-{name}'
        run([binary,args.wad.resolve(),shader,ROOT/'build/initial-memory.bin',threads,
             op_path,int(hash_frames),folder,'replay'],out/f'{label}-{name}.log')
        summary=json.loads((folder/'summary.json').read_text())
        if summary['dispatches']!=1 or summary['submissions']!=1 or summary['completion_reason']!=2:
            raise AssertionError('Replay lost single-dispatch execution')
        return folder,summary
    expected=None
    for name in variants:
        folder,summary=replay(name,'warmup',ops,True)
        actual=rows(folder/'frames.csv')
        if expected is None:expected=actual
        if actual!=expected:raise AssertionError(f'{name}: short/frame replay differs')
        print(f'{name}: warmup exact; {summary["wall_fps"]:.2f} FPS',flush=True)
    if args.smoke:return
    scripted_cpu=out/'cpu-scripted'
    run([sys.executable,ROOT/'tests/reference.py','--wad',args.wad.resolve(),
         '--ticks',350,'--scripted','--render-every-tick','--frame-hashes','--output',scripted_cpu],
        out/'cpu-scripted.log')
    cpu_summary=json.loads((scripted_cpu/'summary.json').read_text())
    cpu_hashes=dict(rows(scripted_cpu/'frame-hashes.csv')[1:])
    expected_scripted=[expected[0],['0',cpu_hashes['0'],*map(str,cpu_summary['initial_state'])]]
    for row in rows(scripted_cpu/'ticks.csv')[1:]:
        expected_scripted.append([row[0],cpu_hashes[row[0]],*row[2:]])
    for name in variants:
        folder=out/f'warmup-{name}'
        if rows(folder/'frames.csv')!=expected_scripted or (folder/'frame.rgba').read_bytes()!=(scripted_cpu/'frame.rgba').read_bytes():
            raise AssertionError(f'{name}: scripted CPU comparison failed')
    # Fresh CPU references: original C has process-global engine state.
    checks=[]
    for per_frame in (1,8):
        case=out/f'cpu-{per_frame}'
        run([sys.executable,ROOT/'tests/test_persistent.py','--wad',args.wad.resolve(),
             '--cpu-case',per_frame,'--output',case],out/f'cpu-{per_frame}.log')
        ref=rows(case/'cpu.csv')
        event_ops=out/f'events-{per_frame}.txt';operations(schedule(),TICKS,per_frame,event_ops)
        for name in variants:
            folder,summary=replay(name,f'validate-{per_frame}',event_ops,True)
            got=rows(folder/'frames.csv')
            if got!=ref:
                mismatch=next((i for i,(a,b) in enumerate(zip(ref,got)) if a!=b),min(len(ref),len(got)))
                raise AssertionError(f'{name}: CPU divergence at row {mismatch}, ticks/frame={per_frame}')
            checks.append({'variant':name,'ticks_per_frame':per_frame,'frames':len(ref)-1,
                           'dispatches':summary['dispatches'],'live_frames':summary['live_frames']})
            print(f'{name}: {len(ref)-1} CPU frame hashes/diagnostics exact ({per_frame} ticks/frame)',flush=True)
    timing={name:[] for name in variants};profiles={name:[] for name in variants}
    expected=None
    for repeat in range(args.repeats):
        order=list(variants)
        # Rotate which size runs first, rather than always favouring one temperature.
        order=order[repeat%len(order):]+order[:repeat%len(order)]
        for name in order:
            folder,summary=replay(name,f'timed-{repeat}',ops,False)
            hashes=(digest(folder/'frames.csv'),digest(folder/'frame.rgba'))
            if expected is None:expected=hashes
            if hashes!=expected:raise AssertionError(f'{name}: timed outputs differ')
            timing[name].append(summary)
            threads,shader=variants[name];profile_out=out/f'profile-{repeat}-{name}'
            run([binary,args.wad.resolve(),shader,ROOT/'build/initial-memory.bin',threads,
                 ops,0,profile_out,'profile'],out/f'profile-{repeat}-{name}.log')
            profiles[name].append(json.loads((profile_out/'profile.json').read_text()))
            print(f'{name} trial{repeat+1}: {summary["wall_fps"]:.2f} FPS',flush=True)
    report={'passed':True,'device':timing['simd32'][0]['device'],'repeats':args.repeats,
            'base_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'engine_api_sha256':digest(ROOT/'engine/entrypoints.metal'),
            'profile_scope':'12 repeated renders of the initialized scene per phase; separate from the timed gameplay route',
            'ticks_per_timed_run':350,'validation':checks,'identical_timed_outputs':True,
            'scope':'Test-only group barriers/broadcasts. Production single-SIMD API unchanged.',
            'scripted_cpu_frames_compared_per_variant':351,
            'wad_sha256':digest(args.wad),
            'host_sha256':digest(binary),
            'variants':{}}
    for name,samples in timing.items():
        report['variants'][name]={'threads':variants[name][0],'wall_fps_median':statistics.median(x['wall_fps'] for x in samples),
            'wall_fps_samples':[x['wall_fps'] for x in samples],
            'mean_gpu_ms_median':{phase:statistics.median(x['mean_gpu_ms'][phase] for x in profiles[name])
                                  for phase in ('palette','world','full_render')},
            'pipeline_max_threads':samples[0]['pipeline_max_threads'],
            'static_threadgroup_bytes':samples[0]['static_threadgroup_bytes'],
            'shader_sha256':digest(variants[name][1]),'samples':samples,'profiles':profiles[name]}
    report['live_checks']=live_checks(args,variants,report)
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v['wall_fps_median'] for k,v in report['variants'].items()},indent=2))


if __name__=='__main__':main()
