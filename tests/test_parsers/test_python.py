"""Tests for the Python parser."""

from arcade_agent.parsers.python import PythonParser


def test_python_parser_entities(python_files, fixtures_dir):
    parser = PythonParser()
    graph = parser.parse(python_files, fixtures_dir)

    # Should find classes and functions from app.py and models.py
    assert graph.num_entities >= 3
    entity_names = {e.name for e in graph.entities.values()}
    assert "User" in entity_names or "UserService" in entity_names


def test_python_parser_classes(python_files, fixtures_dir):
    parser = PythonParser()
    graph = parser.parse(python_files, fixtures_dir)

    # Find Product class from models.py
    product_entities = [e for e in graph.entities.values() if e.name == "Product"]
    if product_entities:
        product = product_entities[0]
        assert product.kind == "class"
        assert product.language == "python"
        assert product.superclass == "BaseModel"


def test_python_parser_properties():
    parser = PythonParser()
    assert parser.language == "python"
    assert ".py" in parser.file_extensions


def test_python_parser_skips_empty_init_modules(tmp_path):
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text('"""package marker"""\n')
    module_path = package_dir / "service.py"
    module_path.write_text("def run():\n    return 1\n")

    parser = PythonParser()
    graph = parser.parse([package_dir / "__init__.py", module_path], tmp_path)

    assert "pkg" not in graph.entities
    assert "pkg.service.run" in graph.entities


def test_python_parser_extracts_methods(python_files, fixtures_dir):
    parser = PythonParser()
    graph = parser.parse(python_files, fixtures_dir)

    assert "app.UserService.add_user" in graph.entities
    assert graph.entities["app.UserService.add_user"].kind == "method"
    assert graph.entities["app.UserService.add_user"].properties["owner"] == "app.UserService"


def test_python_parser_keeps_decorator_edges(tmp_path):
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()

    registry_path = package_dir / "registry.py"
    registry_path.write_text(
        "def tool(fn):\n"
        "    return fn\n"
    )
    module_path = package_dir / "service.py"
    module_path.write_text(
        "from pkg.registry import tool\n\n"
        "@tool\n"
        "def run():\n"
        "    return 1\n"
    )

    parser = PythonParser()
    graph = parser.parse([registry_path, module_path], tmp_path)

    assert "pkg.service.run" in graph.entities
    assert ("pkg.service.run", "pkg.registry.tool", "import") in graph.to_edge_tuples()


def test_python_parser_skips_annotation_only_import_edges(tmp_path):
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()

    models_path = package_dir / "models.py"
    models_path.write_text("class User:\n    pass\n")
    service_path = package_dir / "service.py"
    service_path.write_text(
        "from pkg.models import User\n\n"
        "def annotation_only(value: User) -> User:\n"
        "    result: User = value\n"
        "    return result\n\n"
        "def runtime_use():\n"
        "    return User()\n"
    )

    graph = PythonParser().parse([models_path, service_path], tmp_path)
    edges = graph.to_edge_tuples()

    assert ("pkg.service.annotation_only", "pkg.models.User", "import") not in edges
    assert ("pkg.service.runtime_use", "pkg.models.User", "import") in edges


def _write(root, files):
    paths = []
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        paths.append(path)
    return paths


def test_python_parser_resolves_relative_imports(tmp_path):
    paths = _write(tmp_path, {
        "app/__init__.py": "",
        "app/store/__init__.py": "",
        "app/store/repo.py": "class Repo:\n    pass\n",
        "app/api/__init__.py": "",
        "app/api/sibling.py": "class Helper:\n    pass\n",
        "app/api/orders.py": (
            "from ..store.repo import Repo\n"
            "from .sibling import Helper\n\n"
            "def handler():\n"
            "    return Repo(), Helper()\n"
        ),
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    assert ("app.api.orders.handler", "app.store.repo.Repo", "import") in edges
    assert ("app.api.orders.handler", "app.api.sibling.Helper", "import") in edges


def test_python_parser_relative_import_from_package_init(tmp_path):
    paths = _write(tmp_path, {
        "pkg/__init__.py": "from .core import Core\n\ndef make():\n    return Core()\n",
        "pkg/core.py": "class Core:\n    pass\n",
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    assert ("pkg.make", "pkg.core.Core", "import") in edges


def test_python_parser_resolves_aliased_imports(tmp_path):
    paths = _write(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/models.py": "class User:\n    pass\n",
        "pkg/service.py": (
            "from pkg.models import User as Account\n\n"
            "def make():\n"
            "    return Account()\n"
        ),
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    assert ("pkg.service.make", "pkg.models.User", "import") in edges


def test_python_parser_drops_imports_above_the_root(tmp_path):
    paths = _write(tmp_path, {
        "mod.py": "from ...outside import Thing\n\ndef f():\n    return Thing()\n",
    })

    graph = PythonParser().parse(paths, tmp_path)

    assert graph.to_edge_tuples() == []
