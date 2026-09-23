import unittest
from unittest.mock import patch

import sosalsa


KARINA_ID = 1235654176
VITALY_HEMUL_ID = 765591886
OTHER_USER_ID = 42


class MarriedPairsTests(unittest.TestCase):
    def test_karina_and_vitaly_can_only_match_each_other(self):
        active_users = [KARINA_ID, VITALY_HEMUL_ID, OTHER_USER_ID]
        with patch("sosalsa.get_active_users", return_value=active_users):
            self.assertEqual(
                VITALY_HEMUL_ID,
                sosalsa.get_random_active_user(-1002730880821, KARINA_ID),
            )
            self.assertEqual(
                KARINA_ID,
                sosalsa.get_random_active_user(-1002730880821, VITALY_HEMUL_ID),
            )
            self.assertIsNone(
                sosalsa.get_random_active_user(-1002730880821, OTHER_USER_ID)
            )

    def test_missing_spouse_is_not_replaced_with_another_user(self):
        with patch("sosalsa.get_active_users", return_value=[KARINA_ID, OTHER_USER_ID]):
            self.assertIsNone(
                sosalsa.get_random_active_user(-1002730880821, KARINA_ID)
            )


if __name__ == "__main__":
    unittest.main()
