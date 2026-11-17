import unittest

from tests._stub_db import app


class FakeCursor:
    """記錄所有 execute 的 SQL；fetchone 固定回傳旗標值。"""

    def __init__(self, flag_value=0):
        self.executed = []
        self._flag = flag_value

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchone(self):
        return (self._flag,)

    def order_updates(self):
        return [s for s in self.executed if s.startswith('UPDATE `Order`')]


class MigrationFlagTest(unittest.TestCase):
    def test_runs_when_flag_is_zero(self):
        cur = FakeCursor(0)
        self.assertTrue(app._migrate_payment_split(cur))
        self.assertEqual(len(cur.order_updates()), 5)

    def test_skips_when_flag_is_one(self):
        cur = FakeCursor(1)
        self.assertFalse(app._migrate_payment_split(cur))
        self.assertEqual(cur.order_updates(), [])

    def test_sets_flag_after_running(self):
        cur = FakeCursor(0)
        app._migrate_payment_split(cur)
        self.assertTrue(any('UPDATE site_stats SET v = 1' in s for s in cur.executed))


class MigrationOrderTest(unittest.TestCase):
    """執行順序是正確性關鍵：unpaid→cash 必須最後跑，
    否則前面 WHERE payment_method='cash' 的兩條會再抓到它們，把 is_confirmed 誤設為 1。"""

    def setUp(self):
        self.cur = FakeCursor(0)
        app._migrate_payment_split(self.cur)
        self.updates = self.cur.order_updates()

    def test_unpaid_step_is_last(self):
        self.assertIn("WHERE payment_method='unpaid'", self.updates[-1])

    def test_all_cash_steps_run_before_unpaid_step(self):
        unpaid_i = next(i for i, s in enumerate(self.updates)
                        if "WHERE payment_method='unpaid'" in s)
        cash_i = [i for i, s in enumerate(self.updates)
                  if "WHERE payment_method='cash'" in s]
        self.assertEqual(len(cash_i), 2)
        self.assertTrue(all(i < unpaid_i for i in cash_i))


class MigrationRulesTest(unittest.TestCase):
    def setUp(self):
        self.cur = FakeCursor(0)
        app._migrate_payment_split(self.cur)
        self.updates = self.cur.order_updates()

    def _stmt_for(self, where_fragment):
        return next(s for s in self.updates if where_fragment in s)

    def test_picked_up_cash_becomes_paid(self):
        s = self._stmt_for("WHERE payment_method='cash' AND is_picked_up=1")
        self.assertIn("payment_status='paid'", s)
        self.assertIn("is_confirmed=1", s)

    def test_unpicked_cash_becomes_unpaid(self):
        s = self._stmt_for("WHERE payment_method='cash' AND is_picked_up=0")
        self.assertIn("payment_status='unpaid'", s)
        self.assertIn("is_confirmed=1", s)

    def test_transfer_becomes_paid(self):
        s = self._stmt_for("WHERE payment_method='transfer'")
        self.assertIn("payment_status='paid'", s)

    def test_pending_check_becomes_transfer_pending(self):
        s = self._stmt_for("WHERE payment_method='pending_check'")
        self.assertIn("payment_method='transfer'", s)
        self.assertIn("payment_status='pending_check'", s)

    def test_unpaid_becomes_unconfirmed_cash(self):
        s = self._stmt_for("WHERE payment_method='unpaid'")
        self.assertIn("is_confirmed=0", s)
        self.assertIn("payment_method='cash'", s)
        self.assertIn("payment_status='unpaid'", s)


if __name__ == '__main__':
    unittest.main()
