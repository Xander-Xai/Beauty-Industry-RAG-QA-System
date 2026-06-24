import json
import os
import tempfile

from data.mock_generator import MockDataGenerator


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
        assert "inci_name" in first
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
