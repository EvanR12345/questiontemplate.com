import unittest
from types import SimpleNamespace
import torch
from image_engine import ImageEngine


class Tokenizer:
    model_max_length = 77
    bos_token_id, eos_token_id, pad_token_id = 1, 2, 2
    def __call__(self, text, **kwargs):
        return {'input_ids': list(map(int, text.split()))}


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.engine = object.__new__(ImageEngine)
        self.engine.torch, self.engine.device = torch, 'cpu'
        self.engine.pipe = SimpleNamespace(tokenizer=Tokenizer(), unet=SimpleNamespace(dtype=torch.float32),
                                           text_encoder=lambda inputs: (inputs.unsqueeze(-1).float(),))

    def test_action_beyond_first_clip_window_is_encoded(self):
        prompt = ' '.join(['3'] * 80 + ['212'])
        positive, negative, count = self.engine.encode_prompts(prompt, '4', lambda *args: None)
        self.assertEqual(count, 81)
        self.assertEqual(positive.shape, (1, 154, 1))
        self.assertEqual(positive.shape, negative.shape)
        self.assertTrue((positive == 212).any())

    def test_oversized_prompt_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, 'nothing was silently truncated'):
            self.engine.encode_prompts(' '.join(['3'] * 301), '', lambda *args: None)

    def test_short_prompt_retains_one_clip_window(self):
        positive, negative, count = self.engine.encode_prompts('3 4', '', lambda *args: None)
        self.assertEqual(positive.shape, (1, 77, 1))
        self.assertEqual(negative.shape, positive.shape)


if __name__ == '__main__':
    unittest.main()
