import cadquery as cq
import pytest

from cad_generator import check_code
from recipe_book import RECIPES_DIR, find_recipes, load_recipes, parse_recipe, spec_query
from schemas import PartSpec
from validator import find_hole_diameters

RECIPES = load_recipes()

WHEEL_SPACER = PartSpec(
    part_type="wheel_spacer_disc",
    description="Wheel spacer disc with center bore and bolt holes",
    parameters={"diameter": 150, "thickness": 20},
    features=[{"type": "center_bore", "parameters": {"diameter": 65}}, {"type": "through_hole", "count": 5, "parameters": {"diameter": 15}}],
)
BRACKET = PartSpec(
    part_type="L-shaped bracket",
    description="L-shaped bracket with holes in each leg",
    parameters={"leg1_length": 80, "leg2_length": 40, "width": 40, "thickness": 5},
    features=[{"type": "through_hole", "count": 2, "parameters": {"diameter": 6}, "location": "leg1"}],
)


def run_recipe(code: str) -> cq.Shape:
    namespace: dict = {}
    exec(compile(code, "recipe", "exec"), namespace)
    result = namespace["result"]
    return result.val() if isinstance(result, cq.Workplane) else result


# --- The library itself: every recipe must be safe to show to the LLM ---


def test_library_is_not_empty_and_names_are_unique():
    assert len(RECIPES) >= 10
    assert len({recipe.name for recipe in RECIPES}) == len(RECIPES)


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda recipe: recipe.name)
def test_recipe_has_metadata(recipe):
    assert recipe.title and recipe.description and recipe.tags
    assert '"""' not in recipe.code.split("\n")[0]  # the metadata docstring is stripped from the code


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda recipe: recipe.name)
def test_recipe_passes_the_generator_static_checks(recipe):
    # Including the API checker: a recipe flagged here would mean a false positive in cadquery_api
    assert check_code(recipe.code) == []


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda recipe: recipe.name)
def test_recipe_builds_one_valid_solid(recipe):
    shape = run_recipe(recipe.code)

    assert shape.isValid()
    assert len(shape.Solids()) == 1
    assert shape.Volume() > 0


def test_l_bracket_recipe_has_two_holes_in_each_leg():
    shape = run_recipe(next(r for r in RECIPES if r.name == "l_bracket_holes_in_both_legs").code)
    bbox = shape.BoundingBox()

    assert (round(bbox.xlen, 3), round(bbox.ylen, 3), round(bbox.zlen, 3)) == (80, 40, 40)
    assert sorted(find_hole_diameters(shape)) == pytest.approx([6, 6, 6, 6])


def test_recipe_without_metadata_is_rejected(tmp_path):
    path = tmp_path / "broken.py"
    path.write_text('"""\ntags: a\n"""\nimport cadquery as cq\n')

    with pytest.raises(ValueError, match="missing"):
        parse_recipe(path)


# --- Retrieval ---


def test_query_includes_parameters_and_features():
    query = spec_query(BRACKET)

    assert "leg1 length" in query
    assert "2 x through hole leg1" in query


def test_tfidf_ranks_the_matching_recipe_first():
    assert find_recipes(WHEEL_SPACER, 1, embedder=None)[0][0].name == "round_flange_bolt_circle"
    assert find_recipes(BRACKET, 1, embedder=None)[0][0].name == "l_bracket_holes_in_both_legs"


def test_returns_k_recipes_sorted_by_similarity():
    ranked = find_recipes(WHEEL_SPACER, 3, embedder=None)

    assert len(ranked) == 3
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)


def test_zero_recipes_disables_retrieval():
    assert find_recipes(WHEEL_SPACER, 0, embedder=None) == []


class FakeEmbedder:
    """Embeds a text as the counts of a few keywords, and counts how many documents it had to embed."""

    name = "fake-model"
    keywords = ("disc", "round", "bracket", "leg", "plate")

    def __init__(self):
        self.embedded_documents = 0

    def _vector(self, text: str) -> list[float]:
        return [float(text.lower().count(keyword)) for keyword in self.keywords]

    def documents(self, texts):
        self.embedded_documents += len(texts)
        return [self._vector(text) for text in texts]

    def query(self, text):
        return self._vector(text)


def test_embeddings_rank_and_are_cached(tmp_path):
    embedder = FakeEmbedder()

    first = find_recipes(BRACKET, 1, embedder=embedder, cache_dir=tmp_path)
    find_recipes(WHEEL_SPACER, 1, embedder=embedder, cache_dir=tmp_path)

    assert first[0][0].name == "l_bracket_holes_in_both_legs"
    assert embedder.embedded_documents == len(RECIPES)  # the second call reused the cache
    assert (tmp_path / "recipe_embeddings_fake-model.json").exists()


def test_falls_back_to_tfidf_when_embedding_model_is_unavailable(tmp_path, caplog):
    class Unavailable(FakeEmbedder):
        def documents(self, texts):
            raise ConnectionError("Ollama is not running")

    ranked = find_recipes(BRACKET, 1, embedder=Unavailable(), cache_dir=tmp_path)

    assert ranked[0][0].name == "l_bracket_holes_in_both_legs"
    assert "using TF-IDF" in caplog.text


def test_recipes_dir_is_the_project_folder():
    assert RECIPES_DIR.name == "recipes"
    assert RECIPES_DIR.is_dir()
