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


def test_external_import_does_not_link_to_a_local_entity_with_the_same_name(tmp_path):
    """Regression for #66: ``from sqlalchemy import delete`` is not ``Service.delete``."""
    paths = _write(tmp_path, {
        "app/services/articles.py": (
            "from sqlalchemy import delete\n\n"
            "class ArticleService:\n"
            "    def delete(self, slug):\n"
            "        return slug\n"
        ),
        "app/repositories/articles.py": (
            "from sqlalchemy import delete\n\n"
            "class ArticleRepository:\n"
            "    def remove(self, session):\n"
            "        return session.execute(delete('articles'))\n"
        ),
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    assert not [edge for edge in edges if edge[1].endswith("ArticleService.delete")]


def test_missing_name_does_not_link_to_an_unrelated_module(tmp_path):
    """Regression for #66: a name absent from the imported module stays unresolved."""
    paths = _write(tmp_path, {
        "app/routes/handlers.py": "def create_article(request):\n    return request\n",
        "app/repositories/article_repo.py": "def find_article(slug):\n    return slug\n",
        "app/services/creating.py": (
            "from app.repositories.article_repo import create_article\n\n"
            "def create_article_service(data):\n"
            "    return create_article(data)\n"
        ),
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    assert ("app.services.creating.create_article_service",
            "app.routes.handlers.create_article", "import") not in edges


def test_package_reexport_still_resolves_inside_the_package(tmp_path):
    paths = _write(tmp_path, {
        "app/models/__init__.py": "from .user import User\n",
        "app/models/user.py": "class User:\n    pass\n",
        "app/other/user.py": "class User:\n    pass\n",
        "app/services/users.py": (
            "from app.models import User\n\n"
            "def make():\n"
            "    return User()\n"
        ),
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    assert ("app.services.users.make", "app.models.user.User", "import") in edges
    assert ("app.services.users.make", "app.other.user.User", "import") not in edges


def test_superclass_resolves_through_imports_not_by_bare_name(tmp_path):
    paths = _write(tmp_path, {
        "app/base.py": "class Base:\n    pass\n",
        "app/unrelated.py": "class Model:\n    pass\n",
        "app/models.py": (
            "from flask_sqlalchemy import Model\n"
            "from app.base import Base\n\n"
            "class Local:\n    pass\n\n"
            "class Article(Model):\n    pass\n\n"
            "class Comment(Base):\n    pass\n\n"
            "class Reply(Local):\n    pass\n"
        ),
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    assert ("app.models.Article", "app.unrelated.Model", "extends") not in edges
    assert ("app.models.Comment", "app.base.Base", "extends") in edges
    assert ("app.models.Reply", "app.models.Local", "extends") in edges


def test_reexport_shim_module_resolves_to_the_defining_module(tmp_path):
    """A compatibility module that re-exports a class links to its definition."""
    paths = _write(tmp_path, {
        "pkg/algorithms/architecture.py": "class Architecture:\n    pass\n",
        "pkg/models/architecture.py": (
            "from pkg.algorithms.architecture import Architecture\n"
        ),
        "pkg/other/architecture.py": "class Architecture:\n    pass\n",
        "pkg/serialization.py": (
            "from pkg.models.architecture import Architecture\n\n"
            "def load():\n"
            "    return Architecture()\n"
        ),
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    assert ("pkg.serialization.load", "pkg.algorithms.architecture.Architecture",
            "import") in edges
    assert ("pkg.serialization.load", "pkg.other.architecture.Architecture",
            "import") not in edges


def test_src_layout_imports_resolve_without_the_src_prefix(tmp_path):
    paths = _write(tmp_path, {
        "src/conduit/repositories/user_repository.py": "class UserRepository:\n    pass\n",
        "src/conduit/routes/articles.py": (
            "from conduit.repositories.user_repository import UserRepository\n\n"
            "def show():\n"
            "    return UserRepository()\n"
        ),
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    assert ("src.conduit.routes.articles.show",
            "src.conduit.repositories.user_repository.UserRepository", "import") in edges


def test_import_of_a_module_level_variable_links_to_its_module(tmp_path):
    """Regression for #67: ``from repo.database import db`` is a dependency."""
    paths = _write(tmp_path, {
        "app/repositories/database.py": "db = object()\n\ndef init_db():\n    return db\n",
        "app/repositories/settings.py": "TIMEOUT: int = 5\n",
        "app/models/user.py": (
            "from app.repositories.database import db\n"
            "from app.repositories.settings import TIMEOUT\n\n"
            "class User:\n"
            "    def save(self):\n"
            "        return db, TIMEOUT\n"
        ),
    })

    edges = PythonParser().parse(paths, tmp_path).to_edge_tuples()

    # database.py declares a function, which stands in for the module;
    # settings.py declares nothing, so it is its own module entity.
    assert ("app.models.user.User.save", "app.repositories.database.init_db",
            "import") in edges
    assert ("app.models.user.User.save", "app.repositories.settings", "import") in edges


def test_module_variables_survive_the_incremental_cache(tmp_path):
    from arcade_agent.incremental import _facts_from_json, _facts_to_json
    from arcade_agent.parsers.python import extract_file

    (tmp_path / "database.py").write_text("db = object()\n\ndef init_db():\n    return db\n")
    facts = extract_file(tmp_path / "database.py", tmp_path)

    assert facts is not None
    assert _facts_from_json(_facts_to_json(facts)).module_vars == ["db"]
