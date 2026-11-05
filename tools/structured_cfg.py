"""Conservative reconstruction of reducible wasm2c control flow.

``try_structure(cfg, entry)`` returns native C/Metal statements, or None when
proof checks fail. The caller retains its general program-counter fallback.
Natural loops must have one entry and at most one exit, and nested loops must
form a tree. After collapsing loops, every region must be acyclic. Branches
join at a common postdominator; bounded duplication handles non-series DAGs.
No condition or switch expression is evaluated more often than in the CFG.
"""
from __future__ import annotations

from dataclasses import dataclass


class Unsupported(Exception):
    pass


@dataclass
class Loop:
    header: int
    nodes: set[int]
    exit: int | None = None


def try_structure(cfg, entry: int) -> str | None:
    """Structure a resolved, optimized lower_cfg.Compiler, without mutating it."""
    try:
        return _Emitter(cfg, entry).run()
    except (Unsupported, RecursionError):
        return None


class _Emitter:
    SINK = -1
    CONTINUE = -2
    BREAK = -3

    def __init__(self, cfg, entry):
        self.cfg, self.entry = cfg, entry
        self.reachable, stack = set(), [entry]
        while stack:
            node = stack.pop()
            if node not in self.reachable:
                self.reachable.add(node)
                stack.extend(cfg.targets(cfg.blocks[node].term))
        self.preds = {node: set() for node in self.reachable}
        for node in self.reachable:
            for target in cfg.targets(cfg.blocks[node].term):
                self.preds[target].add(node)
        self.loops = self.find_loops()
        self.count = 0
        self.limit = max(64, len(self.reachable) * 5)
        self.serial = 0

    def find_loops(self):
        dom = {node: set(self.reachable) for node in self.reachable}
        dom[self.entry] = {self.entry}
        changed = True
        while changed:
            changed = False
            for node in sorted(self.reachable):
                if node == self.entry:
                    continue
                incoming = self.preds[node]
                result = {node} | set.intersection(*(dom[p] for p in incoming))
                if result != dom[node]:
                    dom[node], changed = result, True
        loops = {}
        for latch in self.reachable:
            for header in self.cfg.targets(self.cfg.blocks[latch].term):
                if header not in dom[latch]:
                    continue
                nodes, todo = {header}, [latch]
                while todo:
                    node = todo.pop()
                    if node not in nodes:
                        nodes.add(node)
                        todo.extend(self.preds[node])
                if header in loops:
                    loops[header].nodes.update(nodes)
                else:
                    loops[header] = Loop(header, nodes)
        items = list(loops.values())
        for loop in items:
            if any(p not in loop.nodes for node in loop.nodes - {loop.header}
                   for p in self.preds[node]):
                raise Unsupported('Loop has multiple entries')
            exits = {target for node in loop.nodes
                     for target in self.cfg.targets(self.cfg.blocks[node].term)
                     if target not in loop.nodes}
            if len(exits) > 1:
                raise Unsupported('Loop has multiple exits')
            loop.exit = next(iter(exits), None)
            for other in items:
                if loop.nodes & other.nodes and not (
                        loop.nodes <= other.nodes or other.nodes <= loop.nodes):
                    raise Unsupported('Overlapping natural loops')
                if loop is not other and loop.nodes == other.nodes:
                    raise Unsupported('Ambiguous loop headers')
        return loops

    def run(self):
        out = self.region(self.reachable, self.entry, None)
        return '\n'.join('  ' + line for line in out) + '\n'

    def region(self, nodes, entry, current):
        # Only direct child loops become atomic nodes in this region.
        candidates = [loop for loop in self.loops.values()
                      if loop is not current and loop.nodes < nodes]
        if current is None:
            candidates = [loop for loop in self.loops.values() if loop.nodes <= nodes]
        children = {loop.header: loop for loop in candidates
                    if not any(loop.nodes < other.nodes for other in candidates)}
        visible = set(nodes)
        for loop in children.values():
            visible.difference_update(loop.nodes - {loop.header})

        def target(node):
            if current is not None and node == current.header:
                return self.CONTINUE
            if node not in nodes:
                if current is None or node != current.exit:
                    raise Unsupported('Unexpected region exit')
                return self.BREAK
            if node not in visible:
                raise Unsupported('Jump into nested loop')
            return node

        succ = {}
        for node in visible:
            if node in children:
                exit_node = children[node].exit
                succ[node] = [self.SINK if exit_node is None else target(exit_node)]
            else:
                succ[node] = [target(t) for t in self.cfg.targets(self.cfg.blocks[node].term)]
                if not succ[node]:
                    succ[node] = [self.SINK]
        for endpoint in (self.CONTINUE, self.BREAK):
            succ[endpoint] = [self.SINK]
        succ[self.SINK] = []

        # Postorder both proves this loop-collapsed region is a DAG (rejecting
        # irreducible cycles) and gives an order for exact postdominators.
        visited, active, order = set(), set(), []
        def visit(node):
            if node in active:
                raise Unsupported('Irreducible cycle')
            if node in visited:
                return
            active.add(node)
            for dest in succ[node]:
                visit(dest)
            active.remove(node)
            visited.add(node)
            order.append(node)
        visit(entry)
        post = {}
        for node in order:
            post[node] = {node}
            if succ[node]:
                post[node] |= set.intersection(*(post[s] for s in succ[node]))

        def join(destinations):
            common = set.intersection(*(post[d] for d in destinations))
            # A nearer postdominator has all farther postdominators in its set.
            return max(common, key=lambda n: len(post[n]))

        def indent(lines):
            return ['  ' + line for line in lines]

        def emit(node, stop):
            result = []
            while node != stop:
                self.count += 1
                if self.count > self.limit:
                    raise Unsupported('Excessive structured duplication')
                if node == self.SINK:
                    break
                if node in (self.CONTINUE, self.BREAK):
                    result.append('continue;' if node == self.CONTINUE else 'break;')
                    break
                if node in children:
                    loop = children[node]
                    body = self.region(loop.nodes, node, loop)
                    result += ['while (1) {', *indent(body), '}']
                    node = succ[node][0]
                    continue
                block = self.cfg.blocks[node]
                result.extend(block.lines)
                term = block.term
                kind = term[0]
                if kind == 'jump':
                    node = succ[node][0]
                elif kind in ('end', 'return'):
                    break
                elif kind == 'branch':
                    yes, no = succ[node]
                    merge = join([yes, no])
                    result += [f'if ({term[1]}) {{', *indent(emit(yes, merge)),
                               '} else {', *indent(emit(no, merge)), '}']
                    node = merge
                elif kind == 'switch':
                    # Select the arm with an actual switch so C integer
                    # conversion and side effects remain exact. Emit arm bodies
                    # outside it so loop break/continue target the native loop.
                    self.serial += 1
                    temp = f'__doom_struct_arm_{self.serial}'
                    groups = {}
                    for key, dest in term[2]:
                        groups.setdefault(target(dest), []).append(key)
                    default = target(term[3])
                    groups.setdefault(default, [])
                    destinations = list(groups)
                    merge = join(destinations)
                    result += [f'unsigned int {temp};', f'switch ({term[1]}) {{']
                    for arm, dest in enumerate(destinations):
                        result.extend(f'  case {key}:' for key in groups[dest])
                        if dest == default:
                            result.append('  default:')
                        result += [f'    {temp} = {arm};', '    break;']
                    result.append('}')
                    for arm, dest in enumerate(destinations):
                        prefix = 'if' if arm == 0 else 'else if'
                        result += [f'{prefix} ({temp} == {arm}) {{', *indent(emit(dest, merge)), '}']
                    node = merge
                else:
                    raise Unsupported(f'Unknown terminator {kind}')
            return result
        return emit(entry, self.SINK)
