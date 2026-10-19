"""Negative controls: an oracle that accepts every history is not useful evidence."""
import unittest
from order_state_model import State, Rules, audit_chain, witness


class OracleTest(unittest.TestCase):
    def test_overlapping_calls_may_linearize_in_reverse_completion_order(self):
        history = [dict(operation='pay', result='ok', startNs=1, endNs=9),
                   dict(operation='confirm', result='ok', startNs=2, endNs=8)]
        self.assertEqual((0, 1), witness(State(1, 0), State(3, 1), history, Rules()))

    def test_real_time_precedence_cannot_be_reordered(self):
        history = [dict(operation='confirm', result='ok', startNs=1, endNs=2),
                   dict(operation='pay', result='ok', startNs=3, endNs=4)]
        self.assertIsNone(witness(State(1, 0), State(3, 1), history, Rules()))

    def test_two_successful_redemptions_are_rejected(self):
        history = [dict(operation='redeem', result='ok', startNs=1, endNs=4),
                   dict(operation='redeem', result='ok', startNs=2, endNs=3)]
        self.assertIsNone(witness(State(4, 1), State(5, 1), history, Rules(pickup=True)))

    def test_late_payment_cannot_resurrect_cancelled_order(self):
        history = [dict(operation='pay', result='ok', startNs=1, endNs=2)]
        self.assertIsNone(witness(State(6, 0), State(2, 1), history, Rules()))

    def test_committed_history_rejects_resurrection_and_capacity_leak(self):
        def row(i, before, after, held):
            return dict(id=i, old_status=before.status if before else None,
                        old_paid=before.paid if before else None, old_refund=before.refund if before else None,
                        new_status=after.status, new_paid=after.paid, new_refund=after.refund, quantity=1, held=held)
        base = [row(1, None, State(1, 0), 1), row(2, State(1, 0), State(6, 0), 0)]
        self.assertEqual(State(6, 0), audit_chain(base))
        with self.assertRaises(AssertionError):
            audit_chain(base + [row(3, State(6, 0), State(2, 1), 1)])
        with self.assertRaises(AssertionError):
            audit_chain([base[0], row(2, State(1, 0), State(6, 0), 1)])


if __name__ == '__main__':
    unittest.main()
