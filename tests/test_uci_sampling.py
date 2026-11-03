import unittest
from unittest.mock import patch

import torch

from maia3.uci import sample_from_logits


class SampleFromLogitsTests(unittest.TestCase):
    def test_nucleus_sampling_keeps_the_probability_that_crosses_threshold(self):
        logits = torch.log(torch.tensor([0.6, 0.25, 0.15]))

        with patch("maia3.uci.torch.multinomial", return_value=torch.tensor([0])) as sample:
            sample_from_logits(logits, temperature=1.0, top_p=0.8)

        sampled_probabilities = sample.call_args.args[0]
        self.assertEqual(sampled_probabilities.numel(), 2)
        torch.testing.assert_close(
            sampled_probabilities,
            torch.tensor([0.6 / 0.85, 0.25 / 0.85]),
        )

    def test_zero_temperature_selects_the_highest_logit(self):
        logits = torch.tensor([0.2, 0.8, -0.5])

        with patch("maia3.uci.torch.multinomial") as sample:
            selected = sample_from_logits(logits, temperature=0, top_p=0.8)

        self.assertEqual(selected, 1)
        sample.assert_not_called()


if __name__ == "__main__":
    unittest.main()
