"""Independent domain oracle for bounded, quiescent HTTP histories.

No SQL/Mapper parsing. Refund/background histories are checked separately, not
silently included in a claim of linearizability for the synchronous API phase.
"""
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class State:
    status: int
    paid: int
    refund: str = 'NONE'


@dataclass(frozen=True)
class Rules:
    expired: bool = False
    cutoff: bool = False
    pickup: bool = False


def step(state, operation, rules):
    """Return (next state, response kind). Expiry returns a Boolean in its body."""
    s, p, r = state.status, state.paid, state.refund
    if operation == 'replay':
        return state, 'ok'
    if operation == 'pay':
        if p != 0:
            return state, 'ok'
        if s == 6 or rules.expired:
            return State(6, 1, 'PENDING'), 'ok'
        if s == 1:
            return State(2, 1), 'ok'
        raise AssertionError('Unpaid order in a non-payable state')
    if operation == 'expire':
        if s == 1 and p == 0 and rules.expired:
            return State(6, 0), 'true'
        return state, 'false'
    if operation in ('user_cancel', 'admin_cancel'):
        if s == 6:
            return state, 'ok'
        allowed = s != 5 if operation == 'admin_cancel' else s in (1, 2) and not rules.cutoff
        if not allowed:
            return state, 'reject'
        return State(6, p, 'PENDING' if p == 1 else 'NONE'), 'ok'
    transitions = {'confirm': (2, 3), 'ready': (3, 4), 'redeem': (4, 5)}
    before, after = transitions[operation]
    if s == before and p == 1 and (operation != 'redeem' or rules.pickup):
        return State(after, 1), 'ok'
    return state, 'reject'


def witness(initial, final, history, rules):
    """Find a legal ordering respecting completed-before-invoked precedence.

Bounded to eight complete operations. A None result rejects the history; this
does not resolve lost HTTP outcomes or prove arbitrary executions correct.
"""
    assert len(history) <= 8
    predecessors = [sum(1 << j for j, other in enumerate(history)
                        if j != i and other['endNs'] < item['startNs'])
                    for i, item in enumerate(history)]
    full = (1 << len(history)) - 1

    @lru_cache(None)
    def visit(mask, state):
        if mask == full:
            return () if state == final else None
        for i, item in enumerate(history):
            bit = 1 << i
            if mask & bit or predecessors[i] & mask != predecessors[i]:
                continue
            following, result = step(state, item['operation'], rules)
            if result != item['result']:
                continue
            suffix = visit(mask | bit, following)
            if suffix is not None:
                return (i,) + suffix
        return None

    return visit(0, initial)


def legal_change(before, after):
    """Committed domain changes, including asynchronous refund confirmation."""
    if before == after:
        return True
    s, p, r = before.status, before.paid, before.refund
    if before == State(1, 0):
        return after in (State(2, 1), State(6, 0), State(6, 1, 'PENDING'))
    if before == State(6, 0):
        return after == State(6, 1, 'PENDING')
    if p == 1 and r == 'NONE' and s in (2, 3, 4):
        return after in (State(s + 1, 1), State(6, 1, 'PENDING'))
    if before == State(6, 1, 'PENDING'):
        return after in (State(6, 2, 'SUCCEEDED'), State(6, 1, 'FAILED'))
    if before == State(6, 1, 'FAILED'):
        return after == State(6, 1, 'PENDING')
    return False


def audit_chain(rows):
    assert rows, 'No committed order history'
    previous = None
    for row in rows:
        before = None if row['old_status'] is None else State(row['old_status'], row['old_paid'], row['old_refund'])
        after = State(row['new_status'], row['new_paid'], row['new_refund'])
        assert before == previous, ('Broken audit chain', row['id'], before, previous)
        assert (after == State(1, 0) if before is None else legal_change(before, after)), ('Illegal transition', row)
        assert row['held'] == (0 if after.status == 6 else row['quantity']), ('Capacity at committed change', row)
        assert row['quantity'] > 0
        previous = after
    return previous
