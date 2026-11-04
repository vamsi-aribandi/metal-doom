#!/usr/bin/env python3
"""Lower wasm2c's typed, scalar IR to native MSL. Not a bytecode interpreter."""
from pathlib import Path
import re, struct, json
from lower_cfg import lower_body
ROOT=Path(__file__).resolve().parent.parent
BUILD=ROOT/'build'
s=(BUILD/'doom.c').read_text()
def matching(text,pos):
    depth=1; i=pos+1
    while depth:
        if text[i]=='{': depth+=1
        elif text[i]=='}': depth-=1
        i+=1
    return i
# Only engine implementations; export wrappers and runtime metadata are replaced.
funcs=[]
for m in re.finditer(r'^(void|u32|u64|f32|f64) (w2c_doom_\w+)\(([^\n]*)\) \{',s,re.M):
    end=matching(s,m.end()-1)
    body=s[m.end():end-1]
    active=[True]; lines=[]
    for line in body.splitlines():
        if line.lstrip().startswith('#if'): active.append(False)
        elif line.lstrip().startswith('#else'): active[-1]=not active[-1]
        elif line.lstrip().startswith('#endif'): active.pop()
        elif all(active): lines.append(line)
    body='\n'.join(lines)
    funcs.append((m[1],m[2],m[3],body))
print('functions',len(funcs))
# Extract only immutable engine data; the CPU loads these as program assets.
data={}
for m in re.finditer(r'static const u8 (data_segment_\w+)\[\] = \{(.*?)\};',s,re.S):
    data[m[1]]=bytes(int(v,16) for v in re.findall(r'0x([0-9a-f]{2})',m[2]))
image=bytearray(100663296)
for off,name,n in re.findall(r'LOAD_DATA\(instance->w2c_memory, (\d+)u, (\w+), (\d+)\)',s):
    d=data[name]; assert len(d)==int(n); image[int(off):int(off)+len(d)]=d
# Sparse image is a regular file, compressed well in source packages; not tracked.
(BUILD/'initial-memory.bin').write_bytes(image)
table=re.findall(r'\{RefFunc, (w2c_doom_t\d+), \(wasm_rt_function_ptr_t\)wrap_(w2c_doom_\w+), \{NULL\}, 0\}',s)
sigs={name:(ret,args) for ret,name,args,_ in funcs}
indirect={}
for tid,name in table:
    ret,args=sigs[name]
    types=[p.strip().rsplit(' ',1)[0] for p in args.split(',')[1:]]
    indirect[tid]=(ret,types)
def replace_call(m):
    # wasm2c puts each indirect invocation on a single line, all args scalar.
    text=m[0]; parts=[]; start=0; level=0
    inside=text[len('CALL_INDIRECT('):-1]
    for i,c in enumerate(inside):
        if c=='(':level+=1
        if c==')':level-=1
        if c==',' and level==0:parts.append(inside[start:i].strip());start=i+1
    parts.append(inside[start:].strip())
    return 'indirect_'+parts[2]+'(instance, '+', '.join([parts[3]]+parts[5:])+')'
header=(ROOT/'engine/metal_runtime.metal').read_text()
protos=[]; bodies=[]
for ret,name,args,body in funcs:
    args=args.replace('w2c_doom*','device w2c_doom*')
    sig=f'[[visible]] {ret} {name}({args})'
    protos.append(sig+';')
    body=body.replace('uint8_t* const','device u8* const').replace('uint64_t','u64')
    body=body.replace('FUNC_PROLOGUE;','').replace('FUNC_EPILOGUE;','')
    body=re.sub(r'CALL_INDIRECT\([^\n]+?\)(?=;)',replace_call,body)
    body=re.sub(r'\(f64\)wasm_quietf\((var_f\d+)\)',r'promote_f32(\1)',body)
    body=body.replace('var_d1 = 0.01;','var_d1 = 0x3f847ae147ae147bull;')
    body=re.sub(r'(var_d\d+) < (var_d\d+)',r'f64_less(\1, \2)',body)
    body=body.replace('UNREACHABLE;', 'instance->trap = 1; '+('return;' if ret=='void' else 'return 0;'))
    # All generated memory helpers carry the memory view for bounds checking.
    try: body=lower_body(body)
    except Exception as e: raise RuntimeError(name) from e
    bodies.append(sig+' {\n'+body+('\nreturn 0;' if ret!='void' else '')+'\n}\n')
for tid,(ret,types) in indirect.items():
    sig=f'[[visible]] {ret} indirect_{tid}(device w2c_doom* instance, u32 index'+''.join(', '+t+' a'+str(i) for i,t in enumerate(types))+')'
    protos.append(sig+';')
    cases=[]
    for i,(t,name) in enumerate(table,1):
        if t!=tid:continue
        call=name+'(instance'+''.join(', a'+str(j) for j in range(len(types)))+')'
        cases.append(f'case {i}: '+('return '+call+';' if ret!='void' else call+'; return;'))
    bodies.append(sig+' { switch(index) {\n'+'\n'.join(cases)+'\ndefault: instance->trap=2; '+('return;' if ret=='void' else 'return 0;')+'}}\n')
bridge='''
// These ordinary device functions can also be called by a persistent scheduler.
void doom_initialize(device w2c_doom* s, uint offset, uint length) { w2c_doom_engine_init_0(s,offset,length); }
void doom_tick(device w2c_doom* s) { w2c_doom_engine_tick_0(s); }
void doom_render(device w2c_doom* s) { w2c_doom_engine_render_0(s); }
kernel void doom_dispatch(device u8* memory [[buffer(0)]], device u32* c [[buffer(1)]], device w2c_doom* instance [[buffer(2)]]) {
 if(c[0]==5) { instance->w2c_memory.data=memory; instance->w2c_memory.size=100663296; instance->w2c_0x5F_stack_pointer=1048576; instance->trap=0; return; }
 if(c[0]==0) c[8]=w2c_doom_engine_init_0(instance,c[1],c[2]);
 if(c[0]==1) doom_tick(instance);
 if(c[0]==2) doom_render(instance);
 if(c[0]==3) c[8]=w2c_doom_engine_alloc_0(instance,c[1]);
 if(c[0]==4) w2c_doom_engine_key_0(instance,c[1],c[2]);
 c[9]=w2c_doom_engine_framebuffer_0(instance); c[10]=w2c_doom_engine_diagnostics_0(instance);
 c[11]=w2c_doom_engine_log_0(instance); c[12]=w2c_doom_engine_log_size_0(instance); c[13]=instance->trap;
}
'''
(BUILD/'doom.metal').write_text(header+'\n'+'\n'.join(protos)+'\n'+'\n'.join(bodies)+bridge)
(BUILD/'engine.json').write_text(json.dumps({'memoryBytes':len(image),'functions':len(funcs),'tableEntries':len(table),'width':320,'height':200},indent=2)+'\n')
print(BUILD/'doom.metal')
