#!/usr/bin/env python3
"""Semantic tests for conservative native-loop reconstruction."""
import pathlib
import random
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from lower_cfg import Compiler, Parser, _DECL
from structured_cfg import try_structure


def structure(body):
    seq = Parser(body).sequence()
    declarations = [s.text for s in seq if s.kind == 'raw' and _DECL.match(s.text)]
    remaining = [s for s in seq if not (s.kind == 'raw' and _DECL.match(s.text))]
    cfg = Compiler()
    end = cfg.add([], ('end',))
    entry = cfg.sequence(remaining, end)
    cfg.resolve()
    entry, _ = cfg.optimize(entry)
    result = try_structure(cfg, entry)
    return None if result is None else '\n'.join(declarations) + '\n' + result


def randomized_bodies(count=256):
    """Generate ordinary C programs independently of CFG/emitter internals.

    Fixed-seed source generation explores nested loops, switch fallthrough,
    branch joins, early returns, break/continue, and side-effecting selectors.
    Loop bounds and unsigned arithmetic keep every generated program bounded
    and defined for arbitrary 32-bit input, including wraparound values.
    """
    rng = random.Random(0xD00F2026)

    def update():
        code = rng.choice([
            f'x = x * {rng.randrange(1, 31, 2)}u + s;',
            f'x ^= (s >> {rng.randrange(0, 16)}u);',
            f's = s * 1664525u + {rng.randrange(1, 100)}u; x += s;',
            f'x += {rng.randrange(1, 32)}u; s ^= x;',
        ])
        return code + ' audit = audit * 33u + x + s;'

    def condition():
        return f'((++audit, s += {rng.randrange(1, 16)}u) & {rng.choice([1, 3, 7, 15])}u)'

    def statement(depth, level=0):
        if depth == 0:
            return update()
        kind = rng.randrange(7)
        if kind <= 1:
            return (f'if ({condition()}) {{ {statement(depth-1, level)} }} '
                    f'else {{ {statement(depth-1, level)} }}')
        if kind == 2:
            # Intentional fallthrough and two keys sharing a destination.
            return (f'switch ((++audit, s++ ^ n) & 7u) {{ '
                    f'case 0: {update()} '
                    f'case 1: case 2: {statement(depth-1, level)} break; '
                    f'case 3: {statement(depth-1, level)} break; '
                    f'default: {update()} }}')
        if kind == 3 and level < 3:
            var = f'i{level}'
            inner = statement(depth-1, level+1)
            controls = (f'if ((s++ & 15u) == 3u) continue; '
                        f'if ((s++ & 15u) == 7u) break;')
            style = rng.randrange(3)
            if style == 0:
                return (f'for ({var}=0u; {var} < (n & 3u)+2u; ++{var}) '
                        f'{{ {controls} {inner} }}')
            if style == 1:
                return (f'{var}=0u; while ({var}++ < (n & 3u)+2u) '
                        f'{{ {controls} {inner} }}')
            return (f'{var}=0u; do {{ {controls} {inner} }} '
                    f'while (++{var} < (n & 3u)+2u);')
        if kind == 4:
            # Early returns inside a natural loop generally require fallback;
            # outside loops they exercise independent terminal paths.
            return f'if ({condition()} == 0u) return x ^ s; {update()}'
        return update() + ' ' + statement(depth-1, level)

    for index in range(count):
        depth = rng.choice([2, 3, 4])
        chunks = ['unsigned int x=n; unsigned int s=n^0xa531e1b9u;',
                  'unsigned int i0=0u, i1=0u, i2=0u;']
        # Vary source goto layout without consulting the CFG algorithm.
        if index % 3 == 0:
            chunks += ['if (n & 1u) goto alternate;', update(),
                       'goto joined; alternate:;', update(), 'joined:;']
        chunks += [statement(depth), statement(depth-1),
                   'goto done; done:; return x ^ s;']
        yield ' '.join(chunks)


