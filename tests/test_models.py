import unittest

import torch

from maia3.models import RMSNorm


class RMSNormTests(unittest.TestCase):
    def test_normalizes_by_root_mean_square_and_keeps_affine_weight(self):
        norm = RMSNorm(2)
        values = torch.tensor([[3.0, 4.0]])

        result = norm(values)

        expected = values / torch.sqrt(torch.tensor(12.5 + torch.finfo(values.dtype).eps))
        torch.testing.assert_close(result, expected)
        self.assertEqual(norm.state_dict()["weight"].shape, torch.Size([2]))


if __name__ == "__main__":
    unittest.main()
