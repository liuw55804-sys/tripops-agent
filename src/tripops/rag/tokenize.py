import re

TOKEN_PATTERN = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Tokenize Latin words and Chinese text into deterministic search terms."""

    tokens: list[str] = []
    for raw in TOKEN_PATTERN.findall(text.lower()):
        if any("\u3400" <= character <= "\u9fff" for character in raw):
            chinese = "".join(
                character for character in raw if "\u3400" <= character <= "\u9fff"
            )
            tokens.extend(chinese[index : index + 2] for index in range(len(chinese) - 1))
            tokens.extend(character for character in chinese)
            latin = "".join(character for character in raw if character.isascii())
            if latin:
                tokens.append(latin)
        else:
            tokens.append(raw)
    return tokens

