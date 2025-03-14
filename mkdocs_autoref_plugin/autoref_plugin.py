"""A plugin for the mkdocs documentation engine to provide better support for IDE-friendly links.

IDEA: Tweak the AutoRefsPlugin so that text in backticks like `this` (more IDE-friendly) are
considered refs when possible.

TODO: Move to a separate package or somewhere else. Users don't care about this.
"""

import functools
import importlib
import inspect
import re
import types

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import BasePlugin, get_plugin_logger
from mkdocs.structure.files import Files
from mkdocs.structure.pages import Page


# Same as in the mkdocs_autorefs plugin.
logger = get_plugin_logger(__name__)

default_reference_sources = [
    # lightning.Trainer,
    # lightning.LightningModule,
    # lightning.LightningDataModule,
    # torch.nn.Module,
]
"""These are some "known objects" that can be referenced with backticks anywhere in the docs.

Additionally, if there were modules in here, then any of their public members can also be
referenced.
"""


class CustomAutoRefPlugin(BasePlugin):
    """Small mkdocs plugin that converts backticks to refs when possible."""

    def __init__(self):
        super().__init__()
        self.default_reference_sources: dict[str, type | object] = {}
        for source in default_reference_sources:
            self.default_reference_sources.update(_expand(source))

    def on_page_markdown(
        self, markdown: str, /, *, page: Page, config: MkDocsConfig, files: Files
    ) -> str | None:
        # Find all instances where backticks are used and try to convert them to refs.

        # Examples:
        # - `package.foo.bar` -> [package.foo.bar][] (only if `package.foo.bar` is importable)
        # - `baz` -> [baz][]

        # TODO: The idea here is to also make all the members of a module referentiable with
        # backticks in the same module. The problem is that the "reference" page we create with
        # mkdocstrings only contains a simple `::: project.path.to.module` and doesn't have any
        # text, so we can't just replace the `backticks` with refs, since mkdocstrings hasn't yet
        # processed the module into a page with the reference docs. This seems to be happening
        # in a markdown extension (the `MkdocstringsExtension`).

        # file = page.file.abs_src_path
        # if file and "reference/project" in file:
        #     relative_path = file[file.index("reference/") :].removeprefix("reference/")
        #     module_path = relative_path.replace("/", ".").replace(".md", "")
        #     if module_path.endswith(".index"):
        #         module_path = module_path.removesuffix(".index")
        #     logger.error(
        #         f"file {relative_path} is the reference page for the python module {module_path}"
        #     )
        #     if "algorithms/example" in file:
        #         assert False, markdown
        #     additional_objects = _expand(module_path)
        if referenced_packages := page.meta.get("additional_python_references", []):
            logger.debug(f"Loading extra references: {referenced_packages}")
            additional_objects = _get_referencable_objects_from_doc_page_header(
                referenced_packages
            )
        else:
            additional_objects = {}

        if additional_objects:
            additional_objects = {
                k: obj
                for k, obj in additional_objects.items()
                if (
                    inspect.isfunction(obj)
                    or inspect.isclass(obj)
                    or inspect.ismodule(obj)
                    or inspect.ismethod(obj)
                )
                # and (hasattr(obj, "__name__") or hasattr(obj, "__qualname__"))
            }

        known_objects_for_this_module = (
            self.default_reference_sources | additional_objects
        )

        known_object_names = list(known_objects_for_this_module.keys())
        new_markdown = []
        # TODO: This changes things inside code blocks, which is not desired!
        in_code_block = False

        for line_index, line in enumerate(markdown.splitlines(keepends=True)):
            # Can't convert `this` to `[this][]` in headers, otherwise they break.
            if line.lstrip().startswith("#"):
                new_markdown.append(line)
                continue
            if "```" in line:
                in_code_block = not in_code_block
            if in_code_block:
                new_markdown.append(line)
                continue

            matches = re.findall(r"`([^`]+)`", line)
            for match in matches:
                thing_name = match
                if any(char in thing_name for char in ["/", " ", "-"]):
                    continue
                if thing_name in known_object_names:
                    # References like `JaxTrainer` (which are in a module that we're aware of).
                    thing = known_objects_for_this_module[thing_name]
                else:
                    thing = _try_import_thing(thing_name)

                if thing is None:
                    logger.debug(f"Unable to import {thing_name}, leaving it as-is.")
                    continue

                new_ref = f"[`{thing_name}`][{_full_path(thing)}]"
                logger.debug(
                    f"Replacing `{thing_name}` with {new_ref} in {page.file.abs_src_path}:{line_index}"
                )
                line = line.replace(f"`{thing_name}`", new_ref)

            new_markdown.append(line)

        return "".join(new_markdown)


def _expand(obj: types.ModuleType | object) -> dict[str, object]:
    if not inspect.ismodule(obj):
        # The ref is something else (a class, function, etc.)
        if hasattr(obj, "__qualname__"):
            return {obj.__qualname__: obj}
        if hasattr(obj, "__name__"):
            return {obj.__name__: obj}
        return {}

    # The ref is a package, so we import everything from it.
    # equivalent of `from package import *`
    if hasattr(obj, "__all__"):
        return {name: getattr(obj, name) for name in obj.__all__}
    else:
        objects_in_global_scope = {
            k: v for k, v in vars(obj).items() if not k.startswith("_")
        }
        # Don't consider any external modules that were imported in the global scope.
        source_file = inspect.getsourcefile(obj)
        # too obtuse, but whatever
        return {
            k: v
            for k, v in objects_in_global_scope.items()
            if not (
                (
                    inspect.ismodule(v) and getattr(v, "__file__", None) is None
                )  # built-in module.
                or (inspect.ismodule(v) and inspect.getsourcefile(v) != source_file)
            )
        }


def import_object(target_path: str):
    """Imports the object at the given path."""

    # todo: what is the difference between this here and `hydra.utils.get_object` ?
    assert not target_path.endswith(
        ".py"
    ), "expect a valid python path like 'module.submodule.object'"
    if "." not in target_path:
        return importlib.import_module(target_path)

    parts = target_path.split(".")
    try:
        return importlib.import_module(
            name=f".{parts[-1]}", package=".".join(parts[:-1])
        )
    except (ModuleNotFoundError, AttributeError):
        pass
    exc = None
    for i in range(1, len(parts)):
        module_name = ".".join(parts[:i])
        obj_path = parts[i:]
        try:
            module = importlib.import_module(module_name)
            obj = getattr(module, obj_path[0])
            for part in obj_path[1:]:
                obj = getattr(obj, part)
            return obj
        except (ModuleNotFoundError, AttributeError) as _exc:
            exc = _exc
            continue
    assert exc is not None
    raise ModuleNotFoundError(f"Unable to import the {target_path=}!") from exc


def _get_referencable_objects_from_doc_page_header(doc_page_references: list[str]):
    additional_objects: dict[str, object] = {}
    for package in doc_page_references:
        additional_ref_source = import_object(package)
        # todo: what about collisions?
        additional_objects.update(_expand(additional_ref_source))
    return additional_objects


def _full_path(thing) -> str:
    if inspect.ismodule(thing):
        return thing.__name__
    return thing.__module__ + "." + getattr(thing, "__qualname__", thing.__name__)


@functools.cache
def _try_import_thing(thing: str):
    try:
        return import_object(thing)
    except Exception:
        return None