def main():
    bodies = [
        'int x=0; int i=0; a:; if(i>=n) goto out; ++i; if(i%2) goto a; x+=i; goto a; out:; return x;',
        'int x=0; int i=0; a:; x+=n; if(n-->0) goto a; return x+n;',
        'int x=0; int i=0; a:; x+=n; if(n--<=0) goto out; goto a; out:; return x+n;',
        'int x=0; int i=0; int j=0; a:; if(i>=n) goto out; j=0; b:; if(j>=i) goto next; x+=j; ++j; goto b; next:; ++i; goto a; out:; return x;',
        'int x=0; switch(n++%4) { case 0: goto a; case 1: case 2: x=10; break; default: x=20; } goto b; a:; x=30; b:; return x+n;',
        'int x=0; int i=0; a:; if(i>=n) goto out; ++i; switch(i%4) {case 0: goto a; case 1: x+=3; break; case 2: goto out; default: x+=7;} x+=i; goto a; out:; return x;',
        'int x=0; int i=0; a:; if(i>=n) goto out; ++i; switch(i%3) {case 0: goto a; case 1: x+=3; break; default: x+=7;} x+=i; goto a; out:; return x;',
        'int x=0; int i=0; a:; ++i; if(i%2) {x+=1;} else {x+=2;} if(i<n) goto a; if(x<3) return 5; return x;',
        'unsigned int x=0; unsigned int y=0; y=(unsigned int)n; switch(y) {case -1: x=10; break; case 0: x=20; break; default: x=30;} goto out; out:; return (int)x;',
    ]
    # Multi-entry cycles must stay in the general lowering path.
    irreducible = 'int x=0; if(n%2) goto a; goto b; a:; ++x; if(x<n) goto b; return x; b:; x+=2; if(x<n) goto a; return x;'
    assert structure(irreducible) is None
    # Distinct loop exits are deliberately unsupported; conservatism is part of
    # the contract, rather than guessing how to rewrite break destinations.
    multi_exit = 'int x=0; a:; if(n--<0) goto b; if(n==2) goto c; ++x; goto a; b:; return x; c:; return x+10;'
    assert structure(multi_exit) is None
    # Preserve exact unsigned switch conversions, duplicate/backward joins,
    # multiple latches, and branches which terminate before their sibling.
    bodies += [
        'int x=0; int i=0; a:; if(i++>=n) goto out; if(i%2) {x+=3; goto a;} x+=5; if(i<n) goto a; out:; return x+i;',
        'int x=n; if(n%2) goto a; x+=3; goto b; a:; x+=5; if(n%3) goto b; x+=7; b:; if(n%5) return x; x+=11; return x;',
        'int x=0; int i=0; a:; if(i++>=n) goto out; switch(i++%4) {case 0: x+=3; case 1: case 2: x+=5; break; default: goto a;} x+=i; goto a; out:; return x+i;',
        'int x=0; switch(n%3) {case 0: goto a; case 1: goto b; default: goto c;} a:; x+=3; goto d; b:; x+=5; if(n%2) goto d; goto e; c:; x+=7; goto e; d:; x+=11; goto out; e:; x+=13; out:; return x;',
    ]
    source = ['#include <stdio.h>', 'unsigned int audit;']
    structured = 0
    for index, body in enumerate(bodies):
        result = structure(body)
        assert result is not None, f'Expected supported body {index}'
        assert not re.search(r'\bgoto\b|__doom_cfg_pc', result)
        if index in (0, 1, 2, 3, 5, 6, 7):
            assert 'while (1)' in result
        structured += 1
        source.extend([f'int original_{index}(int n) {{ {body} }}',
                       f'int structured_{index}(int n) {{ {result} }}'])
    source.append('int main(void) {')
    for index in range(len(bodies)):
        source.append(f'for(int n=-2;n<70;++n) if(original_{index}(n)!=structured_{index}(n)) {{printf("failed {index} %d\\n",n); return {index+1};}}')
    source.append('return 0; }')
    # Keep generated originals intact for the C compiler, then compare against
    # native reconstructed functions over fixed edge cases and random seeds.
    randomized, void_cases, rejected = [], [], 0
    for index, body in enumerate(randomized_bodies()):
        result = structure(body)
        if result is None:
            rejected += 1
            continue
        assert not re.search(r'\bgoto\b|__doom_cfg_pc', result)
        randomized.append(index)
        source.extend([f'unsigned int random_original_{index}(unsigned int n) {{ {body} }}',
                       f'unsigned int random_structured_{index}(unsigned int n) {{ {result} }}'])
        if index % 4 == 0:
            # Void engine routines also need exact fallthrough and early-return
            # behavior; turn the same independent source into observable writes.
            void_body = body.replace('return x ^ s;', '{ audit ^= x ^ s; return; }')
            void_body = void_body.removesuffix(' { audit ^= x ^ s; return; }') + ' audit ^= x ^ s;'
            void_result = structure(void_body)
            if void_result is not None:
                void_cases.append(index)
                source.extend([f'void void_original_{index}(unsigned int n) {{ {void_body} }}',
                               f'void void_structured_{index}(unsigned int n) {{ {void_result} }}'])
    assert len(randomized) >= 200, f'Unexpectedly narrow native coverage: {len(randomized)}'
    # Rename the preceding hand-written driver so both suites share one build.
    source = [line.replace('int main(void)', 'int fixed_cases(void)') for line in source]
    source += ['int main(void) {', 'if (fixed_cases()) return 1;',
               'unsigned int seed=0x12345678u;',
               'unsigned int edges[]={0u,1u,2u,3u,7u,15u,0x7fffffffu,0x80000000u,0xfffffffeu,0xffffffffu};',
               'for (unsigned int trial=0;trial<138u;++trial) {',
               'seed=seed*1664525u+1013904223u;',
               'unsigned int n=trial<10u ? edges[trial] : seed;']
    for index in randomized:
        source.append('{ audit=0u; '
                      f'unsigned int expected=random_original_{index}(n); '
                      'unsigned int expected_audit=audit; audit=0u; '
                      f'unsigned int actual=random_structured_{index}(n); '
                      f'if(expected!=actual || expected_audit!=audit) {{printf("random case {index} input %u\\n",n); return 1;}} }}')
    for index in void_cases:
        source.append('{ audit=0u; '
                      f'void_original_{index}(n); unsigned int expected_audit=audit; audit=0u; '
                      f'void_structured_{index}(n); '
                      f'if(expected_audit!=audit) {{printf("void case {index} input %u\\n",n); return 1;}} }}')
    source += ['}', 'return 0; }']
    with tempfile.TemporaryDirectory() as folder:
        path = pathlib.Path(folder)
        (path/'test.c').write_text('\n'.join(source))
        for opt in ('-O0', '-O2'):
            subprocess.run(['clang', '-std=c11', '-Werror', '-Wno-return-type', opt,
                            '-fsanitize=undefined', '-fno-sanitize-recover=all',
                            str(path/'test.c'), '-o', str(path/'test')], check=True, timeout=90)
            subprocess.run([str(path/'test')], check=True, timeout=10)
    print(f'{structured} fixed, {len(randomized)} randomized, and {len(void_cases)} void native-structure cases '
          f'passed at O0 and O2 with UBSan over 138 edge/random inputs; '
          f'{rejected} generated cases conservatively rejected; unsupported CFG checks passed')


if __name__ == '__main__':
    main()
