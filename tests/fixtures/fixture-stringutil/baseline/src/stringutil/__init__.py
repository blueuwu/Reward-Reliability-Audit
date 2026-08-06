def normalize_whitespace(text: str) -> str:
    words = text.split(" ")
    return " ".join(word for word in words if word)
