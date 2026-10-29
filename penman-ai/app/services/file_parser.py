import io

from docx import Document


def parse_txt(file_bytes: bytes) -> str:
    """Decode plain text files, trying utf-8 then latin-1 fallback."""
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return file_bytes.decode("latin-1", errors="ignore")


def parse_docx(file_bytes: bytes) -> str:
    """Extract text from a .docx file."""
    doc = Document(io.BytesIO(file_bytes))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def extract_text(filename: str, file_bytes: bytes) -> str:
    """Dispatch based on extension. Raises ValueError for unsupported types."""
    lower = filename.lower()
    if lower.endswith(".txt"):
        return parse_txt(file_bytes)
    if lower.endswith(".docx"):
        return parse_docx(file_bytes)
    raise ValueError("Unsupported file type. Only .txt and .docx are allowed.")