from typing import List


def chunk_text(text: str, max_words: int) -> List[str]:
    words = text.split()
    if not words:
        return []
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]
