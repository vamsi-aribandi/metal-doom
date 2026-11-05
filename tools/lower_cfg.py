#!/usr/bin/env python3
"""Lower wasm2c function bodies to structured C/Metal control flow.

This is deliberately a parser for wasm2c's statement subset, not arbitrary C.
The generated program counter and all Wasm locals live in function scope; a
dispatch iteration executes a basic block. No engine work moves to the CPU.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from structured_cfg import try_structure


class LoweringError(ValueError):
    pass


@dataclass
class Stmt:
    kind: str
    text: str = ""
    children: list[Stmt] = field(default_factory=list)


class Parser:
    def __init__(self, source: str):
        self.s = source
        self.i = 0

    def skip(self):
        while self.i < len(self.s):
            if self.s[self.i].isspace():
                self.i += 1
            elif self.s.startswith("//", self.i):
                end = self.s.find("\n", self.i)
                self.i = len(self.s) if end < 0 else end + 1
            elif self.s.startswith("/*", self.i):
                end = self.s.find("*/", self.i + 2)
                if end < 0:
                    raise LoweringError("Unterminated comment")
                self.i = end + 2
            else:
                return

    def word(self, word: str) -> bool:
        self.skip()
        end = self.i + len(word)
        if self.s[self.i:end] == word and (end == len(self.s) or not
                (self.s[end].isalnum() or self.s[end] == "_")):
            self.i = end
            return True
        return False

    def scan(self, delimiter: str) -> str:
        """Consume through a delimiter outside balanced expressions/strings."""
        self.skip()
        start = self.i
        stack: list[str] = []
        pairs = {"(": ")", "[": "]", "{": "}"}
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in "\"'":
                quote = c
                self.i += 1
                while self.i < len(self.s):
                    c = self.s[self.i]
                    self.i += 1
                    if c == "\\":
                        self.i += 1
                    elif c == quote:
                        break
                continue
            if self.s.startswith("//", self.i) or self.s.startswith("/*", self.i):
                self.skip()
                continue
            if not stack and c == delimiter:
                result = self.s[start:self.i].strip()
                self.i += 1
                return result
            if c in pairs:
                stack.append(pairs[c])
            elif c in ")]}":
                if not stack or stack.pop() != c:
                    raise LoweringError(f"Unbalanced expression near {self.s[self.i:self.i+50]!r}")
            self.i += 1
        raise LoweringError(f"Expected {delimiter!r} after {self.s[start:start+80]!r}")

    def condition(self):
        self.skip()
        if self.i == len(self.s) or self.s[self.i] != "(":
            raise LoweringError("Expected parenthesized condition")
        self.i += 1
        return self.scan(")")

    def sequence(self, closing=False):
        children = []
        while True:
            self.skip()
            if self.i == len(self.s):
                if closing:
                    raise LoweringError("Missing closing brace")
                break
            if self.s[self.i] == "}":
                if not closing:
                    raise LoweringError("Unexpected closing brace")
                self.i += 1
                break
            children.append(self.statement())
        return children

    def statement(self):
        self.skip()
        if self.s[self.i] == "#":
            raise LoweringError("Preprocess conditional directives before CFG lowering")
        if self.s[self.i] == "{":
            self.i += 1
            return Stmt("block", children=self.sequence(True))
        if self.word("if"):
            cond = self.condition()
            yes = self.statement()
            no = self.statement() if self.word("else") else Stmt("block")
            return Stmt("if", cond, [yes, no])
        if self.word("while"):
            return Stmt("while", self.condition(), [self.statement()])
        if self.word("do"):
            child = self.statement()
            if not self.word("while"):
                raise LoweringError("Expected while after do body")
            cond = self.condition()
            if self.scan(";"):
                raise LoweringError("Unexpected text after do/while")
            return Stmt("do", cond, [child])
        if self.word("for"):
            header = self.condition()
            split = Parser(header)
            init, cond = split.scan(";"), split.scan(";")
            step = header[split.i:].strip()
            return Stmt("for", cond or "1", [Stmt("raw", init + ";"),
                         Stmt("raw", step + ";"), self.statement()])
        if self.word("switch"):
            return Stmt("switch", self.condition(), [self.statement()])
        if self.word("case"):
            return Stmt("case", self.scan(":"))
        if self.word("default"):
            if self.scan(":"):
                raise LoweringError("Unexpected text after default")
            return Stmt("default")
        if self.word("goto"):
            label = self.scan(";")
            if not re.fullmatch(r"[A-Za-z_]\w*", label):
                raise LoweringError(f"Unsupported goto target: {label}")
            return Stmt("goto", label)
        for word in ("return", "break", "continue"):
            if self.word(word):
                tail = self.scan(";")
                return Stmt(word, word + (" " + tail if tail else "") + ";")
        label = re.match(r"([A-Za-z_]\w*)\s*:", self.s[self.i:])
        if label:
            self.i += label.end()
            return Stmt("label", label[1])
        return Stmt("raw", self.scan(";") + ";")


# wasm2c puts all its locals at function scope. These are the generated scalar
# types and the memory-cache declarations; adding a type requires an explicit
# update instead of silently miscompiling a declaration as an expression.
_DECL = re.compile(r"^(?:(?:const|volatile|static|device)\s+)*(?:u32|u64|s32|s64|f32|f64|v128|"
                   r"u8|u16|s8|s16|int|unsigned|long|float|double|bool|size_t|"
                   r"u?int(?:8|16|32|64)_t|void|wasm_rt_\w+_t)\b")


@dataclass
class Block:
    lines: list[str]
    term: tuple


class Compiler:
    def __init__(self):
        self.blocks: list[Block] = []
        self.labels: dict[str, int] = {}

    def add(self, lines, term):
        index = len(self.blocks)
        self.blocks.append(Block(lines, term))
        return index

    def sequence(self, seq, nxt, brk=None, cont=None, cases=None):
        for stmt in reversed(seq):
            nxt = self.compile(stmt, nxt, brk, cont, cases)
        return nxt

    def compile(self, stmt, nxt, brk=None, cont=None, cases=None):
        kind = stmt.kind
        if kind == "block":
            return self.sequence(stmt.children, nxt, brk, cont, cases)
        if kind == "label":
            if stmt.text in self.labels:
                raise LoweringError(f"Duplicate label {stmt.text}")
            self.labels[stmt.text] = nxt
            return nxt
        if kind in ("case", "default"):
            if cases is None:
                raise LoweringError("Case outside a switch")
            cases.append((stmt.text if kind == "case" else None, nxt))
            return nxt
        if kind == "goto":
            return self.add([], ("label", stmt.text))
        if kind == "return":
            return self.add([stmt.text], ("return",))
        if kind in ("break", "continue"):
            target = brk if kind == "break" else cont
            if target is None:
                raise LoweringError(f"{kind} outside a loop/switch")
            return self.add([], ("jump", target))
        if kind == "raw":
            return self.add([stmt.text] if stmt.text != ";" else [], ("jump", nxt))
        if kind == "if":
            yes, no = [self.compile(s, nxt, brk, cont, cases) for s in stmt.children]
            return self.add([], ("branch", stmt.text, yes, no))
        if kind in ("while", "do", "for"):
            test = self.add([], ("pending",))
            step = test
            if kind == "for":
                step = self.compile(stmt.children[1], test, nxt, test)
            body = self.compile(stmt.children[-1], step, nxt, step, cases)
            self.blocks[test].term = ("branch", stmt.text, body, nxt)
            if kind == "for":
                return self.compile(stmt.children[0], test, brk, cont)
            return body if kind == "do" else test
        if kind == "switch":
            switch_cases = []
            self.compile(stmt.children[0], nxt, nxt, cont, switch_cases)
            switch_cases.reverse()
            default = next((target for key, target in switch_cases if key is None), nxt)
            return self.add([], ("switch", stmt.text,
                                    [(key, target) for key, target in switch_cases if key is not None], default))
        raise LoweringError(f"Unsupported statement {kind}")

    def resolve(self):
        for block in self.blocks:
            if block.term[0] == "label":
                try:
                    block.term = ("jump", self.labels[block.term[1]])
                except KeyError as e:
                    raise LoweringError(f"Undefined label {block.term[1]}") from e

    @staticmethod
    def targets(term):
        if term[0] == "jump":
            return [term[1]]
        if term[0] == "branch":
            return list(term[2:])
        if term[0] == "switch":
            return [target for _, target in term[2]] + [term[3]]
        return []

    def optimize(self, entry):
        # Collapse empty forwarding blocks, including goto/label placeholders.
        def forward(index):
            seen = set()
            while index not in seen:
                seen.add(index)
                block = self.blocks[index]
                if block.lines or block.term[0] != "jump":
                    break
                index = block.term[1]
            return index
        for block in self.blocks:
            t = block.term
            if t[0] == "jump":
                block.term = ("jump", forward(t[1]))
            elif t[0] == "branch":
                block.term = ("branch", t[1], forward(t[2]), forward(t[3]))
            elif t[0] == "switch":
                block.term = ("switch", t[1], [(key, forward(target)) for key, target in t[2]], forward(t[3]))
        entry = forward(entry)
        # Merge linear blocks. Only merge single-predecessor targets so this
        # never duplicates a loop body or a branch destination.
        reachable, stack = set(), [entry]
        while stack:
            i = stack.pop()
            if i not in reachable:
                reachable.add(i)
                stack.extend(self.targets(self.blocks[i].term))
        refs = {i: 0 for i in reachable}
        refs[entry] += 1
        for i in reachable:
            for target in self.targets(self.blocks[i].term):
                refs[target] += 1
        removed = set()
        for i in sorted(reachable):
            if i in removed:
                continue
            block = self.blocks[i]
            while block.term[0] == "jump":
                target = block.term[1]
                if target == i or refs[target] != 1:
                    break
                if target in removed:
                    raise LoweringError("Internal CFG merge invariant failed")
                other = self.blocks[target]
                block.lines.extend(other.lines)
                block.term = other.term
                removed.add(target)
        return entry, reachable - removed


def lower_body(body: str) -> str:
    """Return a goto-free equivalent of a wasm2c function's brace-less body.

    Conditional preprocessing must already be resolved. Functions without goto
    are returned unchanged. Locals must follow wasm2c's function-scope pattern;
    nested declarations are intentionally rejected instead of changing scope.
    """
    if not re.search(r"\bgoto\s+\w+\s*;", body):
        return body
    if "__doom_cfg_pc" in body or "__doom_struct_" in body:
        raise LoweringError("Reserved CFG program counter name already in use")
    seq = Parser(body).sequence()
    declarations, remaining = [], []
    for stmt in seq:
        if stmt.kind == "raw" and _DECL.match(stmt.text):
            declarations.append(stmt.text)
        else:
            remaining.append(stmt)
    def check_nested(stmt):
        if stmt.kind == "raw" and _DECL.match(stmt.text):
            raise LoweringError("Nested declaration is outside the wasm2c subset")
        for child in stmt.children:
            check_nested(child)
    for stmt in remaining:
        check_nested(stmt)
    cfg = Compiler()
    end = cfg.add([], ("end",))
    entry = cfg.sequence(remaining, end)
    cfg.resolve()
    entry, reachable = cfg.optimize(entry)
    structured = try_structure(cfg, entry)
    if structured is not None:
        return "\n".join("  " + text for text in declarations) + "\n" + structured
    # Stable small IDs make the emitted source readable and reduce switch range.
    order = [entry] + sorted(reachable - {entry})
    ids = {old: new for new, old in enumerate(order)}
    pc = "__doom_cfg_pc"
    out = ["  " + text for text in declarations]
    out += [f"  int {pc} = {ids[entry]};", f"  while ({pc} >= 0) {{", f"    switch ({pc}) {{"]
    for old in order:
        block = cfg.blocks[old]
        out.append(f"      case {ids[old]}: {{")
        t = block.term
        # A block whose conditional back edge targets itself is already a
        # complete natural loop. Keep that loop inside one dispatch case so
        # Metal sees its induction variables and pixel loads directly, rather
        # than routing every iteration through the outer program-counter switch.
        # Other blocks may still jump here: entering this do/while always runs
        # the body once, exactly as entering the original basic block does.
        self_loop = t[0] == "branch" and old in t[2:] and t[2] != t[3]
        if self_loop:
            condition = t[1] if t[2] == old else "!(" + t[1] + ")"
            exit_target = t[3] if t[2] == old else t[2]
            out.append("        do {")
            out.extend("          " + line for line in block.lines)
            out.append("        } while (" + condition + ");")
            t = ("jump", exit_target)
        else:
            out.extend("        " + line for line in block.lines)
        if t[0] == "jump":
            out.append(f"        {pc} = {ids[t[1]]};")
        elif t[0] == "branch":
            out.append(f"        {pc} = ({t[1]}) ? {ids[t[2]]} : {ids[t[3]]};")
        elif t[0] == "switch":
            out.append(f"        switch ({t[1]}) {{")
            out.extend(f"          case {key}: {pc} = {ids[target]}; break;" for key, target in t[2])
            out.append(f"          default: {pc} = {ids[t[3]]}; break;")
            out.append("        }")
        elif t[0] == "end":
            out.append(f"        {pc} = -1;")
        elif t[0] != "return":
            raise LoweringError(f"Unresolved terminator: {t}")
        if t[0] != "return":
            out.append("        break;")
        out.append("      }")
    out += [f"      default: {pc} = -1; break;", "    }", "  }"]
    return "\n".join(out) + "\n"


def _selftest():
    """Compare original and lowered C across branches, loops and switch cases."""
    import pathlib
    import subprocess
    import tempfile
    bodies = [
        "int x = n; if (x < 3) goto end; x *= 5; end:; return x;",
        "int x = 0; int i = 0; again:; if (i >= n) goto end; ++i; "
        "if (i % 2) { goto again; } x += i; goto again; end:; return x;",
        "int x = 0; while (n > 0) { --n; if (n == 5) continue; "
        "if (n == 2) break; if (n == 7) goto out; x += n; } out:; return x;",
        "int x = 0; switch (n % 4) { case 0: goto a; case 1: case 2: "
        "x = 10; break; default: x = 20; } goto b; a:; x = 30; b:; return x;",
        "int x = 0; do { ++x; if (x == n) goto out; } while (x < 9); out:; return x;",
        "int x = 0; int i = 0; for (i=0; i<n; ++i) { "
        "if (i == 3) continue; if (i == 8) goto out; x += i; } out:; return x;",
        "int x = 0; while (n--) { switch(n % 3) { case 0: continue; "
        "case 1: ++x; break; default: goto out; } x += 3; } out:; return x;",
    ]
    # Both back-edge polarities must evaluate side-effecting conditions exactly
    # once per iteration. The third case leaves and re-enters the same loop,
    # checking that folding it does not change its entry or its exit state.
    single_block_loops = [
        "int x = 0; loop:; x += n; if (n-- > 0) goto loop; return x + n;",
        "int x = 0; loop:; x += n; if (n-- <= 0) goto out; goto loop; out:; return x + n;",
        "int x = 0; int rounds = 0; loop:; x += n; if (n-- > 0) goto loop; "
        "if (++rounds < 3) { n = rounds; goto loop; } return x + n;",
    ]
    for body in single_block_loops:
        lowered = lower_body(body)
        assert "__doom_cfg_pc" not in lowered or "do {" in lowered, "Single-block loop was left in the PC dispatcher"
    bodies += single_block_loops
    source = ["#include <stdio.h>"]
    for i, body in enumerate(bodies):
        lowered = lower_body(body)
        assert not re.search(r"\bgoto\b", lowered)
        source += [f"int original_{i}(int n) {{ {body} }}", f"int lowered_{i}(int n) {{ {lowered} }}"]
    source.append("int main(void) {")
    for i in range(len(bodies)):
        source.append(f"for (int n=0; n<30; ++n) if(original_{i}(n) != lowered_{i}(n)) return {i+1};")
    source.append('puts("CFG lowering selftests passed"); return 0; }')
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp)
        (path / "test.c").write_text("\n".join(source))
        subprocess.run(["clang", "-std=c11", "-Werror", "-Wno-return-type", "-O1", str(path / "test.c"), "-o", str(path / "test")], check=True)
        subprocess.run([str(path / "test")], check=True, timeout=5)


if __name__ == "__main__":
    _selftest()
