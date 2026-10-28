"""
A library of validated CadQuery recipes, and retrieval of the ones most similar to a PartSpec.

Recipes live in recipes/*.py: a module docstring with `title:`, `description:` and `tags:` lines, followed by a complete
script that builds `result`. tests/test_recipe_book.py executes every recipe, so a broken one can never reach a prompt.

Retrieval ranks the recipes by cosine similarity between a text describing the spec and each recipe's title, description
and tags, and returns the top k:
- with Ollama embeddings (settings.embedding_model, e.g. nomic-embed-text) when the model is available,
- otherwise with TF-IDF word vectors, so the agent still works without an embedding model.
"""

import ast
import hashlib
import json
import logging
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from ollama import Client, ResponseError

from schemas import PartSpec

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECIPES_DIR = PROJECT_ROOT / "recipes"
CACHE_DIR = PROJECT_ROOT / "outputs" / "cache"

METADATA_PATTERN = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)
MIN_STEM_LENGTH = 4  # shorter words ending in 's' (e.g. 'has') are not plurals


@dataclass
class RecipeSettings:
    top_k: int = 2
    embedding_model: str = "nomic-embed-text"


settings = RecipeSettings()


@dataclass(frozen=True)
class Recipe:
    name: str
    title: str
    description: str
    tags: tuple[str, ...]
    code: str  # the script without its metadata docstring

    @property
    def search_text(self) -> str:
        return f"{self.title}. {self.description} Tags: {', '.join(self.tags)}."

    def to_prompt(self) -> str:
        return f"## {self.title}\n{self.description}\n```python\n{self.code}\n```"


def parse_recipe(path: Path) -> Recipe:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstring = ast.get_docstring(tree)
    if docstring is None:
        raise ValueError(f"Recipe {path.name} has no metadata docstring")
    metadata = dict(METADATA_PATTERN.findall(docstring))
    missing = {"title", "description"} - metadata.keys()
    if missing:
        raise ValueError(f"Recipe {path.name} is missing {sorted(missing)} in its docstring")

    code_start = tree.body[0].end_lineno or 0  # the docstring is the first statement
    code = "\n".join(source.splitlines()[code_start:]).strip()
    tags = tuple(tag.strip() for tag in metadata.get("tags", "").split(",") if tag.strip())
    return Recipe(name=path.stem, title=metadata["title"], description=metadata["description"], tags=tags, code=code)


def load_recipes(directory: Path = RECIPES_DIR) -> list[Recipe]:
    return [parse_recipe(path) for path in sorted(directory.glob("*.py"))]


def spec_query(spec: PartSpec) -> str:
    """
    Text that describes the part for retrieval. The part type alone is often vague ('wheel_spacer_disc'), so the
    description, the names of the base dimensions (e.g. 'leg_1_length') and the features are included too.
    """
    features = "; ".join(f"{f.count} x {f.type.replace('_', ' ')}" + (f" {f.location}" if f.location else "") for f in spec.features)
    parameters = ", ".join(name.replace("_", " ") for name in spec.parameters)
    return f"{spec.part_type.replace('_', ' ')}. {spec.description}. Dimensions: {parameters}. Features: {features or 'none'}."


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class Embedder(Protocol):
    name: str

    def documents(self, texts: list[str]) -> list[list[float]]: ...

    def query(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    def __init__(self, model: str, timeout_s: float = 30):
        self.name = model
        self.client = Client(timeout=timeout_s)
        # nomic-embed-text was trained with these task prefixes and works noticeably better with them
        self.document_prefix, self.query_prefix = ("search_document: ", "search_query: ") if "nomic" in model else ("", "")

    def documents(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embed(model=self.name, input=[self.document_prefix + text for text in texts])
        return [list(vector) for vector in response.embeddings]

    def query(self, text: str) -> list[float]:
        return list(self.client.embed(model=self.name, input=self.query_prefix + text).embeddings[0])


def _cached_document_embeddings(texts: list[str], embedder: Embedder, cache_dir: Path) -> list[list[float]]:
    """Embed the recipe texts, reusing vectors stored on disk; a recipe is re-embedded only when its text changes."""
    cache_path = cache_dir / f"recipe_embeddings_{re.sub(r'[^\w.-]', '_', embedder.name)}.json"
    cache: dict[str, list[float]] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    keys = [hashlib.sha256(text.encode()).hexdigest() for text in texts]
    missing = [(key, text) for key, text in zip(keys, texts, strict=True) if key not in cache]
    if missing:
        vectors = embedder.documents([text for _, text in missing])
        cache.update({key: vector for (key, _), vector in zip(missing, vectors, strict=True)})
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return [cache[key] for key in keys]


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z]+", text.lower().replace("_", " "))
    # crude stemming so that 'holes' matches 'hole' and 'legs' matches 'leg'
    return [word[:-1] if len(word) >= MIN_STEM_LENGTH and word.endswith("s") else word for word in words]


def tfidf_scores(query: str, documents: list[str]) -> list[float]:
    """Cosine similarity of TF-IDF vectors: the fallback when no embedding model is available."""
    tokenized = [Counter(_tokens(document)) for document in documents]
    document_frequency = Counter(word for counts in tokenized for word in counts)
    idf = {word: math.log((1 + len(documents)) / (1 + count)) + 1 for word, count in document_frequency.items()}

    def vector(counts: Counter[str], vocabulary: list[str]) -> list[float]:
        return [counts[word] * idf.get(word, 0.0) for word in vocabulary]

    query_counts = Counter(_tokens(query))
    vocabulary = sorted(set(query_counts) | set(document_frequency))
    query_vector = vector(query_counts, vocabulary)
    return [cosine(query_vector, vector(counts, vocabulary)) for counts in tokenized]


def find_recipes(
    spec: PartSpec,
    k: int | None = None,
    *,
    recipes: list[Recipe] | None = None,
    embedder: Embedder | None = None,
    cache_dir: Path = CACHE_DIR,
) -> list[tuple[Recipe, float]]:
    """The k recipes most similar to the spec, best first, with their similarity. embedder=None uses TF-IDF only."""
    k = settings.top_k if k is None else k
    recipes = load_recipes() if recipes is None else recipes
    if k <= 0 or not recipes:
        return []

    query = spec_query(spec)
    documents = [recipe.search_text for recipe in recipes]
    scores: list[float] | None = None
    if embedder is not None:
        try:
            vectors = _cached_document_embeddings(documents, embedder, cache_dir)
            query_vector = embedder.query(query)
            scores = [cosine(query_vector, vector) for vector in vectors]
        except (ConnectionError, ResponseError, httpx.HTTPError) as exc:
            logger.warning("Embedding model '%s' unavailable (%s), using TF-IDF. Try `ollama pull %s`.", embedder.name, exc, embedder.name)
    method = "embeddings" if scores is not None else "TF-IDF"
    if scores is None:
        scores = tfidf_scores(query, documents)

    ranked = sorted(zip(recipes, scores, strict=True), key=lambda pair: pair[1], reverse=True)[:k]
    logger.info("Recipes by %s for '%s': %s", method, spec.part_type, ", ".join(f"{r.name} ({s:.2f})" for r, s in ranked))
    return ranked
