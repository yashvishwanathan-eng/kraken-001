"""Answering SOP questions from our own documents (a small RAG: retrieve, then answer only from it).

Put SOPs and contact lists as .md or .txt files in backend/knowledge/. They are split into
paragraph-sized chunks. A question is matched to the closest chunks with TF-IDF and cosine
similarity (word-importance vectors; no model download, works offline). The best chunks go to the
language model with the rule "answer only from this text". Without a model, the best chunk itself
is returned. If nothing matches well enough, the answer says so instead of guessing.
"""

import logging
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.config import BACKEND_DIR, get_settings
from app.services.llm import LlmUnavailable, chat

log = logging.getLogger("safetyai.knowledge")

NOT_FOUND = "I couldn't find that in our documents."
MIN_SCORE = 0.08  # below this, a chunk is not really about the question
CHUNK_CHARS = 600
TOP_K = 3
STOP_WORDS = {"the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "and", "or", "what", "how", "do", "we",
              "i", "it", "be", "at", "by", "with", "should", "our", "you", "this", "that", "who", "when", "where"}


@dataclass(frozen=True)
class Chunk:
    text: str
    source: str


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP_WORDS]


def split_document(text: str, source: str) -> list[Chunk]:
    """Paragraph chunks; a heading line is carried into the chunks under it so they keep their context.
    A heading may sit directly above its paragraph (no blank line), as in most hand-written SOPs."""
    chunks: list[Chunk] = []
    heading, current = "", ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(Chunk(current.strip(), source))
            current = ""

    for block in re.split(r"\n\s*\n", text):
        lines = block.strip().splitlines()
        while lines and lines[0].lstrip().startswith("#"):
            flush()  # a new heading starts a new chunk
            heading = lines.pop(0).lstrip().lstrip("#").strip()
        body = "\n".join(lines).strip()
        if not body:
            continue
        piece = f"{heading}: {body}" if heading and not current else body
        if current and len(current) + len(piece) > CHUNK_CHARS:
            flush()
            piece = f"{heading}: {body}" if heading else body
        current = f"{current}\n{piece}" if current else piece
    flush()
    return chunks


class KnowledgeBase:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()
        self._signature: tuple = ()
        self.chunks: list[Chunk] = []
        self._vectors: list[dict[str, float]] = []
        self._idf: dict[str, float] = {}

    def _reload_if_changed(self) -> None:
        files = sorted(f for pattern in ("*.md", "*.txt") for f in self.folder.glob(pattern)
                       if f.name.lower() != "readme.md")
        signature = tuple((f.name, f.stat().st_mtime) for f in files)
        if signature == self._signature:
            return
        chunks = [c for f in files for c in split_document(f.read_text(encoding="utf-8"), f.name)]
        df = Counter(w for c in chunks for w in set(words(c.text)))
        n = len(chunks)
        self._idf = {w: math.log((1 + n) / (1 + count)) + 1 for w, count in df.items()}
        self._vectors = [self._vector(c.text) for c in chunks]
        self.chunks, self._signature = chunks, signature
        log.info("knowledge base: %d chunks from %d files", len(chunks), len(files))

    def _vector(self, text: str) -> dict[str, float]:
        counts = Counter(words(text))
        return {w: n * self._idf.get(w, 0.0) for w, n in counts.items()}

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        dot = sum(v * b.get(w, 0.0) for w, v in a.items())
        norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
        return dot / norm if norm else 0.0

    def search(self, question: str, k: int = TOP_K) -> list[tuple[float, Chunk]]:
        with self._lock:
            self._reload_if_changed()
            q = self._vector(question)
            scored = [(self._cosine(q, v), c) for v, c in zip(self._vectors, self.chunks)]
        return [pair for pair in sorted(scored, key=lambda p: p[0], reverse=True)[:k] if pair[0] >= MIN_SCORE]

    def answer(self, question: str) -> tuple[str, list[str], str]:
        """Returns (answer, source files, how) where how is groq, ollama, or 'documents' (no model used)."""
        hits = self.search(question)
        if not hits:
            return NOT_FOUND, [], "documents"
        sources = sorted({c.source for _, c in hits})
        context = "\n\n".join(f"[{c.source}] {c.text}" for _, c in hits)
        messages = [
            {"role": "system", "content": "You answer safety questions for a factory control room. Use ONLY the "
             f"context. If the answer is not in the context, reply exactly: {NOT_FOUND} Keep it under 3 sentences."},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ]
        try:
            text, how = chat(messages)
            return text.strip() or NOT_FOUND, sources, how
        except LlmUnavailable:
            best = hits[0][1]
            return f"From {best.source}: {best.text}", sources, "documents"


knowledge = KnowledgeBase(BACKEND_DIR / get_settings().knowledge_dir)
