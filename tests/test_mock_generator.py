import json
import os
import re
import tempfile

from data.mock_generator import (
    REGULATION_SYSTEMS,
    SYNTHETIC_DATA_NOTICE,
    TYPICAL_INGREDIENTS,
    MockDataGenerator,
)

# Real regulatory instruments and registries. A generated fixture that carries
# one of these next to a limit-shaped number is the hybrid this file exists to
# prevent, so the identifiers are named here rather than inline so a failure
# points at the table that has to change.
_REAL_INSTRUMENTS = re.compile(
    r"GB\s*/?\s*T|QB\s*/?\s*T|NMPA|HFJB|FDA|EU\s*1223|ISO\s*\d|GB\s*4806|"
    r"化妆品安全技术规范|化妆品监督管理条例|化妆品标签管理办法|化妆品功效宣称评价规范"
)
_REAL_CAS = re.compile(r"\b(?!000-00-0)\d{2,7}-\d{2}-\d\b")


def test_generates_regulation_documents():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_regulations(count=5)
        output_dir = os.path.join(tmpdir, "regulations")
        assert os.path.isdir(output_dir)
        files = os.listdir(output_dir)
        assert len(files) == 5
        assert any(f.endswith(".txt") for f in files)


def test_generates_ingredient_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_ingredients(count=10)
        filepath = os.path.join(tmpdir, "ingredients.jsonl")
        assert os.path.isfile(filepath)
        with open(filepath) as f:
            lines = f.readlines()
        assert len(lines) == 10
        first = json.loads(lines[0])
        assert "ingredient_id" in first
        assert "act_code" in first
        assert "safety_info" in first
        assert "role_mask" in first


def test_generates_formula_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_formulas(count=5)
        filepath = os.path.join(tmpdir, "formulas.jsonl")
        assert os.path.isfile(filepath)
        with open(filepath) as f:
            lines = f.readlines()
        assert len(lines) == 5
        first = json.loads(lines[0])
        assert "formula_id" in first
        assert "category" in first
        assert "ingredients" in first


def test_generates_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_all(count_per_type=3)
        metadata_path = os.path.join(tmpdir, "metadata.jsonl")
        assert os.path.isfile(metadata_path)
        with open(metadata_path) as f:
            lines = f.readlines()
        assert len(lines) >= 9
        first = json.loads(lines[0])
        assert "doc_id" in first
        assert "doc_type" in first
        assert "role_mask" in first
        assert "dept_mask" in first
        assert "doc_version_epoch" in first


# --- disclosure contract -------------------------------------------------
#
# The structural tests above pass on a fixture that is indistinguishable from a
# real standard. These ones pin the property that makes the fixture safe to
# ship and to regenerate: it cannot be read as a regulatory conclusion.


def test_source_tables_use_no_real_instrument_or_registry():
    for system in REGULATION_SYSTEMS:
        assert not _REAL_INSTRUMENTS.search(system["code"]), system
        assert not _REAL_INSTRUMENTS.search(system["name"]), system
    for ingredient in TYPICAL_INGREDIENTS:
        assert ingredient["code"].startswith("DEMO-"), ingredient
        assert not _REAL_CAS.search(ingredient["cas"]), ingredient


def test_generated_documents_open_with_the_synthetic_notice():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_regulations(count=5)
        output_dir = os.path.join(tmpdir, "regulations")
        for name in os.listdir(output_dir):
            text = open(os.path.join(output_dir, name), encoding="utf-8").read()
            assert text.startswith(SYNTHETIC_DATA_NOTICE), name


def test_generated_documents_name_no_real_instrument_or_registry():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        gen.generate_all(count_per_type=5)
        for name in os.listdir(os.path.join(tmpdir, "regulations")):
            text = open(os.path.join(tmpdir, "regulations", name), encoding="utf-8").read()
            assert not _REAL_INSTRUMENTS.search(text), name
            assert not _REAL_CAS.search(text), name


def test_every_generated_record_declares_its_class_and_denies_regulatory_claims():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        result = gen.generate_all(count_per_type=5)
        assert result["carries_real_regulatory_claims"] is False
        assert result["data_class"] == "SYNTHETIC"
        for stream in ("metadata.jsonl", "ingredients.jsonl", "formulas.jsonl"):
            with open(os.path.join(tmpdir, stream), encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line)
                    assert record["data_class"] == "SYNTHETIC", stream
                    assert record["carries_real_regulatory_claims"] is False, stream


def test_generator_emits_a_provenance_sidecar_next_to_its_output():
    with tempfile.TemporaryDirectory() as tmpdir:
        gen = MockDataGenerator(output_dir=tmpdir)
        result = gen.generate_all(count_per_type=3)
        sidecar = result["provenance_path"]
        assert os.path.isfile(sidecar)
        text = open(sidecar, encoding="utf-8").read()
        assert "虚构占位值" in text
        assert SYNTHETIC_DATA_NOTICE in text
