from pathlib import Path
import sys
import pytest
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import File, Files
from mkdocs.structure.pages import Page

from mkdocs_autoref_plugin.autoref_plugin import CustomAutoRefPlugin

from mkdocs_autoref_plugin.autoref_plugin import default_reference_sources


class Foo:
    pass


this_module = Path(__file__).parent.name + "." + Path(__file__).stem
foo_full_ref = this_module + "." + Foo.__qualname__


@pytest.fixture(autouse=True)
def add_test_module_to_references():
    default_reference_sources.append(this_module)
    yield
    default_reference_sources.remove(this_module)


@pytest.mark.xfail(
    reason="TODO: these tests depend on lightning and pytorch being installed."
)
@pytest.mark.parametrize(
    ("input", "expected"),
    [
        # Headers aren't changed:
        (_header := "## Some header with a ref `Foo`", _header),
        (
            f"a backtick ref: `{Foo.__name__}`",
            f"a backtick ref: [`{Foo.__name__}`][{foo_full_ref}]",
        ),
        pytest.param(
            "`torch.Tensor`",
            "[`torch.Tensor`][torch.Tensor]",
            marks=pytest.mark.xfail(
                "torch" not in sys.modules,
                reason="Need torch to be installed for this to pass.",
            ),
        ),
        (
            # when a ref is already formatted the right way, it doesn't change.
            "a proper full ref: " + (_foo_ref := f"[{Foo.__name__}][{foo_full_ref}]"),
            # Keep the ref as-is.
            f"a proper full ref: {_foo_ref}",
        ),
        # Unknown module or reference: keep it as-is.
        ("`foo.bar`", "`foo.bar`"),
        (
            "`jax.Array`",
            # not sure if this will make a proper link in mkdocs though.
            "[`jax.Array`][jax.Array]",
        ),
        ("`Trainer`", "[`Trainer`][lightning.pytorch.trainer.trainer.Trainer]"),
        # since `Trainer` is in the `known_things` list, we add the proper ref.
        ("`.devcontainer/devcontainer.json`", "`.devcontainer/devcontainer.json`"),
    ],
)
def test_autoref_plugin(input: str, expected: str):
    config: MkDocsConfig = MkDocsConfig("mkdocs.yaml")  # type: ignore (weird!)
    plugin = CustomAutoRefPlugin()
    result = plugin.on_page_markdown(
        input,
        page=Page(
            title="Test",
            file=File(
                "test.md",
                src_dir="bob",
                dest_dir="bobo",
                use_directory_urls=False,
            ),
            config=config,
        ),
        config=config,
        files=Files([]),
    )
    assert result == expected


def test_ref_using_additional_python_references():
    mkdocs_config: MkDocsConfig = MkDocsConfig("mkdocs.yaml")  # type: ignore (weird!)

    plugin = CustomAutoRefPlugin()

    page = Page(
        title="Test",
        file=File(
            "test.md",
            src_dir="bob",
            dest_dir="bobo",
            use_directory_urls=False,
        ),
        config=mkdocs_config,
    )
    page.meta = {"additional_python_references": [this_module]}

    result = plugin.on_page_markdown(
        f"`{Foo.__name__}`",
        page=page,
        config=mkdocs_config,
        files=Files([]),
    )
    assert result == f"[`Foo`][{foo_full_ref}]"
