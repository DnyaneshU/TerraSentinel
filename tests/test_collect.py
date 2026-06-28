from pathlib import Path

from terrasentinel.collect import find_tf_files, matches_changed, read_files


def test_matches_changed():
    assert matches_changed("/modules/s3/main.tf", ["modules/s3/main.tf"])
    assert matches_changed("main.tf", ["dir/main.tf"])  # basename match
    assert matches_changed("a/b/c.tf", ["c.tf"])
    assert not matches_changed("a.tf", ["b.tf"])


def test_find_tf_files(tmp_path: Path):
    (tmp_path / "main.tf").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "vars.tf").write_text("y")
    (tmp_path / "readme.md").write_text("z")
    # .terraform dirs are ignored
    (tmp_path / ".terraform").mkdir()
    (tmp_path / ".terraform" / "junk.tf").write_text("ignore me")

    found = find_tf_files(tmp_path)
    names = sorted(f.name for f in found)
    assert names == ["main.tf", "vars.tf"]


def test_read_files_truncates(tmp_path: Path):
    big = tmp_path / "big.tf"
    big.write_text("a" * 50_000)
    contents = read_files([big])
    key = next(iter(contents))
    assert "truncated by TerraSentinel" in contents[key]
