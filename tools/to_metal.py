#!/usr/bin/env python3
"""Lower wasm2c's typed scalar IR to native MSL and an initial memory image.

The reusable engine contains ordinary callable GPU entrypoints. The standalone
app adds a thin dispatch kernel; neither output interprets Wasm bytecode.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re

from lower_cfg import lower_body


ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / 'build'
MEMORY_BYTES = 100663296


@dataclass
class Function:
    result: str
    name: str
    arguments: str
    body: str


def matching_brace(text: str, opening: int) -> int:
    """Return the position after a generated function's closing brace."""
    depth, position = 1, opening + 1
    while depth:
        if text[position] == '{':
            depth += 1
        elif text[position] == '}':
            depth -= 1
        position += 1
    return position


def resolve_directives(body: str) -> str:
    """Select wasm2c's portable memory-cache paths before lowering its CFG."""
    active, lines = [True], []
    for line in body.splitlines():
        directive = line.lstrip()
        if directive.startswith('#if'):
            active.append(False)
        elif directive.startswith('#else'):
            active[-1] = not active[-1]
        elif directive.startswith('#endif'):
            active.pop()
        elif all(active):
            lines.append(line)
    return '\n'.join(lines)


def read_functions(source: str) -> list[Function]:
    """Keep engine implementations; replace export wrappers and runtime metadata."""
    pattern = r'^(void|u32|u64|f32|f64) (w2c_doom_\w+)\(([^\n]*)\) \{'
    functions = []
    for match in re.finditer(pattern, source, re.M):
        end = matching_brace(source, match.end() - 1)
        body = resolve_directives(source[match.end():end - 1])
        functions.append(Function(match[1], match[2], match[3], body))
    return functions


def initial_memory(source: str) -> bytearray:
    """Extract immutable program data; game assets are supplied separately."""
    segments = {}
    pattern = r'static const u8 (data_segment_\w+)\[\] = \{(.*?)\};'
    for match in re.finditer(pattern, source, re.S):
        segments[match[1]] = bytes(
            int(value, 16) for value in re.findall(r'0x([0-9a-f]{2})', match[2]))
    image = bytearray(MEMORY_BYTES)
    pattern = r'LOAD_DATA\(instance->w2c_memory, (\d+)u, (\w+), (\d+)\)'
    for offset, name, length in re.findall(pattern, source):
        data = segments[name]
        assert len(data) == int(length)
        start = int(offset)
        image[start:start + len(data)] = data
    return image


def read_function_table(source: str) -> list[tuple[str, str]]:
    pattern = (r'\{RefFunc, (w2c_doom_t\d+), '
               r'\(wasm_rt_function_ptr_t\)wrap_(w2c_doom_\w+), \{NULL\}, 0\}')
    return re.findall(pattern, source)


def replace_indirect_call(match: re.Match) -> str:
    """Replace one scalar CALL_INDIRECT with a typed switch dispatcher."""
    # wasm2c places each invocation on one line. Split only top-level commas.
    text = match[0]
    inside = text[len('CALL_INDIRECT('):-1]
    parts, start, depth = [], 0, 0
    for position, character in enumerate(inside):
        if character == '(':
            depth += 1
        elif character == ')':
            depth -= 1
        elif character == ',' and depth == 0:
            parts.append(inside[start:position].strip())
            start = position + 1
    parts.append(inside[start:].strip())
    arguments = ', '.join([parts[3]] + parts[5:])
    return 'indirect_' + parts[2] + '(instance, ' + arguments + ')'


