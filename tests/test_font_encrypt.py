import unittest
import unicodedata

from utils.encrypt_font import FONT_OBFUSCATION_PRIVATE_CODEPOINTS, FontEncrypt


class FontEncryptObfuscationPolicyTest(unittest.TestCase):
    def test_should_obfuscate_only_wide_or_fullwidth_letters_and_numbers(self):
        font_encrypt = FontEncrypt.__new__(FontEncrypt)

        self.assertTrue(font_encrypt.should_obfuscate_char("你"))
        self.assertTrue(font_encrypt.should_obfuscate_char("０"))
        self.assertFalse(font_encrypt.should_obfuscate_char("0"))
        self.assertFalse(font_encrypt.should_obfuscate_char("❶"))
        self.assertFalse(font_encrypt.should_obfuscate_char("。"))

    def test_obfuscation_codepoints_use_private_use_area(self):
        font_encrypt = FontEncrypt.__new__(FontEncrypt)

        codepoints = font_encrypt.sample_obfuscation_codepoints(64)

        self.assertEqual(len(codepoints), 64)
        self.assertTrue(all(0xE000 <= codepoint <= 0xF8FF for codepoint in codepoints))
        self.assertTrue(all(unicodedata.category(chr(codepoint)) == "Co" for codepoint in codepoints))
        self.assertFalse(any(0xAC00 <= codepoint <= 0xD7AF for codepoint in codepoints))

    def test_obfuscation_codepoint_pool_reports_capacity_overflow(self):
        font_encrypt = FontEncrypt.__new__(FontEncrypt)

        with self.assertRaisesRegex(ValueError, "可用私用区混淆码位不足"):
            font_encrypt.sample_obfuscation_codepoints(
                len(FONT_OBFUSCATION_PRIVATE_CODEPOINTS) + 1
            )


if __name__ == "__main__":
    unittest.main()