def lower_function(function: Function) -> tuple[str, str]:
    arguments = function.arguments.replace('w2c_doom*', 'device w2c_doom*')
    signature = f'[[visible]] {function.result} {function.name}({arguments})'
    body = function.body.replace('uint8_t* const', 'device u8* const')
    body = body.replace('uint64_t', 'u64')
    body = body.replace('FUNC_PROLOGUE;', '').replace('FUNC_EPILOGUE;', '')
    body = re.sub(r'CALL_INDIRECT\([^\n]+?\)(?=;)', replace_indirect_call, body)
    body = re.sub(r'\(f64\)wasm_quietf\((var_f\d+)\)', r'promote_f32(\1)', body)
    body = body.replace('var_d1 = 0.01;', 'var_d1 = 0x3f847ae147ae147bull;')
    body = re.sub(r'(var_d\d+) < (var_d\d+)', r'f64_less(\1, \2)', body)
    result = 'return;' if function.result == 'void' else 'return 0;'
    body = body.replace('UNREACHABLE;', 'instance->trap = 1; ' + result)
    try:
        body = lower_body(body)
    except Exception as error:
        raise RuntimeError(function.name) from error
    fallthrough = '\nreturn 0;' if function.result != 'void' else ''
    return signature + ';', signature + ' {\n' + body + fallthrough + '\n}\n'


def indirect_dispatchers(functions: list[Function], table: list[tuple[str, str]]):
    """Generate one dispatcher per Wasm function type, preserving table indices."""
    signatures = {function.name: function for function in functions}
    indirect = {}
    for type_id, name in table:
        function = signatures[name]
        types = [argument.strip().rsplit(' ', 1)[0]
                 for argument in function.arguments.split(',')[1:]]
        indirect[type_id] = (function.result, types)

    for type_id, (result, types) in indirect.items():
        arguments = ''.join(', ' + kind + ' a' + str(i)
                            for i, kind in enumerate(types))
        signature = (f'[[visible]] {result} indirect_{type_id}'
                     '(device w2c_doom* instance, u32 index' + arguments + ')')
        cases = []
        for index, (candidate_type, name) in enumerate(table, 1):
            if candidate_type != type_id:
                continue
            arguments = ''.join(', a' + str(i) for i in range(len(types)))
            call = name + '(instance' + arguments + ')'
            statement = 'return ' + call + ';' if result != 'void' else call + '; return;'
            cases.append(f'case {index}: ' + statement)
        fallback = 'return;' if result == 'void' else 'return 0;'
        body = (signature + ' { switch(index) {\n' + '\n'.join(cases)
                + '\ndefault: instance->trap=2; ' + fallback + '}}\n')
        yield signature + ';', body


def engine_source(functions: list[Function], table: list[tuple[str, str]]) -> str:
    declarations, bodies = [], []
    implementations = [lower_function(function) for function in functions]
    implementations.extend(indirect_dispatchers(functions, table))
    for declaration, body in implementations:
        declarations.append(declaration)
        bodies.append(body)
    runtime = (ROOT / 'engine/metal_runtime.metal').read_text()
    entrypoints = (ROOT / 'engine/entrypoints.metal').read_text()
    output = runtime + '\n' + '\n'.join(declarations) + '\n' + '\n'.join(bodies) + entrypoints
    assert not re.search(r'\bgoto\s+\w+', output), 'Unlowered branch'
    assert not re.search(r'\bdouble\s+\w+', output), 'Unlowered double'
    return output


def main():
    source = (BUILD / 'doom.c').read_text()
    functions = read_functions(source)
    table = read_function_table(source)
    image = initial_memory(source)
    engine = engine_source(functions, table)
    dispatch = (ROOT / 'app/dispatch.metal').read_text()

    # Generated assets stay in build/. The kernel-free engine can be embedded
    # in a persistent scheduler; the app loads the combined standalone source.
    (BUILD / 'initial-memory.bin').write_bytes(image)
    (BUILD / 'doom-engine.metal').write_text(engine)
    protocol = (ROOT / 'app/persistent_protocol.h').read_text()
    persistent = (ROOT / 'app/persistent.metal').read_text()
    (BUILD / 'doom.metal').write_text(engine + dispatch)
    (BUILD / 'doom-persistent.metal').write_text(engine + protocol + persistent)
    metadata = {'memoryBytes': len(image), 'functions': len(functions),
                'tableEntries': len(table), 'width': 320, 'height': 200}
    (BUILD / 'engine.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print('functions', len(functions))
    print(BUILD / 'doom.metal')


if __name__ == '__main__':
    main()
